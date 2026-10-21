"""Registry paths against a fake GitLab API: job-token MLflow path, fallback, failure."""

from __future__ import annotations

import json
from typing import Any

import pytest

from modelgate import registry
from modelgate.common import Paths, write_json
from modelgate.registry import RegistryError, Response

TOKEN = "glcbt-secret-token-value"
API = "https://gitlab.example/api/v4"
MLFLOW = "/projects/42/ml/mlflow/api/2.0/mlflow"
ARTIFACT_URI = f"{API}/projects/42/packages/ml_models/7/files"


class FakeGitLab:
    def __init__(
        self, mlflow_auth: str | None, model_exists: bool = False, generic_ok: bool = True
    ):
        self.mlflow_auth = mlflow_auth  # header name the MLflow API accepts, or None
        self.model_exists = model_exists
        self.generic_ok = generic_ok
        self.uploaded: dict[str, bytes] = {}

    def __call__(self, method: str, url: str, headers: dict, data: bytes | None) -> Response:
        path = url.removeprefix(API)
        if path.startswith(MLFLOW):
            authed = (self.mlflow_auth == "JOB-TOKEN" and headers.get("JOB-TOKEN") == TOKEN) or (
                self.mlflow_auth == "Bearer" and headers.get("Authorization") == f"Bearer {TOKEN}"
            )
            if not authed:
                return Response(401, {"message": "401 Unauthorized"}, "")
            endpoint = path.removeprefix(MLFLOW)
            if endpoint == "/registered-models/create":
                if self.model_exists:
                    return Response(400, {"error_code": "RESOURCE_ALREADY_EXISTS"}, "")
                return Response(200, {"registered_model": {"name": "modelgate"}}, "")
            if endpoint == "/model-versions/create":
                body = json.loads(data or b"{}")
                version = body["tags"][0]["value"]
                return Response(200, {"model_version": {"version": version, "run_id": "r1"}}, "")
            if endpoint == "/runs/log-batch":
                return Response(200, {}, "")
            if endpoint.startswith("/runs/get"):
                return Response(200, {"run": {"info": {"artifact_uri": ARTIFACT_URI}}}, "")
            return Response(404, None, "")
        if url.startswith(ARTIFACT_URI) or "/packages/generic/" in path:
            if headers.get("JOB-TOKEN") != TOKEN:
                return Response(401, None, "")
            if "/packages/generic/" in path and not self.generic_ok:
                return Response(403, None, "")
            self.uploaded[path] = data or b""
            return Response(201, {"message": "201 Created"}, "")
        return Response(404, None, "")


GATE: dict[str, Any] = {
    "verdict": "GO",
    "proposed_version_bump": {"to": "0.2.0"},
    "challenger": {
        "config": {
            "model_family": "logistic_regression",
            "output_schema": "s",
            "features": ["income"],
            "seed": 1,
            "hyperparams": {"C": 1.0},
        },
        "metrics": {
            "overall": {"auc": 0.87, "f1": 0.6, "accuracy": 0.8, "brier": 0.13, "ece": 0.01},
            "segments": {"A": {"auc": 0.88}, "B": {"auc": 0.87}},
            "segment_auc_gap": 0.01,
        },
    },
}


@pytest.fixture
def ci_repo(tmp_path, monkeypatch: pytest.MonkeyPatch) -> Paths:
    for key, value in {
        "CI_JOB_TOKEN": TOKEN,
        "CI_API_V4_URL": API,
        "CI_PROJECT_ID": "42",
        "CI_PIPELINE_IID": "17",
        "CI_JOB_ID": "9001",
        "CI_COMMIT_SHA": "abc123",
    }.items():
        monkeypatch.setenv(key, value)
    paths = Paths(tmp_path)
    write_json(paths.reports / "gate_report.json", GATE)
    (paths.challenger).mkdir(parents=True)
    (paths.challenger / "model.pkl").write_bytes(b"model-bytes")
    return paths


def _record_text(paths: Paths) -> str:
    return (paths.reports / "registry.json").read_text(encoding="utf-8")


def test_job_token_rejected_falls_back_to_generic_package(ci_repo: Paths) -> None:
    gitlab = FakeGitLab(mlflow_auth=None)
    record = registry.main(ci_repo, http=gitlab)
    assert record["method"] == "generic_package"
    assert record["version"] == "0.2.0-rc.17"
    assert [a["status"] for a in record["attempts"] if a["step"] == "create_model"] == [401] * 3
    assert "/projects/42/packages/generic/modelgate-model/0.2.0-rc.17/model.pkl" in gitlab.uploaded
    assert TOKEN not in _record_text(ci_repo)


def test_job_token_accepted_uses_model_registry(ci_repo: Paths) -> None:
    gitlab = FakeGitLab(mlflow_auth="JOB-TOKEN")
    record = registry.main(ci_repo, http=gitlab)
    assert record["method"] == "model_registry"
    assert record["auth"] == "JOB-TOKEN header"
    assert record["registered_version"] == "0.2.0-rc.17"
    assert f"{ARTIFACT_URI.removeprefix(API)}/model.pkl" in gitlab.uploaded
    assert TOKEN not in _record_text(ci_repo)


def test_second_auth_style_and_existing_model(ci_repo: Paths) -> None:
    record = registry.main(ci_repo, http=FakeGitLab(mlflow_auth="Bearer", model_exists=True))
    assert record["method"] == "model_registry"
    assert record["auth"] == "Bearer"
    statuses = [a["status"] for a in record["attempts"] if a["step"] == "create_model"]
    assert statuses == [401, 400]


def test_both_paths_failing_raises_and_records(ci_repo: Paths) -> None:
    with pytest.raises(RegistryError):
        registry.main(ci_repo, http=FakeGitLab(mlflow_auth=None, generic_ok=False))
    assert json.loads(_record_text(ci_repo))["method"] == "failed"


def test_outside_ci_is_skipped(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CI_JOB_TOKEN", raising=False)
    record = registry.main(Paths(tmp_path), http=FakeGitLab(mlflow_auth=None))
    assert record["method"] == "skipped"
