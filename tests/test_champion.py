"""Where the gate reads the champion from."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from modelgate.common import Paths, Response, write_json
from modelgate.evaluate import load_champion

CHAMP = {"version": "0.1.0", "config": {}, "metrics": {}}
BRANCH_OWN = {"version": "0.2.0", "config": {}, "metrics": {}}


@pytest.fixture
def branch_ci(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Paths:
    for key, value in {
        "CI_JOB_TOKEN": "tok",
        "CI_API_V4_URL": "https://gitlab.example/api/v4",
        "CI_PROJECT_ID": "42",
        "CI_COMMIT_BRANCH": "feature",
        "CI_DEFAULT_BRANCH": "main",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("MODELGATE_CHAMPION_REF", raising=False)
    paths = Paths(tmp_path)
    # The branch's own working tree already carries its GO candidate.
    write_json(paths.champion_file, BRANCH_OWN)
    return paths


def test_branch_pipeline_uses_default_branch_copy(branch_ci: Paths) -> None:
    seen: list[str] = []

    def http(method: str, url: str, headers: dict, data: bytes | None) -> Response:
        seen.append(url)
        assert headers == {"JOB-TOKEN": "tok"}
        return Response(200, CHAMP, "", json.dumps(CHAMP).encode())

    champion, source = load_champion(branch_ci, http)
    assert champion == CHAMP
    assert source == "default branch main via Files API"
    assert seen == [
        "https://gitlab.example/api/v4/projects/42/repository/files/"
        "models%2Fchampion.json/raw?ref=main"
    ]


def test_no_champion_on_default_branch_is_bootstrap(branch_ci: Paths) -> None:
    champion, _ = load_champion(branch_ci, lambda *a: Response(404, None, ""))
    assert champion is None


def test_failed_lookup_fails_closed(branch_ci: Paths) -> None:
    with pytest.raises(RuntimeError, match="returned 500"):
        load_champion(branch_ci, lambda *a: Response(500, None, ""))


def test_default_branch_and_local_runs_use_working_tree(
    branch_ci: Paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CI_COMMIT_BRANCH", "main")
    assert load_champion(branch_ci, lambda *a: Response(500, None, "")) == (
        BRANCH_OWN,
        "working tree",
    )
    monkeypatch.delenv("CI_JOB_TOKEN")
    assert load_champion(branch_ci)[1] == "working tree"


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_git_ref_reads_the_committed_champion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = Paths(tmp_path)
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    _git(tmp_path, "commit", "-q", "--allow-empty", "-m", "start")
    monkeypatch.setenv("MODELGATE_CHAMPION_REF", "main")
    assert load_champion(paths) == (None, "git main")
    write_json(paths.champion_file, CHAMP)
    _git(tmp_path, "add", "models/champion.json")
    _git(tmp_path, "commit", "-q", "-m", "champion")
    write_json(paths.champion_file, BRANCH_OWN)  # uncommitted change must be ignored
    assert load_champion(paths) == (CHAMP, "git main")
    monkeypatch.setenv("MODELGATE_CHAMPION_REF", "no-such-ref")
    with pytest.raises(RuntimeError, match="git show"):
        load_champion(paths)
