"""The flow's default-branch step: find the merged merge request and build its note."""

from __future__ import annotations

import json
from typing import Any

from modelgate import flow
from modelgate.common import Response

API = "https://gitlab.example/api/v4/projects/42"
TARGET = flow.Target("https://gitlab.example/ns/modelgate", "42", 2000, 0, "main", "f" * 40, "main")
PROMOTION = {
    "version": "0.2.0",
    "candidate_version": "0.2.0-rc.17",
    "commit": "f" * 40,
    "pipeline_url": "https://gitlab.example/ns/modelgate/-/pipelines/2000",
    "release": {
        "tag": "model-v0.2.0",
        "url": "https://gitlab.example/ns/modelgate/-/releases/model-v0.2.0",
    },
    "registry": {
        "method": "generic_package",
        "package": "modelgate-model",
        "registered_version": "0.2.0",
    },
    "cumulative_sci": {"gco2e": 0.15, "candidates": 3},
}
DEPLOYMENT = {"environment": "production", "job_url": "https://gitlab.example/-/jobs/9"}


def api(
    merge_requests: list[dict[str, Any]],
    jobs: list[dict[str, Any]],
    notes: list[dict[str, Any]] | None = None,
) -> Any:
    def http(method: str, url: str, headers: dict, data: bytes | None) -> Response:
        if url == f"{API}/repository/commits/{'f' * 40}/merge_requests":
            return Response(200, merge_requests, "")
        if url == f"{API}/pipelines/2000/jobs?per_page=100":
            return Response(200, jobs, "")
        if url == f"{API}/jobs/31/artifacts/reports/promotion.json":
            raw = json.dumps(PROMOTION).encode()
            return Response(200, PROMOTION, "", raw)
        if url == f"{API}/jobs/32/artifacts/reports/deployment.json":
            raw = json.dumps(DEPLOYMENT).encode()
            return Response(200, DEPLOYMENT, "", raw)
        if url == f"{API}/merge_requests/5/notes?per_page=100":
            return Response(200, notes or [], "")
        return Response(404, None, "")

    return http


MERGED = [{"iid": 5, "state": "merged", "target_branch": "main"}]
PROMOTED_JOBS = [
    {"id": 30, "name": "train_evaluate", "status": "success"},
    {"id": 31, "name": "promote", "status": "success"},
    {"id": 32, "name": "deploy_production", "status": "success"},
]


def test_direct_push_has_no_merge_request() -> None:
    result = flow.promotion_note(TARGET, api([], PROMOTED_JOBS))
    assert result["status"] == "no_merge_request"


def test_merge_without_promote_job() -> None:
    jobs = [{"id": 30, "name": "train_evaluate", "status": "success"}]
    result = flow.promotion_note(TARGET, api(MERGED, jobs))
    assert (result["status"], result["merge_request_iid"]) == ("no_promotion", 5)


def test_note_names_release_registry_and_deployment() -> None:
    result = flow.promotion_note(TARGET, api(MERGED, PROMOTED_JOBS))
    assert result["status"] == "note"
    assert result["merge_request_iid"] == 5
    body = result["body"]
    assert body.startswith(
        "Promotion complete: Release [model-v0.2.0]"
        "(https://gitlab.example/ns/modelgate/-/releases/model-v0.2.0), "
        "registry version generic package `modelgate-model` 0.2.0, "
        "deployment [production](https://gitlab.example/-/jobs/9)."
    )
    assert "0.15 gCO2e over 3 evaluated candidate(s)" in body


def test_note_is_not_posted_twice() -> None:
    notes = [{"body": "Promotion complete: Release ..."}]
    result = flow.promotion_note(TARGET, api(MERGED, PROMOTED_JOBS, notes))
    assert result["status"] == "already_posted"
