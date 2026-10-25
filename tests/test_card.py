"""Card and description rendering, and the checks that keep the agent's text honest."""

from __future__ import annotations

import pytest
from conftest import build_reports

from modelgate import card
from modelgate.common import Paths, read_json
from modelgate.scenarios import SCENARIOS

LINEAGE = {
    "project_url": "https://gitlab.example/ns/modelgate",
    "branch": "feature",
    "commit": "abc123",
    "pipeline_id": 1000,
    "pipeline_iid": 17,
    "pipeline_url": "https://gitlab.example/ns/modelgate/-/pipelines/1000",
    "train_job_id": 501,
    "reports_source": "CI artifacts of job train_evaluate #501",
    "card_url": "https://gitlab.example/ns/modelgate/-/blob/feature/models/cards/x.md",
}


def _decision(paths: Paths) -> dict:
    reports = {
        name: read_json(paths.reports / f"{name}.json") for name in ("gate_report", "sci", "scan")
    }
    reports["registry"] = {
        "method": "generic_package",
        "package": "modelgate-model",
        "registered_version": "0.2.0-rc.17",
    }
    return card.build_decision(paths, reports, LINEAGE)


@pytest.fixture(scope="module")
def decisions(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict]:
    """GO (bootstrap) and NEEDS_REVIEW decisions; the second compares to the first."""
    import shutil

    root = tmp_path_factory.mktemp("card")
    paths = build_reports(root)
    go = _decision(paths)
    shutil.copy(paths.reports / "champion_candidate.json", paths.champion_file)
    review_paths = build_reports(root, features=SCENARIOS["bad"].features)
    return {"go": go, "review": _decision(review_paths)}


def test_decision_fields(decisions: dict) -> None:
    go, review = decisions["go"], decisions["review"]
    assert go["verdict"] == "GO"
    assert go["candidate_version"] == "0.1.0-rc.17"
    assert review["verdict"] == "NEEDS_REVIEW"
    assert review["failed_rules"] == ["slice_gap"]
    assert review["champion"]["version"] == "0.1.0"
    assert review["registry"]["method"] == "generic_package"


@pytest.mark.parametrize(
    ("verdict", "title", "label"),
    [
        ("GO", "GO: promote model 0.2.0", "modelgate::go"),
        ("NEEDS_REVIEW", "NEEDS REVIEW: model 0.2.0", "modelgate::review"),
        ("NO_GO", "NO-GO: model 0.2.0", "modelgate::no-go"),
    ],
)
def test_titles_and_labels(verdict: str, title: str, label: str) -> None:
    decision = {"verdict": verdict, "proposed_version": "0.2.0"}
    assert card.mr_title(decision) == title
    assert card.mr_label(decision) == label


def test_card_contains_the_numbers(decisions: dict) -> None:
    text = card.render_card(decisions["review"])
    assert "# Model card: ModelGate candidate 0.2.0-rc.17" in text
    assert "| Verdict | **NEEDS REVIEW** |" in text
    assert "| C | " in text and "-0.1360" in text  # segment C AUC delta vs champion
    assert "| `slice_gap` | FAIL | 0.1658 | <= 0.0500 |" in text
    assert "0.05 gCO2e" in text


def test_unwritten_analysis_is_rejected(decisions: dict) -> None:
    problems = card.check_card(card.render_card(decisions["go"]), decisions["go"])
    assert "the Analysis section has not been written" in problems


def test_honest_analysis_passes(decisions: dict) -> None:
    d = decisions["review"]
    analysis = (
        "Replacing region_score with txn_velocity raised overall AUC by 0.0161, but the "
        "slice_gap rule failed: segment C lost 0.1360 AUC. Calibration passed. Recommend "
        "keeping region_score and adding txn_velocity instead."
    )
    assert card.check_card(card.render_card(d, analysis), d) == []


@pytest.mark.parametrize(
    "analysis",
    [
        "All rules passed and slice_gap passed comfortably.",
        "The segment gap was satisfied.",
        "Calibration failed badly.",
    ],
)
def test_contradicting_analysis_is_rejected(decisions: dict, analysis: str) -> None:
    d = decisions["review"]
    problems = card.check_card(card.render_card(d, analysis), d)
    assert any("but it" in p for p in problems), problems


def test_invented_numbers_are_rejected(decisions: dict) -> None:
    d = decisions["review"]
    url = LINEAGE["card_url"]
    # 0.0200 is computed, not copied; 0.80 is the published minimum written as in AGENTS.md.
    analysis = "Overall AUC rose 0.0161, about 0.0200 short of a clean gain, above the 0.80 floor."
    problems = card.check_card(card.render_card(d, analysis), d)
    assert [p for p in problems if "not a number in the card" in p] == [
        "0.0200 is not a number in the card or the decision record; repeat the card's "
        "numbers exactly instead of computing new ones"
    ]
    summary = "NEEDS REVIEW: slice_gap.\nSegment C lost 13.6 points.\nCheck segment C."
    problems = card.check_description(card.render_description(d, url, summary), d, url)
    assert any(p.startswith("13.6 is not a number") for p in problems), problems


