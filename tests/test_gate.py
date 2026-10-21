"""Verdict logic against hand-built metrics. Every expected value is worked out by hand."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from conftest import CLEAN_SCAN, REPO

from modelgate.evaluate import (
    RULE_IDS,
    RuleResult,
    Thresholds,
    build_gate_report,
    check_rules,
    decide_verdict,
)

THRESHOLDS = Thresholds.from_yaml(REPO / "config" / "thresholds.yaml")
CONFIG: dict[str, Any] = {
    "seed": 1,
    "model_family": "logistic_regression",
    "output_schema": "default_probability_v1",
    "features": ["income", "debt_ratio"],
    "hyperparams": {"C": 1.0},
}


def metrics(
    auc: float = 0.85, brier: float = 0.14, seg: tuple[float, float, float] = (0.85, 0.85, 0.85)
) -> dict[str, Any]:
    return {
        "config": CONFIG,
        "holdout": {"sha256": "holdout-hash"},
        "overall": {"auc": auc, "brier": brier},
        "segments": {
            name: {"auc": value, "n": 100, "positive_rate": 0.3, "brier": brier}
            for name, value in zip("ABC", seg, strict=True)
        },
        "segment_auc_gap": round(max(seg) - min(seg), 4),
    }


def champion(auc: float = 0.84, brier: float = 0.14) -> dict[str, Any]:
    m = metrics(auc, brier, (auc, auc, auc))
    return {
        "version": "0.1.0",
        "commit": "abc",
        "pipeline_url": None,
        "registered_as": None,
        "config": CONFIG,
        "metrics": {k: m[k] for k in ("overall", "segments", "segment_auc_gap")},
        "holdout_sha256": "holdout-hash",
    }


def sci(grams: float) -> dict[str, Any]:
    return {"sci_gco2e_per_candidate": grams}


def run(
    ch: dict[str, Any],
    champ: dict[str, Any] | None = None,
    scan: dict[str, Any] | None = CLEAN_SCAN,
    carbon: dict[str, Any] | None = None,
) -> tuple[str, dict[str, RuleResult]]:
    rules = check_rules(ch, champ, scan, carbon, THRESHOLDS)
    return decide_verdict(rules, THRESHOLDS.needs_review_rules), {r.id: r for r in rules}


def failed(rules: dict[str, RuleResult]) -> set[str]:
    return {k for k, r in rules.items() if not r.passed}


def test_all_rules_pass_is_go() -> None:
    verdict, rules = run(metrics(auc=0.87), champion(auc=0.84), carbon=sci(0.45))
    assert verdict == "GO"
    assert failed(rules) == set()
    # gain 0.03 AUC = 3 units of 0.01; 0.45 g / 3 = 0.15 g per 0.01 AUC
    assert rules["carbon_efficiency"].value == pytest.approx(0.15)


def test_no_regression_boundary_is_inclusive() -> None:
    # champion 0.8400 - tolerance 0.005 = 0.8350
    _, rules = run(metrics(auc=0.8350, seg=(0.835,) * 3), champion(auc=0.84))
    assert rules["no_regression"].status == "pass"
    assert rules["no_regression"].threshold == pytest.approx(0.835)
    verdict, rules = run(metrics(auc=0.8349, seg=(0.835,) * 3), champion(auc=0.84))
    assert rules["no_regression"].status == "fail"
    assert verdict == "NO_GO"


def test_min_absolute_auc_fails_below_floor() -> None:
    verdict, rules = run(metrics(auc=0.7999, seg=(0.80,) * 3))
    assert rules["min_absolute_auc"].status == "fail"
    assert verdict == "NO_GO"


def test_slice_gap_boundary_and_review_verdict() -> None:
    _, rules = run(metrics(seg=(0.86, 0.85, 0.81)), champion())
    assert rules["slice_gap"].value == pytest.approx(0.05)
    assert rules["slice_gap"].status == "pass"
    verdict, rules = run(metrics(seg=(0.86, 0.85, 0.8099)), champion())
    assert failed(rules) == {"slice_gap"}
    assert verdict == "NEEDS_REVIEW"


def test_slice_gap_fails_when_no_segment_auc_is_defined() -> None:
    m = metrics()
    m["segment_auc_gap"] = None
    _, rules = run(m)
    assert rules["slice_gap"].status == "fail"


def test_calibration_boundary() -> None:
    # champion Brier 0.1400 + 0.01 = 0.1500
    _, rules = run(metrics(brier=0.1500), champion(brier=0.14))
    assert rules["calibration"].status == "pass"
    verdict, rules = run(metrics(brier=0.1501), champion(brier=0.14))
    assert failed(rules) == {"calibration"}
    assert verdict == "NO_GO"


def test_carbon_inefficient_gain_needs_review() -> None:
    # gain 0.0010 AUC = 0.1 units; 0.30 g / 0.1 = 3.0 g per 0.01 AUC > 2.0
    verdict, rules = run(metrics(auc=0.841, seg=(0.841,) * 3), champion(auc=0.84), carbon=sci(0.30))
    assert rules["carbon_efficiency"].value == pytest.approx(3.0)
    assert failed(rules) == {"carbon_efficiency"}
    assert verdict == "NEEDS_REVIEW"


def test_carbon_boundary_is_inclusive() -> None:
    # gain 0.01 AUC = 1 unit; 2.0 g / 1 = 2.0 g, exactly the threshold
    _, rules = run(metrics(auc=0.85), champion(auc=0.84), carbon=sci(2.0))
    assert rules["carbon_efficiency"].status == "pass"


def test_carbon_not_applicable_without_improvement() -> None:
    _, rules = run(metrics(auc=0.84, seg=(0.84,) * 3), champion(auc=0.84), carbon=sci(99.0))
    assert rules["carbon_efficiency"].status == "not_applicable"
    assert rules["carbon_efficiency"].passed


def test_carbon_skipped_when_sci_missing() -> None:
    _, rules = run(metrics(auc=0.87), champion(auc=0.84), carbon=None)
    assert rules["carbon_efficiency"].status == "skipped"
    assert rules["carbon_efficiency"].passed


def test_slice_and_carbon_together_still_need_review() -> None:
    ch = metrics(auc=0.841, seg=(0.90, 0.85, 0.80))
    verdict, rules = run(ch, champion(auc=0.84), carbon=sci(0.30))
    assert failed(rules) == {"slice_gap", "carbon_efficiency"}
    assert verdict == "NEEDS_REVIEW"


def test_review_rule_plus_blocking_rule_is_no_go() -> None:
    ch = metrics(auc=0.80, seg=(0.86, 0.80, 0.75))
    verdict, rules = run(ch, champion(auc=0.84))
    assert failed(rules) == {"no_regression", "slice_gap"}
    assert verdict == "NO_GO"


def _scan(**pickle_overrides: Any) -> dict[str, Any]:
    return {
        "picklescan": {**CLEAN_SCAN["picklescan"], **pickle_overrides},
        "pip_audit": dict(CLEAN_SCAN["pip_audit"]),
    }


def _with_vuln(severity: str) -> dict[str, Any]:
    scan = _scan()
    scan["pip_audit"]["vulnerabilities"] = [{"package": "x", "id": "GHSA-1", "severity": severity}]
    return scan


@pytest.mark.parametrize(
    ("scan", "fragment"),
    [
        (None, "scan.json missing"),
        (_scan(ran=False), "picklescan did not run"),
        (_scan(scan_error=True), "could not parse the whole model file"),
        ({"picklescan": {"ran": True}, "pip_audit": {"ran": True}}, "could not parse"),
        (_scan(dangerous_globals=2), "2 dangerous global"),
        (_scan(unexpected_globals=["os.system"]), "outside the allowlist: os.system"),
        (_with_vuln("HIGH"), "GHSA-1 (HIGH)"),
        (_with_vuln("CRITICAL"), "GHSA-1 (CRITICAL)"),
        (
            {"picklescan": CLEAN_SCAN["picklescan"], "pip_audit": {"ran": False}},
            "pip-audit did not",
        ),
    ],
)
def test_scan_failures_block(scan: dict[str, Any] | None, fragment: str) -> None:
    verdict, rules = run(metrics(), scan=scan)
    assert rules["scan_clean"].status == "fail"
    assert fragment in rules["scan_clean"].note
    assert verdict == "NO_GO"


def test_lower_severity_vulnerability_does_not_block() -> None:
    _, rules = run(metrics(), scan=_with_vuln("MODERATE"))
    assert rules["scan_clean"].status == "pass"
    assert "1 lower-severity" in rules["scan_clean"].note


def test_bootstrap_skips_champion_rules() -> None:
    verdict, rules = run(metrics())
    assert {k for k, r in rules.items() if r.status == "bootstrap"} == {
        "no_regression",
        "calibration",
        "carbon_efficiency",
    }
    assert verdict == "GO"


def test_report_shape_and_bootstrap_version() -> None:
    report = build_gate_report(metrics(), None, CLEAN_SCAN, None, THRESHOLDS, {"commit": "c"})
    assert report["verdict"] == "GO"
    assert [r["id"] for r in report["rules"]] == list(RULE_IDS)
    assert set(report["rules"][0]) == {"id", "status", "passed", "value", "threshold", "note"}
    assert report["proposed_version_bump"]["to"] == "0.1.0"
    assert report["champion"] is None
    assert report["inputs"] == {"champion": "missing", "scan": "present", "sci": "missing"}


def test_report_flags_holdout_mismatch() -> None:
    champ = champion()
    champ["holdout_sha256"] = "other"
    report = build_gate_report(metrics(), champ, CLEAN_SCAN, None, THRESHOLDS, {})
    assert any("holdout data differs" in n for n in report["notes"])


def _write_thresholds(tmp_path: Path, mutate: Any) -> Path:
    raw = yaml.safe_load((REPO / "config" / "thresholds.yaml").read_text(encoding="utf-8"))
    mutate(raw)
    path = tmp_path / "thresholds.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return path


def test_thresholds_must_define_every_rule(tmp_path: Path) -> None:
    path = _write_thresholds(tmp_path, lambda raw: raw["rules"].pop())
    with pytest.raises(ValueError, match="exactly"):
        Thresholds.from_yaml(path)


def test_thresholds_reject_unknown_review_rule(tmp_path: Path) -> None:
    path = _write_thresholds(
        tmp_path, lambda raw: raw["verdict"]["needs_review_rules"].append("vibes")
    )
    with pytest.raises(ValueError, match="vibes"):
        Thresholds.from_yaml(path)
