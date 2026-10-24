"""Nightly drift check: score a freshly drawn drift batch with the champion.

The batch for a date is drawn with a seed derived from that date, and its drift grows from
nothing on `drift.start_date` to the full `drift` knobs of config/data.yaml after
`drift.ramp_days`. So a given date always gives the same numbers, and the nightly history
shows the champion degrading as the inputs move.

The champion model is retrained from models/champion.json's own config (training is
deterministic), and the check records whether that reproduced the champion's holdout AUC.
It writes reports/drift.json (tonight) and, when the check ran, reports/drift_history.json
(every checked night so far, carried forward from the previous drift_check job's artifacts in
CI).
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import numpy as np

from modelgate import data
from modelgate.common import (
    Http,
    Paths,
    load_yaml,
    read_json_if_exists,
    round_metric,
    urllib_http,
    write_json,
)
from modelgate.evaluate import compute_metrics
from modelgate.train import TrainConfig, build_model

logger = logging.getLogger(__name__)

PSI_BINS = 10
PSI_EPS = 1e-4


def drift_scale(raw_data_cfg: dict[str, Any], day: date) -> float:
    """Fraction of the drift ramp reached on day, from the raw config/data.yaml mapping.

    It is (day - drift.start_date) / drift.ramp_days in days, clamped to [0, 1]: 0 on or
    before the start date and 1 once the ramp is over.
    """
    drift = raw_data_cfg["drift"]
    start = date.fromisoformat(str(drift["start_date"]))
    days = (day - start).days
    return float(min(1.0, max(0.0, days / float(drift["ramp_days"]))))


def drift_batch(paths: Paths, day: date) -> tuple[data.Dataset, float, dict[str, float]]:
    """Generate the drift batch for day in memory from config/data.yaml.

    The segment mix is interpolated linearly from segment_mix toward drift.segment_mix by the
    day's drift scale, and the income and utilization shifts are drift.income_shift and
    drift.utilization_shift times that scale. The batch has n_drift rows, drawn with a generator
    seeded from SeedSequence([seed, day.toordinal()]), so the same config and date always give
    the same batch. Nothing is written to disk. Returns the batch, the unrounded scale, and the
    segment mix used, keyed by segment name with each share rounded to 4 decimals.
    """
    cfg = data.DataConfig.from_yaml(paths.config / "data.yaml")
    scale = drift_scale(load_yaml(paths.config / "data.yaml"), day)
    mix = tuple(
        b + scale * (d - b) for b, d in zip(cfg.segment_mix, cfg.drift.segment_mix, strict=True)
    )
    rng = np.random.default_rng(np.random.SeedSequence([cfg.seed, day.toordinal()]))
    batch = data.generate(
        cfg.n_drift,
        rng,
        mix,  # type: ignore[arg-type]
        cfg.slice_skew,
        income_shift=cfg.drift.income_shift * scale,
        utilization_shift=cfg.drift.utilization_shift * scale,
    )
    return batch, scale, dict(zip(data.SEGMENTS, (round(m, 4) for m in mix), strict=True))


def psi(expected: np.ndarray, actual: np.ndarray, bins: int = PSI_BINS) -> float:
    """Population stability index of actual against expected, over expected's quantile bins.

    The bins are cut at the expected sample's quantiles, at most bins of them and fewer when
    quantiles repeat; actual values outside the expected range fall into the first or last
    bin. Bin shares are floored at PSI_EPS before the log. When the expected sample has fewer
    than three distinct quantile edges, everything falls into a single bin and the result is 0.0.
    """
    edges = np.unique(np.quantile(expected, np.linspace(0, 1, bins + 1)))
    if len(edges) < 3:  # (nearly) constant feature, such as region_score outside segment C
        edges = np.array([expected.min() - 1, expected.max() + 1])
    inner = edges[1:-1]
    e = np.bincount(np.searchsorted(inner, expected, side="right"), minlength=len(edges) - 1)
    a = np.bincount(np.searchsorted(inner, actual, side="right"), minlength=len(edges) - 1)
    e_share = np.maximum(e / e.sum(), PSI_EPS)
    a_share = np.maximum(a / a.sum(), PSI_EPS)
    return float(np.sum((a_share - e_share) * np.log(a_share / e_share)))


def check(paths: Paths, day: date) -> dict[str, Any]:
    """Refit the champion and score it on the holdout and on day's drift batch.

    Returns only the date and status "no_champion" when models/champion.json is missing.
    Otherwise it runs data.main to write the train, holdout and drift_batch CSVs if
    data/train.csv is missing, refits the champion's model on train.csv from the config stored
    in champion.json, and returns status "checked" with the champion version, the holdout AUC
    and whether it exactly equals the AUC recorded in champion.json ("reproduced"), the drift
    scale and segment mix, the drift batch metrics, the AUC drop from holdout to batch, the PSI
    of each of the champion's features (train against batch) and the largest of them. "alert"
    is True, with one message per exceeded limit in "reasons", when the drop exceeds
    max_auc_drop or the largest PSI exceeds max_psi in the drift section of
    config/thresholds.yaml. Writes no report itself.
    """
    champion = read_json_if_exists(paths.champion_file)
    if champion is None:
        return {"date": day.isoformat(), "status": "no_champion"}
    limits = load_yaml(paths.config / "thresholds.yaml")["drift"]
    if not (paths.data / "train.csv").exists():
        data.main(paths)
    train = data.read_csv(paths.data / "train.csv")
    holdout = data.read_csv(paths.data / "holdout.csv")
    cfg = TrainConfig(**{**champion["config"], "features": tuple(champion["config"]["features"])})
    model = build_model(cfg).fit(train.matrix(cfg.features), train.label)
    holdout_metrics = compute_metrics(
        holdout.label, model.predict_proba(holdout.matrix(cfg.features))[:, 1], holdout.segment
    )
    batch, scale, mix = drift_batch(paths, day)
    drift_metrics = compute_metrics(
        batch.label, model.predict_proba(batch.matrix(cfg.features))[:, 1], batch.segment
    )
    scores = {f: round_metric(psi(train.features[f], batch.features[f])) for f in cfg.features}
    holdout_auc = holdout_metrics["overall"]["auc"]
    drop = round_metric(holdout_auc - drift_metrics["overall"]["auc"])
    max_psi = max(scores.values())
    reasons = []
    if drop > float(limits["max_auc_drop"]):
        reasons.append(f"AUC fell {drop:.4f} on the drift batch (limit {limits['max_auc_drop']})")
    if max_psi > float(limits["max_psi"]):
        worst = max(scores, key=scores.__getitem__)
        reasons.append(f"PSI of {worst} is {max_psi:.4f} (limit {limits['max_psi']})")
    return {
        "date": day.isoformat(),
        "status": "checked",
        "champion_version": champion["version"],
        "reproduced": holdout_auc == champion["metrics"]["overall"]["auc"],
        "holdout_auc": holdout_auc,
        "drift_scale": round(scale, 4),
        "segment_mix": mix,
        "drift": {
            "n": len(batch),
            "auc": drift_metrics["overall"]["auc"],
            "brier": drift_metrics["overall"]["brier"],
            "segment_auc": {k: v["auc"] for k, v in drift_metrics["segments"].items()},
            "segment_auc_gap": drift_metrics["segment_auc_gap"],
        },
        "auc_drop": drop,
        "psi": scores,
        "max_psi": max_psi,
        "alert": bool(reasons),
        "reasons": reasons,
    }


def write_json_list(path: Path, items: list[dict[str, Any]]) -> None:
    """Write items to path as 2-space indented JSON with a trailing newline.

    Keys are sorted within each dict; the list keeps its given order. Creates missing parent
    directories and overwrites any existing file. Line endings follow the platform default.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(items, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def merge_history(history: list[dict[str, Any]], entry: dict[str, Any]) -> list[dict[str, Any]]:
    """Return history with entry added, oldest date first, replacing any entry for its date.

    Only entries with the same date as entry are dropped; the input list is not modified.
    """
    kept = [h for h in history if h.get("date") != entry["date"]]
    return sorted([*kept, entry], key=lambda h: h["date"])


def main(paths: Paths, http: Http = urllib_http, day: date | None = None) -> dict[str, Any]:
    """Run the drift check for day (today in UTC by default) and return its entry.

    Overwrites reports/drift.json with the entry. When the status is not "checked" it stops
    there. Otherwise it merges the entry into reports/drift_history.json, replacing any entry
    for the same date. The history starts from the existing local file if there is one, else
    from reports/drift_history.json in the latest drift_check job artifacts on the default
    branch, fetched over http with CI_JOB_TOKEN (an empty list when there is no token or the
    request does not return 200).
    """
    from modelgate.site import fetch_drift_history

    day = day or datetime.now(UTC).date()
    entry = check(paths, day)
    write_json(paths.reports / "drift.json", entry)
    if entry["status"] != "checked":
        logger.info("drift: %s", entry["status"])
        return entry
    history_path = paths.reports / "drift_history.json"
    if history_path.exists():  # a local run keeps its own history
        previous = json.loads(history_path.read_text(encoding="utf-8"))
    else:  # in CI, carry forward the previous drift_check job's history
        previous = fetch_drift_history(http) or []
    history = merge_history(previous, entry)
    write_json_list(history_path, history)
    logger.info(
        "drift %s: champion %s, AUC %.4f on the batch vs %.4f on the holdout, max PSI %.4f%s",
        entry["date"],
        entry["champion_version"],
        entry["drift"]["auc"],
        entry["holdout_auc"],
        entry["max_psi"],
        f"; ALERT: {'; '.join(entry['reasons'])}" if entry["alert"] else "",
    )
    return entry
