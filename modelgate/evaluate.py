"""Evaluate the challenger and compute the gate verdict.

Two deterministic steps:

1. metrics: score data/holdout.csv with models/challenger/model.pkl and
   write reports/metrics.json.
2. gate: apply config/thresholds.yaml to reports/metrics.json,
   models/champion.json, reports/scan.json and reports/sci.json, and write
   reports/gate_report.json plus reports/champion_candidate.json (what
   models/champion.json becomes if this candidate is merged).

The gate is a pure function of those input files. scan.json and sci.json come
from other jobs; sci.json is a measurement, so it differs between runs, and the
carbon rule can only be reproduced from the same sci.json.
"""

from __future__ import annotations

import logging
import pickle
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np
from sklearn.metrics import accuracy_score, brier_score_loss, f1_score, roc_auc_score

from modelgate import data
from modelgate.common import (
    Paths,
    load_yaml,
    read_json,
    read_json_if_exists,
    round_metric,
    run_context,
    sha256_file,
    write_json,
)

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
DECISION_THRESHOLD = 0.5
CALIBRATION_BINS = 10
BOOTSTRAP_VERSION = "0.1.0"

RULE_IDS: tuple[str, ...] = (
    "no_regression",
    "min_absolute_auc",
    "slice_gap",
    "calibration",
    "scan_clean",
    "carbon_efficiency",
)
Verdict = Literal["GO", "NEEDS_REVIEW", "NO_GO"]
Status = Literal["pass", "fail", "bootstrap", "not_applicable", "skipped"]
BumpKind = Literal["initial", "patch", "minor", "major"]

_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


# --------------------------------------------------------------------------- metrics


def expected_calibration_error(y: np.ndarray, p: np.ndarray, bins: int = CALIBRATION_BINS) -> float:
    """Equal-width ECE: sum over bins of (bin share) * |observed rate - mean prediction|."""
    idx = np.minimum((p * bins).astype(int), bins - 1)
    ece = 0.0
    for b in range(bins):
        mask = idx == b
        if mask.any():
            ece += mask.mean() * abs(y[mask].mean() - p[mask].mean())
    return float(ece)


def compute_metrics(y: np.ndarray, p: np.ndarray, segment: np.ndarray) -> dict[str, Any]:
    """Overall and per-segment metrics, rounded for storage and gating."""
    pred = (p >= DECISION_THRESHOLD).astype(int)
    overall = {
        "auc": round_metric(roc_auc_score(y, p)),
        "f1": round_metric(f1_score(y, pred, zero_division=0)),
        "accuracy": round_metric(accuracy_score(y, pred)),
        "brier": round_metric(brier_score_loss(y, p)),
        "ece": round_metric(expected_calibration_error(y, p)),
        "positive_rate": round_metric(y.mean()),
        "n": len(y),
    }
    segments: dict[str, dict[str, Any]] = {}
    for name in sorted(set(segment.tolist())):
        mask = segment == name
        ys, ps = y[mask], p[mask]
        segments[name] = {
            "n": int(mask.sum()),
            "positive_rate": round_metric(ys.mean()),
            # AUC is undefined when a segment holds only one class.
            "auc": round_metric(roc_auc_score(ys, ps)) if len(np.unique(ys)) == 2 else None,
            "brier": round_metric(brier_score_loss(ys, ps)),
        }
    aucs = [s["auc"] for s in segments.values() if s["auc"] is not None]
    # Gap from the rounded values, so it equals the difference a reader sees.
    gap = round_metric(max(aucs) - min(aucs)) if len(aucs) >= 2 else None
    return {"overall": overall, "segments": segments, "segment_auc_gap": gap}


