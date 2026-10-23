"""The flow helper against a real git repository with a bare origin and a fake GitLab API."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from conftest import CLEAN_SCAN, REPO, build_reports

from modelgate import card, flow
from modelgate.common import Paths, Response

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")

PROJECT_URL = "https://gitlab.example/ns/modelgate"
API = "https://gitlab.example/api/v4/projects/42"
REGISTRY = {
    "method": "generic_package",
    "package": "modelgate-model",
    "registered_version": "0.1.0-rc.17",
}


def _git(cwd: Path, *args: str) -> str:
    out = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return out.stdout.strip()


class FakeApi:
    """Serves one pipeline's jobs and report artifacts, and one merge request."""

    def __init__(self, reports: Path | None, allow: bool = True, trained: bool = True) -> None:
        self.reports = reports
        self.allow = allow
        self.trained = trained
        self.mr_description = ""
        self.drift: dict[str, Any] | None = None

    def __call__(self, method: str, url: str, headers: dict, data: bytes | None) -> Response:
        if not self.allow:
            return Response(401, {"message": "401 Unauthorized"}, "")
        if url == f"{API}/pipelines/1000/jobs?per_page=100":
            jobs = [
                {"id": 501, "name": "train_evaluate", "status": "success"},
                {"id": 502, "name": "register_challenger", "status": "success"},
            ]
            if not self.trained:
                jobs = [{"id": 503, "name": "unit_tests", "status": "success"}]
            return Response(200, jobs, "")
        match = re.fullmatch(rf"{API}/jobs/(\d+)/artifacts/reports/(\w+)\.json", url)
        if match:
            job, name = match.groups()
            if job == "502" and name == "registry":
                raw = json.dumps(REGISTRY).encode()
                return Response(200, REGISTRY, "", raw)
            path = (self.reports or Path("/nonexistent")) / f"{name}.json"
            if job == "501" and path.exists():
                raw = path.read_bytes()
                return Response(200, json.loads(raw), "", raw)
        if url.endswith("/merge_requests/5"):
            return Response(200, {"description": self.mr_description}, "")
        if (
            url == f"{API}/jobs/artifacts/main/raw/reports/drift.json?job=drift_check"
            and self.drift
        ):
            raw = json.dumps(self.drift).encode()
            return Response(200, self.drift, "", raw)
        return Response(404, None, "")


@pytest.fixture(scope="module")
def artifacts(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("artifacts")
    failing_scan = {"picklescan": CLEAN_SCAN["picklescan"], "pip_audit": {"ran": False}}
    return {
        "GO": build_reports(root / "go").reports,
        "NO_GO": build_reports(root / "nogo", scan=failing_scan).reports,
    }


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Paths, str]:
    """main with config only; feature with one training commit, pushed to origin."""
    for var in ("GITLAB_TOKEN", "MODELGATE_CHAMPION_REF", "CI_JOB_TOKEN", "CI_COMMIT_BRANCH"):
        monkeypatch.delenv(var, raising=False)
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    work = tmp_path / "work"
    work.mkdir()
    _git(work, "init", "-q", "-b", "main")
    _git(work, "config", "user.email", "dev@example.com")
    _git(work, "config", "user.name", "Dev")
    shutil.copytree(REPO / "config", work / "config")
    (work / ".gitignore").write_text("reports/\ndata/\nmodels/challenger/\n", encoding="utf-8")
    _git(work, "add", ".")
    _git(work, "commit", "-q", "-m", "start")
    _git(work, "remote", "add", "origin", str(origin))
    _git(work, "push", "-q", "origin", "main")
    _git(work, "checkout", "-q", "-b", "feature")
    train_yaml = work / "config" / "train.yaml"
    train_yaml.write_text(train_yaml.read_text(encoding="utf-8") + "# tweak\n", encoding="utf-8")
    _git(work, "commit", "-q", "-am", "training change")
    _git(work, "push", "-q", "origin", "feature")
    sha = _git(work, "rev-parse", "HEAD")
    _git(work, "checkout", "-q", "main")  # the flow job does not start on the branch
    return Paths(work), sha


def _target(sha: str, iid: int = 17) -> flow.Target:
    return flow.Target(PROJECT_URL, "42", 1000, iid, "feature", sha, "main")


def _write_agent_text(paths: Paths, candidate: str) -> None:
    card_file = paths.models / "cards" / f"{candidate}.md"
    card_file.write_text(
        card_file.read_text(encoding="utf-8").replace(
            card.ANALYSIS_PLACEHOLDER, "First candidate; every applicable rule passed."
        ),
        encoding="utf-8",
    )
    desc = paths.root / flow.DESCRIPTION_FILE
    desc.write_text(
        desc.read_text(encoding="utf-8").replace(
            card.SUMMARY_PLACEHOLDER, "Baseline model.\nAUC 0.8397.\nPromote as 0.1.0."
        ),
        encoding="utf-8",
    )


