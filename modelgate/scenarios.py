"""Demo scenarios: deterministic edits to the feature list in config/train.yaml.

Expected verdicts assume the champion is the v0.1.0 baseline (the `baseline`
scenario). tests/test_scenarios.py checks every one of them end to end.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from modelgate.common import Paths

logger = logging.getLogger(__name__)

BASELINE_FEATURES: tuple[str, ...] = (
    "income",
    "debt_ratio",
    "tenure_years",
    "late_payments",
    "utilization",
    "region_score",
)


@dataclass(frozen=True)
class Scenario:
    """One demo scenario: a feature list and the gate result it should produce.

    expected_verdict and expected_failed_rules describe the outcome against the v0.1.0
    baseline champion; summary is a one-line description that apply logs.
    """

    features: tuple[str, ...]
    expected_verdict: str
    expected_failed_rules: tuple[str, ...]
    summary: str


SCENARIOS: dict[str, Scenario] = {
    "baseline": Scenario(
        features=BASELINE_FEATURES,
        expected_verdict="GO",
        expected_failed_rules=(),
        summary="the v0.1.0 baseline feature set",
    ),
    "good": Scenario(
        features=(*BASELINE_FEATURES, "txn_velocity"),
        expected_verdict="GO",
        expected_failed_rules=(),
        summary="adds txn_velocity, an informative feature; AUC improves for every segment",
    ),
    "bad": Scenario(
        features=(*(f for f in BASELINE_FEATURES if f != "region_score"), "txn_velocity"),
        expected_verdict="NEEDS_REVIEW",
        expected_failed_rules=("slice_gap",),
        summary=(
            "replaces region_score (a bureau score collected only for segment C) with "
            "txn_velocity; overall AUC rises while segment C AUC drops"
        ),
    ),
    "bad-auc": Scenario(
        features=tuple(f for f in BASELINE_FEATURES if f != "late_payments"),
        expected_verdict="NO_GO",
        expected_failed_rules=("no_regression", "calibration"),
        summary="drops late_payments as redundant with debt_ratio; AUC and calibration regress",
    ),
}

_FEATURE_LINE = re.compile(r"^\s+-\s+\S")


def set_features(train_yaml: Path, features: tuple[str, ...]) -> None:
    """Overwrite the top-level `features:` list in train_yaml with the given names.

    The block replaced is the run of indented `- item` lines directly below the first line
    that starts with `features:`; it ends at the first line that is not one (a blank line,
    a comment or the next key). Each name is written as `  - name`. Every line outside that
    block, comments included, is kept; inline comments on the replaced item lines are lost.
    The file is rewritten as UTF-8 with LF line endings and a trailing newline. Raises
    ValueError if no line starts with `features:`.
    """
    lines = train_yaml.read_text(encoding="utf-8").splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.startswith("features:"))
    except StopIteration as exc:
        raise ValueError(f"{train_yaml}: no top-level 'features:' key") from exc
    end = start + 1
    while end < len(lines) and _FEATURE_LINE.match(lines[end]):
        end += 1
    new_block = [f"  - {name}" for name in features]
    text = "\n".join([*lines[: start + 1], *new_block, *lines[end:]]) + "\n"
    train_yaml.write_text(text, encoding="utf-8", newline="\n")


def apply(paths: Paths, name: str) -> Scenario:
    """Write the named scenario's features into config/train.yaml and return the scenario.

    Logs the scenario summary and its expected verdict against the v0.1.0 baseline
    champion. Raises KeyError for a name that is not in SCENARIOS.
    """
    if name not in SCENARIOS:
        raise KeyError(f"unknown scenario {name!r}; choose from {sorted(SCENARIOS)}")
    scenario = SCENARIOS[name]
    set_features(paths.config / "train.yaml", scenario.features)
    logger.info("scenario %s: %s", name, scenario.summary)
    logger.info(
        "expected against the v0.1.0 baseline champion: %s%s",
        scenario.expected_verdict,
        f" (failing: {', '.join(scenario.expected_failed_rules)})"
        if scenario.expected_failed_rules
        else "",
    )
    return scenario