def evaluate_challenger(paths: Paths) -> dict[str, Any]:
    meta = read_json(paths.challenger / "meta.json")
    # `python -m modelgate reports` runs scan.py on this file before this step.
    with (paths.challenger / "model.pkl").open("rb") as fh:
        model = pickle.load(fh)
    holdout_csv = paths.data / "holdout.csv"
    ds = data.read_csv(holdout_csv)
    features = meta["config"]["features"]
    p = model.predict_proba(ds.matrix(features))[:, 1]
    report = {
        "schema_version": SCHEMA_VERSION,
        "config": meta["config"],
        "holdout": {"file": holdout_csv.name, "sha256": sha256_file(holdout_csv)},
        "decision_threshold": DECISION_THRESHOLD,
        **compute_metrics(ds.label, p, ds.segment),
    }
    write_json(paths.reports / "metrics.json", report)
    o = report["overall"]
    logger.info(
        "holdout AUC %.4f, F1 %.4f, accuracy %.4f, Brier %.4f, segment AUC gap %s",
        o["auc"],
        o["f1"],
        o["accuracy"],
        o["brier"],
        report["segment_auc_gap"],
    )
    return report


# ------------------------------------------------------------------------ thresholds


@dataclass(frozen=True)
class Thresholds:
    rule_order: tuple[str, ...]
    params: dict[str, dict[str, Any]]
    needs_review_rules: frozenset[str]

    @classmethod
    def from_yaml(cls, path: Path) -> Thresholds:
        raw = load_yaml(path)
        if raw.get("primary_metric") != "auc":
            raise ValueError("only primary_metric: auc is supported")
        order = tuple(str(r["id"]) for r in raw["rules"])
        if sorted(order) != sorted(RULE_IDS):
            raise ValueError(f"thresholds must define exactly {RULE_IDS}, got {order}")
        params = {str(r["id"]): {k: v for k, v in r.items() if k != "id"} for r in raw["rules"]}
        review = frozenset(raw["verdict"]["needs_review_rules"])
        if not review <= set(RULE_IDS):
            unknown = sorted(review - set(RULE_IDS))
            raise ValueError(f"unknown rule in needs_review_rules: {unknown}")
        return cls(rule_order=order, params=params, needs_review_rules=review)


# ----------------------------------------------------------------------------- rules


@dataclass(frozen=True)
class RuleResult:
    id: str
    status: Status
    value: float | None
    threshold: float | None
    note: str

    @property
    def passed(self) -> bool:
        return self.status != "fail"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "passed": self.passed,
            "value": self.value,
            "threshold": self.threshold,
            "note": self.note,
        }


def _bootstrap(rule_id: str) -> RuleResult:
    return RuleResult(rule_id, "bootstrap", None, None, "no champion yet; rule needs one")


def _auc(metrics: dict[str, Any]) -> float:
    return float(metrics["overall"]["auc"])


def rule_no_regression(ch: dict, champ: dict | None, p: dict) -> RuleResult:
    if champ is None:
        return _bootstrap("no_regression")
    champ_auc = _auc(champ["metrics"])
    threshold = round_metric(champ_auc - float(p["tolerance"]))
    value = _auc(ch)
    status: Status = "pass" if value >= threshold else "fail"
    note = (
        f"AUC {value:.4f} vs champion {champ_auc:.4f} "
        f"(delta {value - champ_auc:+.4f}); must be >= {threshold:.4f}"
    )
    return RuleResult("no_regression", status, value, threshold, note)


def rule_min_absolute_auc(ch: dict, p: dict) -> RuleResult:
    value, threshold = _auc(ch), float(p["min"])
    status: Status = "pass" if value >= threshold else "fail"
    return RuleResult(
        "min_absolute_auc", status, value, threshold, f"AUC {value:.4f}; must be >= {threshold:.2f}"
    )