def test_go_card_is_prepared_committed_and_pushed(repo, artifacts) -> None:
    paths, sha = repo
    api = FakeApi(artifacts["GO"])
    result = flow.prepare(paths, _target(sha), api)
    assert result["status"] == "ready"
    assert (result["verdict"], result["candidate_version"]) == ("GO", "0.1.0-rc.17")
    assert result["reports_source"] == "CI artifacts of job train_evaluate #501"
    assert result["champion_action"] == "set to 0.1.0"
    assert result["mr"]["title"] == "GO: promote model 0.1.0"
    staged = _git(paths.root, "diff", "--cached", "--name-only").splitlines()
    assert staged == [
        "models/cards/0.1.0-rc.17.md",
        "models/champion.json",
        "models/decisions/0.1.0-rc.17.json",
    ]
    champion = json.loads(paths.champion_file.read_text(encoding="utf-8"))
    assert champion["registered_as"]["registered_version"] == "0.1.0-rc.17"
    assert champion["commit"] == sha

    rejected = flow.commit(paths, "0.1.0-rc.17", push=True)
    assert rejected["status"] == "rejected"
    assert any("Analysis" in p for p in rejected["problems"])

    _write_agent_text(paths, "0.1.0-rc.17")
    done = flow.commit(paths, "0.1.0-rc.17", push=True)
    assert done["status"] == "committed"
    assert done["message"] == "[skip ci] modelgate: card for 0.1.0-rc.17 (GO)"
    origin = paths.root.parent / "origin.git"
    assert _git(origin, "rev-parse", "refs/heads/feature") == done["commit"]
    assert flow.commit(paths, "0.1.0-rc.17")["status"] == "nothing_to_commit"

    api.mr_description = (paths.root / flow.DESCRIPTION_FILE).read_text(encoding="utf-8")
    assert flow.verify_mr(paths, "0.1.0-rc.17", 5, api)["status"] == "match"
    api.mr_description = api.mr_description.replace("0.8397", "0.9397")
    assert flow.verify_mr(paths, "0.1.0-rc.17", 5, api)["status"] == "mismatch"


def test_later_no_go_resets_the_branch_champion(repo, artifacts) -> None:
    paths, sha = repo
    flow.prepare(paths, _target(sha), FakeApi(artifacts["GO"]))
    _write_agent_text(paths, "0.1.0-rc.17")
    flow.commit(paths, "0.1.0-rc.17", push=True)

    # A second pipeline on the same commit (for example a re-run) now says NO_GO.
    result = flow.prepare(paths, _target(sha, iid=18), FakeApi(artifacts["NO_GO"]))
    assert result["status"] == "ready"
    assert result["verdict"] == "NO_GO"
    assert result["champion_action"].startswith("removed")
    assert not paths.champion_file.exists()
    staged = _git(paths.root, "diff", "--cached", "--name-status").splitlines()
    assert "D\tmodels/champion.json" in staged


def test_branch_with_newer_code_is_superseded(repo, artifacts) -> None:
    paths, sha = repo
    _git(paths.root, "checkout", "-q", "feature")
    (paths.root / "config" / "train.yaml").write_text("seed: 2\n", encoding="utf-8")
    _git(paths.root, "commit", "-q", "-am", "newer change")
    _git(paths.root, "push", "-q", "origin", "feature")
    result = flow.prepare(paths, _target(sha), FakeApi(artifacts["GO"]))
    assert result["status"] == "superseded"
    assert "config/train.yaml" in result["reason"]
    assert not (paths.models / "cards").exists()


def test_regenerates_when_artifacts_cannot_be_read(repo, artifacts, monkeypatch) -> None:
    paths, sha = repo
    calls: list[str] = []

    def fake_regenerate(p: Paths, t: Any) -> None:
        calls.append(t.default_branch)
        shutil.copytree(artifacts["GO"], p.reports, dirs_exist_ok=True)

    monkeypatch.setattr(flow, "regenerate", fake_regenerate)
    result = flow.prepare(paths, _target(sha), FakeApi(None, allow=False))
    assert calls == ["main"]
    assert result["reports_source"].startswith("regenerated in the flow sandbox")
    assert {a["status"] for a in result["artifact_attempts"]} == {401}


def test_prepare_includes_the_champions_drift(repo, artifacts) -> None:
    paths, sha = repo
    api = FakeApi(artifacts["GO"])
    api.drift = {
        "status": "checked",
        "date": "2026-10-26",
        "champion_version": "0.1.0",
        "holdout_auc": 0.8397,
        "drift": {"auc": 0.83},
        "max_psi": 0.12,
        "alert": True,
        "reasons": ["PSI of utilization is 0.1200 (limit 0.1)"],
    }
    flow.prepare(paths, _target(sha), api)
    decision = json.loads((paths.models / "decisions" / "0.1.0-rc.17.json").read_text("utf-8"))
    assert decision["champion_drift"]["max_psi"] == 0.12


def test_pipeline_without_training_has_no_candidate(repo, artifacts) -> None:
    paths, sha = repo
    result = flow.prepare(paths, _target(sha), FakeApi(artifacts["GO"], trained=False))
    assert result["status"] == "no_candidate"
    assert not (paths.models / "cards").exists()
