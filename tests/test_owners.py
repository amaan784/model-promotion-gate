"""Reviewers from config/review.yaml: review requests, the description line and CODEOWNERS."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from conftest import REPO, build_reports, copy_config

from modelgate import card, owners
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
}


def _set_reviewers(paths: Paths, names: list[str]) -> None:
    (paths.config / "review.yaml").write_text(f"reviewers: {names}\n", encoding="utf-8")


def test_reviewers_are_read_and_validated(repo_copy: Paths) -> None:
    _set_reviewers(repo_copy, [])
    assert owners.reviewers(repo_copy) == []
    _set_reviewers(repo_copy, ["@alice", "bob.smith"])
    assert owners.reviewers(repo_copy) == ["alice", "bob.smith"]
    _set_reviewers(repo_copy, ["not a user"])
    with pytest.raises(ValueError, match="not GitLab usernames"):
        owners.reviewers(repo_copy)


def test_codeowners_lists_every_owned_path() -> None:
    text = owners.render_codeowners(["alice", "bob"])
    assert "[ModelGate promotion]" in text
    for path in owners.OWNED_PATHS:
        assert f"{path} @alice @bob" in text
    assert "no file has an owner" in owners.render_codeowners([])


def test_committed_codeowners_matches_review_yaml() -> None:
    committed = (REPO / ".gitlab" / "CODEOWNERS").read_text(encoding="utf-8")
    assert committed == owners.render_codeowners(owners.reviewers(Paths(REPO)))


@pytest.fixture(scope="module")
def decisions(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict]:
    root = tmp_path_factory.mktemp("owners")
    paths = build_reports(root)
    _set_reviewers(paths, ["alice"])

    def decide(p: Paths) -> dict:
        reports = {n: read_json(p.reports / f"{n}.json") for n in ("gate_report", "sci", "scan")}
        return card.build_decision(p, reports, LINEAGE)

    go = decide(paths)
    shutil.copy(paths.reports / "champion_candidate.json", paths.champion_file)
    review = decide(build_reports(root, features=SCENARIOS["bad"].features))
    no_go = decide(build_reports(root, features=SCENARIOS["bad-auc"].features))
    return {"GO": go, "NEEDS_REVIEW": review, "NO_GO": no_go}


@pytest.mark.parametrize(
    ("verdict", "requested"), [("GO", True), ("NEEDS_REVIEW", True), ("NO_GO", False)]
)
def test_review_is_requested_when_a_person_must_decide(
    decisions: dict, verdict: str, requested: bool
) -> None:
    d = decisions[verdict]
    assert d["verdict"] == verdict
    assert d["review"]["reviewers"] == (["alice"] if requested else [])
    text = card.render_description(d, "https://gitlab.example/card.md")
    assert ("Review requested from @alice" in text) is requested


def test_needs_review_without_reviewers_says_so(decisions: dict) -> None:
    d = {**decisions["NEEDS_REVIEW"], "review": {"reviewers": [], "reason": "x"}}
    text = card.render_description(d, "https://gitlab.example/card.md")
    assert "No reviewers are configured in `config/review.yaml`" in text


def test_main_writes_the_file(tmp_path: Path) -> None:
    copy_config(tmp_path)
    _set_reviewers(Paths(tmp_path), ["carol"])
    owners.main(Paths(tmp_path))
    assert "/models/champion.json @carol" in (tmp_path / ".gitlab" / "CODEOWNERS").read_text(
        "utf-8"
    )
