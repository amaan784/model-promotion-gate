"""Promotion on the default branch, and the Pages site, against a fake GitLab API."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

import pytest
from conftest import build_reports

from modelgate import card, promote, site
from modelgate.common import Paths, Response, read_json, write_json

API = "https://gitlab.example/api/v4"
PROJECT = f"{API}/projects/42"
SHA = "c" * 40


class FakeGitLab:
    def __init__(self, release_status: int = 201) -> None:
        self.release_status = release_status
        self.release_payload: dict[str, Any] | None = None
        self.uploads: list[str] = []

    def __call__(self, method: str, url: str, headers: dict, data: bytes | None) -> Response:
        if "/ml/mlflow/" in url:
            return Response(401, {"message": "401 Unauthorized"}, "")
        if "/packages/generic/" in url and method == "PUT":
            self.uploads.append(url.removeprefix(PROJECT))
            return Response(201, {"message": "201 Created"}, "")
        if url == f"{PROJECT}/repository/commits/{SHA}/merge_requests":
            mr = {"iid": 5, "state": "merged", "web_url": "https://gitlab.example/mr/5"}
            return Response(200, [mr], "")
        if url == f"{PROJECT}/releases" and method == "POST":
            self.release_payload = json.loads(data or b"{}")
            return Response(self.release_status, {"tag_name": "model-v0.1.0"}, "")
        return Response(404, None, "")


@pytest.fixture
def main_pipeline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Paths:
    """The default branch after a GO merge: champion.json, card and decision, plus this
    pipeline's own retrained model and metrics."""
    for key, value in {
        "CI_JOB_TOKEN": "tok",
        "CI_API_V4_URL": API,
        "CI_PROJECT_ID": "42",
        "CI_PIPELINE_IID": "20",
        "CI_COMMIT_SHA": SHA,
        "CI_PROJECT_URL": "https://gitlab.example/ns/modelgate",
        "CI_PIPELINE_URL": "https://gitlab.example/ns/modelgate/-/pipelines/2000",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("CI_COMMIT_BRANCH", raising=False)
    paths = build_reports(tmp_path)
    lineage = {
        "project_url": "https://gitlab.example/ns/modelgate",
        "branch": "bootstrap-champion",
        "commit": "b" * 40,
        "pipeline_id": 1000,
        "pipeline_iid": 17,
        "pipeline_url": "https://gitlab.example/ns/modelgate/-/pipelines/1000",
        "train_job_id": 501,
        "reports_source": "CI artifacts of job train_evaluate #501",
        "card_url": "https://gitlab.example/x.md",
    }
    reports = {n: read_json(paths.reports / f"{n}.json") for n in ("gate_report", "sci", "scan")}
    decision = card.build_decision(paths, reports, lineage)
    candidate = decision["candidate_version"]
    write_json(paths.models / "decisions" / f"{candidate}.json", decision)
    (paths.models / "cards").mkdir(parents=True, exist_ok=True)
    (paths.models / "cards" / f"{candidate}.md").write_text(
        card.render_card(decision, "Baseline. Every applicable rule passed."), encoding="utf-8"
    )
    champion = read_json(paths.reports / "champion_candidate.json")
    champion.update(
        {"candidate_version": candidate, "card": f"models/cards/{candidate}.md", "commit": "b" * 40}
    )
    write_json(paths.champion_file, champion)
    return paths


def test_promotion_registers_releases_and_records(main_pipeline: Paths) -> None:
    gitlab = FakeGitLab()
    promotion = promote.main(main_pipeline, http=gitlab)
    assert promotion["version"] == "0.1.0"
    assert promotion["reproduced"] is True
    assert promotion["registry"]["method"] == "generic_package"
    assert "/packages/generic/modelgate-model/0.1.0/model.pkl" in gitlab.uploads
    assert promotion["release"] == {
        "tag": "model-v0.1.0",
        "url": "https://gitlab.example/ns/modelgate/-/releases/model-v0.1.0",
        "status": "created",
    }
    payload = gitlab.release_payload or {}
    assert payload["tag_name"] == "model-v0.1.0" and payload["ref"] == SHA
    assert "merge request !5" in payload["description"]
    assert "# Model card: ModelGate candidate 0.1.0-rc.17" in payload["description"]
    assert {link["name"] for link in payload["assets"]["links"]} == {
        "Pipeline",
        "Model card",
        "Model file (model.pkl)",
    }
    assert promotion["cumulative_sci"] == {
        "gco2e": 0.05,
        "candidates": 1,
        "measured": 1,
        "branch": "bootstrap-champion",
        "previous_champion": None,
    }
    assert read_json(main_pipeline.reports / "promotion.json")["release"]["status"] == "created"

    deployment = promote.deploy(main_pipeline)
    assert (deployment["environment"], deployment["version"]) == ("production", "0.1.0")


def test_rerun_finds_the_existing_release(main_pipeline: Paths) -> None:
    promotion = promote.main(main_pipeline, http=FakeGitLab(release_status=409))
    assert promotion["release"]["status"] == "exists"


def test_unreproduced_metrics_block_the_release(main_pipeline: Paths) -> None:
    metrics_file = main_pipeline.reports / "metrics.json"
    metrics = read_json(metrics_file)
    metrics["overall"]["auc"] = 0.5
    write_json(metrics_file, metrics)
    gitlab = FakeGitLab()
    with pytest.raises(promote.PromotionError, match="does not reproduce"):
        promote.main(main_pipeline, http=gitlab)
    assert gitlab.release_payload is None


def test_cumulative_counts_earlier_rejections_on_the_branch(main_pipeline: Paths) -> None:
    decisions = main_pipeline.models / "decisions"
    rejected = read_json(next(decisions.glob("*.json")))
    rejected["candidate_version"] = "0.1.0-rc.15"
    rejected["verdict"] = "NO_GO"
    rejected["sci"]["sci_gco2e_per_candidate"] = 0.07
    write_json(decisions / "0.1.0-rc.15.json", rejected)
    other = dict(rejected, candidate_version="0.1.0-rc.16")
    other["lineage"] = dict(rejected["lineage"], branch="someone-else")
    write_json(decisions / "0.1.0-rc.16.json", other)
    result = promote.cumulative_sci(main_pipeline, read_json(main_pipeline.champion_file))
    assert (result["gco2e"], result["candidates"]) == (0.12, 2)


def test_site_is_static_and_complete(main_pipeline: Paths) -> None:
    history = [
        {
            "date": f"2026-10-2{i}",
            "champion_version": "0.1.0",
            "holdout_auc": 0.8397,
            "drift": {"auc": 0.83 - i / 100},
            "max_psi": 0.05 * i,
            "psi": {"income": 0.05 * i, "utilization": 0.01},
            "alert": i > 2,
        }
        for i in range(1, 4)
    ]
    write_json(main_pipeline.reports / "drift_history.json", history)
    data = site.main(main_pipeline, http=lambda *a: Response(404, None, ""))
    out = main_pipeline.root / "public"
    page = (out / "index.html").read_text(encoding="utf-8")
    assert data["champion"]["version"] == "0.1.0"
    assert "0.1.0-rc.17" in page and "GO</span>" in page and "<svg" in page
    assert "2026-10-23" in page and "alert" in page
    assert not re.search(r"<(script|link|img)\b", page)  # nothing loaded from anywhere
    assert (out / "cards" / "0.1.0-rc.17.md").exists()
    assert json.loads((out / "data.json").read_text(encoding="utf-8"))["decisions"]


def test_site_without_any_candidate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CI_JOB_TOKEN", raising=False)
    paths = Paths(tmp_path)
    shutil.rmtree(tmp_path, ignore_errors=True)
    tmp_path.mkdir()
    site.main(paths)
    page = (tmp_path / "public" / "index.html").read_text(encoding="utf-8")
    assert "No champion yet" in page and "No drift checks yet" in page


def test_cards_are_rendered_as_html(main_pipeline: Paths) -> None:
    site.main(main_pipeline, http=lambda *a: Response(404, None, ""))
    page = (main_pipeline.root / "public" / "cards" / "0.1.0-rc.17.html").read_text("utf-8")
    assert "<h1>Model card: ModelGate candidate 0.1.0-rc.17</h1>" in page
    assert "<table>" in page and "<th>Metric</th>" in page
    assert "<code>slice_gap</code>" in page
    assert "modelgate:analysis" not in page  # the markers are comments, not content
    assert not re.search(r"<(script|link|img)\b", page)
    index = (main_pipeline.root / "public" / "index.html").read_text("utf-8")
    assert "cards/0.1.0-rc.17.html" in index
    assert "Carbon to reach champion 0.1.0: 0.05 gCO2e over 1 candidate(s)" in index


def test_markdown_subset() -> None:
    html = site.markdown_to_html(
        "# Title\n\nSome **bold** and `code`, see https://example.com/x.\n\n"
        "| A | B |\n| --- | --- |\n| 1 | <2> |\n\n- one\n- two\n<!-- hidden -->\n"
    )
    assert "<h1>Title</h1>" in html
    assert "<strong>bold</strong>" in html and "<code>code</code>" in html
    assert '<a href="https://example.com/x">https://example.com/x</a>.' in html
    assert "<td>&lt;2&gt;</td>" in html  # cell text is escaped
    assert "<ul><li>one</li><li>two</li></ul>" in html
    assert "hidden" not in html


def test_carbon_by_verdict_and_avoided_retrains() -> None:
    def decision(verdict: str, grams: float | None) -> dict:
        return {
            "verdict": verdict,
            "sci": None if grams is None else {"sci_gco2e_per_candidate": grams},
        }

    split = site.carbon_by_verdict(
        [
            decision("GO", 0.05),
            decision("NO_GO", 0.04),
            decision("NEEDS_REVIEW", 0.06),
            decision("NO_GO", 0.05),
            decision("NO_GO", None),
        ]
    )
    assert split["gco2e_by_verdict"] == {"GO": 0.05, "NEEDS_REVIEW": 0.06, "NO_GO": 0.09}
    assert split["candidates_by_verdict"] == {"GO": 1, "NEEDS_REVIEW": 1, "NO_GO": 2}
    assert split["rejected_candidates"] == 3
    # 0.04 + 0.06 + 0.05 = 0.15 gCO2e of promotion retraining avoided
    assert split["avoided_promotion_retrain_gco2e"] == 0.15
    assert site.carbon_by_verdict([decision("GO", 0.05)])["avoided_promotion_retrain_gco2e"] is None


def test_flow_credits_appear_once_measured(main_pipeline: Paths) -> None:
    sci_yaml = main_pipeline.config / "sci.yaml"
    sci_yaml.write_text(
        sci_yaml.read_text(encoding="utf-8").replace(
            "flow_credits_per_run: null", "flow_credits_per_run: 3.5"
        ),
        encoding="utf-8",
    )
    site.main(main_pipeline, http=lambda *a: Response(404, None, ""))
    page = (main_pipeline.root / "public" / "index.html").read_text("utf-8")
    assert "about 3.5 GitLab Credits" in page