def rule_slice_gap(ch: dict, p: dict) -> RuleResult:
    threshold = float(p["max_gap"])
    gap = ch["segment_auc_gap"]
    if gap is None:
        return RuleResult(
            "slice_gap", "fail", None, threshold, "fewer than two segments have a defined AUC"
        )
    aucs = {k: v["auc"] for k, v in ch["segments"].items() if v["auc"] is not None}
    lo, hi = min(aucs, key=aucs.__getitem__), max(aucs, key=aucs.__getitem__)
    status: Status = "pass" if gap <= threshold else "fail"
    note = (
        f"segment AUC gap {gap:.4f} (highest {hi} {aucs[hi]:.4f}, "
        f"lowest {lo} {aucs[lo]:.4f}); must be <= {threshold:.2f}"
    )
    return RuleResult("slice_gap", status, gap, threshold, note)


def rule_calibration(ch: dict, champ: dict | None, p: dict) -> RuleResult:
    if champ is None:
        return _bootstrap("calibration")
    champ_brier = float(champ["metrics"]["overall"]["brier"])
    threshold = round_metric(champ_brier + float(p["max_increase"]))
    value = float(ch["overall"]["brier"])
    status: Status = "pass" if value <= threshold else "fail"
    note = (
        f"Brier {value:.4f} vs champion {champ_brier:.4f} "
        f"(delta {value - champ_brier:+.4f}); must be <= {threshold:.4f}"
    )
    return RuleResult("calibration", status, value, threshold, note)


def rule_scan_clean(scan: dict | None, p: dict) -> RuleResult:
    blocking = {str(s).upper() for s in p["blocking_severities"]}
    if scan is None:
        note = "reports/scan.json missing; scan did not run"
        return RuleResult("scan_clean", "fail", None, 0, note)
    problems: list[str] = []
    pscan = scan.get("picklescan") or {}
    if not pscan.get("ran", False):
        problems.append("picklescan did not run")
    # A parse error hides every global after it, so it fails like a finding.
    elif pscan.get("scan_error", True):
        problems.append("picklescan could not parse the whole model file")
    dangerous = int(pscan.get("dangerous_globals", 0))
    if dangerous:
        problems.append(f"picklescan found {dangerous} dangerous global(s)")
    unexpected = list(pscan.get("unexpected_globals") or [])
    if unexpected:
        problems.append(f"globals outside the allowlist: {', '.join(unexpected)}")
    audit = scan.get("pip_audit") or {}
    if not audit.get("ran", False):
        problems.append("pip-audit did not run")
    vulns = audit.get("vulnerabilities") or []
    blocked = [v for v in vulns if str(v.get("severity", "")).upper() in blocking]
    if blocked:
        ids = ", ".join(f"{v['package']} {v['id']} ({v['severity']})" for v in blocked)
        problems.append(f"pip-audit blocking findings: {ids}")
    value = float(dangerous + len(unexpected) + len(blocked))
    if problems:
        return RuleResult("scan_clean", "fail", value, 0, "; ".join(problems))
    other = len(vulns)
    note = "picklescan clean; no HIGH/CRITICAL pip-audit findings"
    if other:
        note += f" ({other} lower-severity or unrated finding(s) listed in scan.json)"
    return RuleResult("scan_clean", "pass", value, 0, note)


def rule_carbon_efficiency(ch: dict, champ: dict | None, sci: dict | None, p: dict) -> RuleResult:
    if champ is None:
        return _bootstrap("carbon_efficiency")
    threshold = float(p["max_gco2e_per_0_01_auc"])
    gain = round_metric(_auc(ch) - _auc(champ["metrics"]))
    if gain <= 0:
        return RuleResult(
            "carbon_efficiency",
            "not_applicable",
            None,
            threshold,
            f"AUC change {gain:+.4f}; rule applies only when the challenger improves",
        )
    if sci is None:
        note = "reports/sci.json missing; not measured"
        return RuleResult("carbon_efficiency", "skipped", None, threshold, note)
    grams = float(sci["sci_gco2e_per_candidate"])
    value = float(f"{grams / (gain / 0.01):.2g}")
    status: Status = "pass" if value <= threshold else "fail"
    note = (
        f"{grams:.2g} gCO2e for +{gain:.4f} AUC = {value:.2g} gCO2e per 0.01 AUC; "
        f"must be <= {threshold:.2g}"
    )
    return RuleResult("carbon_efficiency", status, value, threshold, note)


