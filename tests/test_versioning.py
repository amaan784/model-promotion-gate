from __future__ import annotations

from typing import Any

import pytest

from modelgate.evaluate import bump_version, propose_bump

BASE: dict[str, Any] = {
    "seed": 1,
    "model_family": "logistic_regression",
    "output_schema": "default_probability_v1",
    "features": ["income", "debt_ratio"],
    "hyperparams": {"C": 1.0},
}


def champ(**overrides: Any) -> dict[str, Any]:
    return {"version": "1.4.2", "config": {**BASE, **overrides}}


@pytest.mark.parametrize(
    ("kind", "expected"), [("patch", "1.4.3"), ("minor", "1.5.0"), ("major", "2.0.0")]
)
def test_bump_version(kind: str, expected: str) -> None:
    assert bump_version("1.4.2", kind) == expected  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", ["1.4", "v1.4.2", "1.4.2-rc.1", "01.2.3"])
def test_bump_rejects_non_final_semver(bad: str) -> None:
    with pytest.raises(ValueError):
        bump_version(bad, "patch")


def test_same_config_is_patch() -> None:
    bump = propose_bump(BASE, champ())
    assert (bump["kind"], bump["to"]) == ("patch", "1.4.3")
    assert bump["reasons"] == ["retrain with unchanged config"]


def test_hyperparam_change_is_patch() -> None:
    bump = propose_bump({**BASE, "hyperparams": {"C": 0.5}}, champ())
    assert bump["kind"] == "patch"
    assert bump["reasons"] == ["hyperparam C: 1.0 -> 0.5"]


def test_feature_change_is_minor() -> None:
    bump = propose_bump({**BASE, "features": ["income", "utilization"]}, champ())
    assert (bump["kind"], bump["to"]) == ("minor", "1.5.0")
    assert bump["reasons"] == ["features added: utilization", "features removed: debt_ratio"]


def test_feature_reorder_alone_is_patch() -> None:
    bump = propose_bump({**BASE, "features": ["debt_ratio", "income"]}, champ())
    assert bump["kind"] == "patch"
    assert "feature order changed" in bump["reasons"]


@pytest.mark.parametrize(
    "change", [{"model_family": "gradient_boosting"}, {"output_schema": "score_v2"}]
)
def test_family_or_schema_change_is_major(change: dict[str, str]) -> None:
    bump = propose_bump({**BASE, **change, "features": ["income"]}, champ())
    assert (bump["kind"], bump["to"]) == ("major", "2.0.0")
    assert "features removed: debt_ratio" in bump["reasons"]


def test_no_champion_is_initial_version() -> None:
    bump = propose_bump(BASE, None)
    assert (bump["kind"], bump["from"], bump["to"]) == ("initial", None, "0.1.0")
