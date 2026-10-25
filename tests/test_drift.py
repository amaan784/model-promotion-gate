"""Nightly drift check: dated batches, PSI, alerts and the carried-forward history."""

from __future__ import annotations

import json
import shutil
from datetime import date

import numpy as np
import pytest
from conftest import build_reports

from modelgate import drift, site
from modelgate.common import Paths, Response, load_yaml


def test_psi_by_hand() -> None:
    expected = np.arange(1000, dtype=float)
    assert drift.psi(expected, expected.copy()) == pytest.approx(0.0, abs=1e-12)
    # Everything lands in the top decile: (1 - 0.1) ln(1 / 0.1) + 9 (1e-4 - 0.1) ln(1e-4 / 0.1)
    shifted = np.full(1000, 999.0)
    by_hand = 0.9 * np.log(10) + 9 * (1e-4 - 0.1) * np.log(1e-4 / 0.1)
    assert drift.psi(expected, shifted) == pytest.approx(by_hand)


def test_psi_of_a_constant_feature_is_zero() -> None:
    zeros = np.zeros(500)
    assert drift.psi(zeros, zeros) == pytest.approx(0.0, abs=1e-12)


def test_drift_ramps_with_the_date(repo_copy: Paths) -> None:
    raw = load_yaml(repo_copy.config / "data.yaml")
    assert drift.drift_scale(raw, date(2026, 10, 19)) == 0.0
    assert drift.drift_scale(raw, date(2026, 10, 23)) == pytest.approx(3 / 7)
    assert drift.drift_scale(raw, date(2027, 1, 1)) == 1.0


def test_same_date_same_batch(repo_copy: Paths) -> None:
    a, _, _ = drift.drift_batch(repo_copy, date(2026, 10, 25))
    b, _, _ = drift.drift_batch(repo_copy, date(2026, 10, 25))
    c, _, _ = drift.drift_batch(repo_copy, date(2026, 10, 26))
    assert np.array_equal(a.features["income"], b.features["income"])
    assert not np.array_equal(a.features["income"], c.features["income"])


@pytest.fixture(scope="module")
def champion_repo(tmp_path_factory: pytest.TempPathFactory) -> Paths:
    paths = build_reports(tmp_path_factory.mktemp("drift"))
    shutil.copy(paths.reports / "champion_candidate.json", paths.champion_file)
    return paths


def test_check_before_and_after_full_drift(champion_repo: Paths) -> None:
    early = drift.check(champion_repo, date(2026, 10, 20))
    late = drift.check(champion_repo, date(2026, 12, 1))
    assert early["status"] == late["status"] == "checked"
    assert early["reproduced"] is True  # retrained from champion.json's own config
    assert early["drift_scale"] == 0.0 and late["drift_scale"] == 1.0
    assert early["holdout_auc"] == late["holdout_auc"] == 0.8397
    assert late["psi"]["income"] > early["psi"]["income"]
    assert late["segment_mix"]["C"] > early["segment_mix"]["C"]
    assert early["alert"] is False
    assert late["alert"] is True and any(r.startswith("PSI of") for r in late["reasons"])


def test_no_champion_is_reported(repo_copy: Paths) -> None:
    assert drift.check(repo_copy, date(2026, 10, 21))["status"] == "no_champion"


def test_history_keeps_one_entry_per_date() -> None:
    history = [{"date": "2026-10-21", "v": 1}, {"date": "2026-10-22", "v": 2}]
    merged = drift.merge_history(history, {"date": "2026-10-21", "v": 3})
    assert [(h["date"], h["v"]) for h in merged] == [("2026-10-21", 3), ("2026-10-22", 2)]


def test_history_survives_a_push_to_the_default_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    # After a merge, main's latest successful pipeline has no drift_check job; its pages job
    # carried the history forward in public/data.json.
    for name, value in {
        "CI_JOB_TOKEN": "job-token",
        "CI_API_V4_URL": "https://gitlab.example/api/v4",
        "CI_PROJECT_ID": "42",
        "CI_DEFAULT_BRANCH": "main",
    }.items():
        monkeypatch.setenv(name, value)
    base = "https://gitlab.example/api/v4/projects/42/jobs/artifacts/main/raw"
    history = [{"date": "2026-10-25"}, {"date": "2026-10-26"}]
    seen: list[str] = []

    def api(method: str, url: str, headers: dict, data: bytes | None) -> Response:
        seen.append(url)
        if url == f"{base}/public/data.json?job=pages":
            body = {"drift_history": history, "decisions": []}
            return Response(200, body, "", json.dumps(body).encode())
        return Response(404, None, "")

    assert site.fetch_drift_history(api) == history
    assert seen == [
        f"{base}/reports/drift_history.json?job=drift_check",
        f"{base}/public/data.json?job=pages",
    ]


def test_main_appends_to_local_history(champion_repo: Paths) -> None:
    no_api = lambda *a: Response(404, None, "")  # noqa: E731
    (champion_repo.reports / "drift_history.json").unlink(missing_ok=True)
    drift.main(champion_repo, no_api, date(2026, 10, 22))
    entry = drift.main(champion_repo, no_api, date(2026, 10, 23))
    history = json.loads((champion_repo.reports / "drift_history.json").read_text("utf-8"))
    assert [h["date"] for h in history] == ["2026-10-22", "2026-10-23"]
    assert entry["champion_version"] == "0.1.0"