def check_rules(
    ch: dict[str, Any],
    champ: dict[str, Any] | None,
    scan: dict[str, Any] | None,
    sci: dict[str, Any] | None,
    thresholds: Thresholds,
) -> list[RuleResult]:
    """Evaluate every rule, in the order thresholds.yaml lists them."""
    prm = thresholds.params
    results = {
        "no_regression": rule_no_regression(ch, champ, prm["no_regression"]),
        "min_absolute_auc": rule_min_absolute_auc(ch, prm["min_absolute_auc"]),
        "slice_gap": rule_slice_gap(ch, prm["slice_gap"]),
        "calibration": rule_calibration(ch, champ, prm["calibration"]),
        "scan_clean": rule_scan_clean(scan, prm["scan_clean"]),
        "carbon_efficiency": rule_carbon_efficiency(ch, champ, sci, prm["carbon_efficiency"]),
    }
    return [results[rule_id] for rule_id in thresholds.rule_order]


def decide_verdict(rules: list[RuleResult], needs_review_rules: frozenset[str]) -> Verdict:
    failed = {r.id for r in rules if not r.passed}
    if not failed:
        return "GO"
    if failed <= needs_review_rules:
        return "NEEDS_REVIEW"
    return "NO_GO"


# ------------------------------------------------------------------------ versioning


def bump_version(version: str, kind: BumpKind) -> str:
    match = _SEMVER.match(version)
    if not match:
        raise ValueError(f"champion version {version!r} is not MAJOR.MINOR.PATCH")
    major, minor, patch = (int(g) for g in match.groups())
    if kind == "major":
        return f"{major + 1}.0.0"
    if kind == "minor":
        return f"{major}.{minor + 1}.0"
    if kind == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise ValueError(f"cannot bump an existing version with kind {kind!r}")


def propose_bump(challenger_config: dict[str, Any], champion: dict[str, Any] | None) -> dict:
    """major: model_family or output_schema changed; minor: feature set changed;
    patch: anything else, including a plain retrain."""
    if champion is None:
        return {
            "kind": "initial",
            "from": None,
            "to": BOOTSTRAP_VERSION,
            "reasons": ["no champion yet; first promoted version"],
        }
    old, new = champion["config"], challenger_config
    reasons: list[str] = []
    kind: BumpKind = "patch"
    for key in ("model_family", "output_schema"):
        if old[key] != new[key]:
            kind = "major"
            reasons.append(f"{key} changed: {old[key]} -> {new[key]}")
    added = [f for f in new["features"] if f not in old["features"]]
    removed = [f for f in old["features"] if f not in new["features"]]
    if added or removed:
        if kind != "major":
            kind = "minor"
        if added:
            reasons.append(f"features added: {', '.join(added)}")
        if removed:
            reasons.append(f"features removed: {', '.join(removed)}")
    elif old["features"] != new["features"]:
        reasons.append("feature order changed")
    for key in sorted(set(old["hyperparams"]) | set(new["hyperparams"])):
        before, after = old["hyperparams"].get(key), new["hyperparams"].get(key)
        if before != after:
            reasons.append(f"hyperparam {key}: {before} -> {after}")
    if old["seed"] != new["seed"]:
        reasons.append(f"seed: {old['seed']} -> {new['seed']}")
    if not reasons:
        reasons.append("retrain with unchanged config")
    return {
        "kind": kind,
        "from": champion["version"],
        "to": bump_version(champion["version"], kind),
        "reasons": reasons,
    }


# ------------------------------------------------------------------------------ gate


def _metric_view(m: dict[str, Any]) -> dict[str, Any]:
    return {k: m[k] for k in ("overall", "segments", "segment_auc_gap")}


