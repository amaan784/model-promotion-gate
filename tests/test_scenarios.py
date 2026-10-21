"""End to end on the real data configuration: the demo scenarios must give the
verdicts the docs promise, and re-running evaluation must give identical bytes."""

from __future__ import annotations

import shutil

import pytest
from conftest import CLEAN_SCAN

from modelgate import data, evaluate, scenarios, train
from modelgate.common import Paths, read_json, write_json


def _candidate(paths: Paths, name: str) -> dict:
    scenarios.apply(paths, name)
    train.main(paths)
    evaluate.evaluate_challenger(paths)
    return evaluate.gate(paths)


@pytest.fixture(scope="module")
def baseline_repo(tmp_path_factory: pytest.TempPathFactory) -> Paths:
    """Data generated once and the baseline promoted to champion v0.1.0."""
    from conftest import REPO

    root = tmp_path_factory.mktemp("repo")
    shutil.copytree(REPO / "config", root / "config")
    paths = Paths(root)
    data.main(paths)
    write_json(paths.reports / "scan.json", CLEAN_SCAN)
    report = _candidate(paths, "baseline")
    assert report["verdict"] == "GO"
    assert report["proposed_version_bump"]["to"] == "0.1.0"
    shutil.copy(paths.reports / "champion_candidate.json", paths.champion_file)
    return paths


@pytest.mark.parametrize("name", ["good", "bad", "bad-auc"])
def test_scenario_verdicts(baseline_repo: Paths, name: str) -> None:
    expected = scenarios.SCENARIOS[name]
    report = _candidate(baseline_repo, name)
    assert report["verdict"] == expected.expected_verdict
    assert report["failed_rules"] == list(expected.expected_failed_rules)


def test_good_scenario_is_a_minor_bump(baseline_repo: Paths) -> None:
    report = _candidate(baseline_repo, "good")
    bump = report["proposed_version_bump"]
    assert (bump["kind"], bump["from"], bump["to"]) == ("minor", "0.1.0", "0.2.0")
    assert all(delta > 0 for delta in report["comparison"]["segment_auc_delta"].values())


def test_bad_scenario_hides_harm_behind_a_better_headline(baseline_repo: Paths) -> None:
    report = _candidate(baseline_repo, "bad")
    assert report["comparison"]["auc_delta"] > 0
    assert report["comparison"]["segment_auc_delta"]["C"] < -0.1


def test_evaluation_is_byte_identical_on_rerun(baseline_repo: Paths) -> None:
    _candidate(baseline_repo, "good")
    metrics_first = (baseline_repo.reports / "metrics.json").read_bytes()
    gate_first = read_json(baseline_repo.reports / "gate_report.json")

    train.main(baseline_repo)
    evaluate.evaluate_challenger(baseline_repo)
    evaluate.gate(baseline_repo)

    assert (baseline_repo.reports / "metrics.json").read_bytes() == metrics_first
    gate_second = read_json(baseline_repo.reports / "gate_report.json")
    gate_first.pop("run")
    gate_second.pop("run")
    assert gate_second == gate_first


def test_set_features_keeps_comments(repo_copy: Paths) -> None:
    path = repo_copy.config / "train.yaml"
    before = path.read_text(encoding="utf-8")
    scenarios.set_features(path, ("income", "txn_velocity"))
    after = path.read_text(encoding="utf-8")
    assert "  - income\n  - txn_velocity\n\nhyperparams:" in after
    comments = [line for line in before.splitlines() if line.lstrip().startswith("#")]
    assert comments == [line for line in after.splitlines() if line.lstrip().startswith("#")]
    assert train.TrainConfig.from_yaml(path).features == ("income", "txn_velocity")