def test_negated_claims_are_fine(decisions: dict) -> None:
    d = decisions["review"]
    text = card.render_card(d, "The slice_gap rule did not pass. Calibration did not fail.")
    assert card.check_card(text, d) == []


def test_edits_outside_analysis_are_rejected(decisions: dict) -> None:
    d = decisions["review"]
    text = card.render_card(d, "slice_gap failed.").replace("0.1658", "0.0158")
    assert "text outside the Analysis section differs from the rendered card" in (
        card.check_card(text, d)
    )


def test_description_summary_rules(decisions: dict) -> None:
    d = decisions["review"]
    url = LINEAGE["card_url"]
    good = card.render_description(d, url, "Line one.\nslice_gap failed for segment C.\nLine 3.")
    assert card.check_description(good, d, url) == []
    long = card.render_description(d, url, "a\nb\nc\nd")
    assert "the summary has more than 3 lines" in card.check_description(long, d, url)
    unwritten = card.render_description(d, url)
    assert "the summary has not been written" in card.check_description(unwritten, d, url)
    tampered = good.replace("NEEDS REVIEW", "GO")
    assert any("differs" in p for p in card.check_description(tampered, d, url))


def test_description_states_what_merging_does(decisions: dict) -> None:
    url = LINEAGE["card_url"]
    assert "promotes `models/champion.json`" in card.render_description(decisions["go"], url)
    assert "does not promote it" in card.render_description(decisions["review"], url)


@pytest.mark.parametrize(
    ("text", "flagged"),
    [
        ("ModelGate stopped this candidate.", False),
        ("Run `python -m modelgate.flow commit` to finish.", False),
        ("Model Gate stopped this candidate.", True),
        ("The model-promotion-gate project disagrees.", True),
        ("modelgate is strict here.", True),
    ],
)
def test_project_name_is_checked(decisions: dict, text: str, flagged: bool) -> None:
    d = decisions["review"]
    problems = card.check_card(card.render_card(d, text), d)
    assert any("called ModelGate" in p for p in problems) is flagged


def test_decision_carries_thresholds_and_accuracy_per_gram(decisions: dict) -> None:
    d = decisions["review"]
    assert d["thresholds"]["rules"]["slice_gap"] == {"max_gap": 0.05}
    assert len(d["thresholds"]["sha256"]) == 64
    # +0.0161 AUC for 0.05 gCO2e = 0.322 AUC per gram
    assert d["sci"]["auc_gain_per_gco2e"] == pytest.approx(0.322)
    text = card.render_card(d, "x")
    assert "| AUC gained per gCO2e | 0.32 |" in text
    assert "No nightly drift check of the champion has run yet." in text


def test_regenerated_reports_are_flagged_in_the_card(decisions: dict) -> None:
    d = {
        **decisions["review"],
        "lineage": {**LINEAGE, "reports_source": "regenerated in the flow sandbox (x)"},
    }
    assert "measured on the flow's runner" in card.render_card(d, "x")


def test_champion_drift_appears_when_checked(tmp_path) -> None:
    paths = build_reports(tmp_path)
    reports = {n: read_json(paths.reports / f"{n}.json") for n in ("gate_report", "sci", "scan")}
    reports["drift"] = {
        "status": "checked",
        "date": "2026-10-27",
        "champion_version": "0.1.0",
        "holdout_auc": 0.8397,
        "drift": {"auc": 0.8343},
        "max_psi": 0.214,
        "alert": True,
        "reasons": ["PSI of utilization is 0.2140 (limit 0.1)"],
    }
    d = card.build_decision(paths, reports, LINEAGE)
    text = card.render_card(d, "x")
    assert "champion 0.1.0 (2026-10-27): AUC 0.8343" in text
    assert "alert: PSI of utilization" in text


@pytest.mark.parametrize(
    ("rule", "margin"),
    [
        ({"id": "no_regression", "value": 0.8073, "threshold": 0.8347}, -0.0274),
        ({"id": "min_absolute_auc", "value": 0.8734, "threshold": 0.8}, 0.0734),
        ({"id": "slice_gap", "value": 0.1658, "threshold": 0.05}, -0.1158),
        ({"id": "calibration", "value": 0.1592, "threshold": 0.1558}, -0.0034),
        ({"id": "scan_clean", "value": 2.0, "threshold": 0}, -2.0),
        ({"id": "carbon_efficiency", "value": 3.0, "threshold": 2.0}, -1.0),
        ({"id": "no_regression", "value": None, "threshold": None}, None),
    ],
)
def test_rule_margin_by_hand(rule: dict, margin: float | None) -> None:
    assert card.rule_margin(rule) == margin


def test_decision_rules_carry_margins(decisions: dict) -> None:
    gap = next(r for r in decisions["review"]["rules"] if r["id"] == "slice_gap")
    assert gap["margin"] == pytest.approx(0.05 - 0.1658)