def build_gate_report(
    metrics: dict[str, Any],
    champion: dict[str, Any] | None,
    scan: dict[str, Any] | None,
    sci: dict[str, Any] | None,
    thresholds: Thresholds,
    run: dict[str, Any],
) -> dict[str, Any]:
    rules = check_rules(metrics, champion, scan, sci, thresholds)
    verdict = decide_verdict(rules, thresholds.needs_review_rules)
    notes: list[str] = []
    comparison = None
    champion_view = None
    if champion is None:
        notes.append("bootstrap: no models/champion.json; rules that need a champion are skipped")
    else:
        champion_view = {
            k: champion.get(k) for k in ("version", "commit", "pipeline_url", "registered_as")
        }
        champion_view["config"] = champion["config"]
        champion_view["metrics"] = champion["metrics"]
        cm = champion["metrics"]
        comparison = {
            "auc_delta": round_metric(_auc(metrics) - _auc(cm)),
            "brier_delta": round_metric(metrics["overall"]["brier"] - cm["overall"]["brier"]),
            "segment_auc_delta": {
                s: round_metric(v["auc"] - cm["segments"][s]["auc"])
                for s, v in metrics["segments"].items()
                if v["auc"] is not None and cm["segments"].get(s, {}).get("auc") is not None
            },
        }
        champ_holdout = champion.get("holdout_sha256")
        if champ_holdout and champ_holdout != metrics["holdout"]["sha256"]:
            notes.append(
                "holdout data differs from the champion's (config/data.yaml changed?); "
                "metric comparisons are not like-for-like"
            )
    return {
        "schema_version": SCHEMA_VERSION,
        "verdict": verdict,
        "failed_rules": [r.id for r in rules if not r.passed],
        "rules": [r.to_dict() for r in rules],
        "challenger": {
            "config": metrics["config"],
            "metrics": _metric_view(metrics),
            "holdout_sha256": metrics["holdout"]["sha256"],
        },
        "champion": champion_view,
        "comparison": comparison,
        "proposed_version_bump": propose_bump(metrics["config"], champion),
        "inputs": {
            "champion": "present" if champion else "missing",
            "scan": "present" if scan else "missing",
            "sci": "present" if sci else "missing",
        },
        "notes": notes,
        # Lineage only; everything above this key is a function of the input files.
        "run": run,
    }


def champion_candidate(report: dict[str, Any], sci: dict[str, Any] | None) -> dict[str, Any]:
    """The models/champion.json content this candidate would get if merged."""
    return {
        "version": report["proposed_version_bump"]["to"],
        "verdict": report["verdict"],
        "commit": report["run"]["commit"],
        "pipeline_url": report["run"]["pipeline_url"],
        "registered_as": None,
        "config": report["challenger"]["config"],
        "metrics": report["challenger"]["metrics"],
        "holdout_sha256": report["challenger"]["holdout_sha256"],
        "sci": sci,
    }


def _log_rules(report: dict[str, Any]) -> None:
    logger.info("%-18s %-15s %s", "rule", "status", "detail")
    for r in report["rules"]:
        logger.info("%-18s %-15s %s", r["id"], r["status"], r["note"])
    bump = report["proposed_version_bump"]
    logger.info(
        "verdict: %s (proposed version %s, %s bump)", report["verdict"], bump["to"], bump["kind"]
    )


def gate(paths: Paths) -> dict[str, Any]:
    metrics = read_json(paths.reports / "metrics.json")
    champion = read_json_if_exists(paths.champion_file)
    scan = read_json_if_exists(paths.reports / "scan.json")
    sci = read_json_if_exists(paths.reports / "sci.json")
    thresholds = Thresholds.from_yaml(paths.config / "thresholds.yaml")
    report = build_gate_report(metrics, champion, scan, sci, thresholds, run_context())
    write_json(paths.reports / "gate_report.json", report)
    write_json(paths.reports / "champion_candidate.json", champion_candidate(report, sci))
    _log_rules(report)
    return report


def main(paths: Paths) -> dict[str, Any]:
    evaluate_challenger(paths)
    return gate(paths)
