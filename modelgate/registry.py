"""Register the challenger as <proposed version>-rc.<pipeline IID>.

GitLab documents CI_JOB_TOKEN access to the Packages API, but its Model Registry
(MLflow-compatible) API documents only personal, project or group access tokens.
ModelGate has no such token because it creates no CI/CD variables, so:

1. Model Registry: create the model if needed and a model version through the
   MLflow REST API with CI_JOB_TOKEN, trying each way of presenting the token.
   Metrics and params go on the version's run; files go to the version's package.
2. Fallback: upload the same files to the generic package registry
   (modelgate-model/<version>/), which documents CI_JOB_TOKEN support.

reports/registry.json records which path worked and the HTTP status of every
attempt. The token itself is never written anywhere.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from modelgate.common import Http, Paths, Response, read_json, urllib_http, write_json

logger = logging.getLogger(__name__)

MODEL_NAME = "modelgate"
GENERIC_PACKAGE = "modelgate-model"
UPLOADS = (
    "models/challenger/model.pkl",
    "models/challenger/meta.json",
    "reports/metrics.json",
    "reports/gate_report.json",
    "reports/scan.json",
    "reports/sci.json",
)


class RegistryError(RuntimeError):
    """Raised when a registration step fails or gets a response it cannot use.

    main also raises it, after writing the record, when both the Model Registry and the
    generic package upload fail.
    """

    pass


@dataclass(frozen=True)
class CiEnv:
    """The GitLab CI variables needed to register a model, read from the environment."""

    api_url: str
    project_id: str
    token: str
    pipeline_iid: str
    job_id: str
    commit: str

    @classmethod
    def from_environ(cls) -> CiEnv | None:
        """Read the CI variables, or return None when CI_JOB_TOKEN is not set or empty.

        Raises KeyError when the token is set but CI_API_V4_URL, CI_PROJECT_ID or
        CI_PIPELINE_IID is missing. CI_JOB_ID and CI_COMMIT_SHA default to empty strings,
        and trailing slashes are stripped from the API URL.
        """
        token = os.environ.get("CI_JOB_TOKEN")
        if not token:
            return None
        return cls(
            api_url=os.environ["CI_API_V4_URL"].rstrip("/"),
            project_id=os.environ["CI_PROJECT_ID"],
            token=token,
            pipeline_iid=os.environ["CI_PIPELINE_IID"],
            job_id=os.environ.get("CI_JOB_ID", ""),
            commit=os.environ.get("CI_COMMIT_SHA", ""),
        )


def auth_styles(token: str) -> list[tuple[str, dict[str, str]]]:
    """Return (label, headers) pairs for each way of presenting the job token, in try order.

    The forms are a JOB-TOKEN header, a Bearer Authorization header and HTTP Basic auth
    with the user name gitlab-ci-token.
    """
    basic = base64.b64encode(f"gitlab-ci-token:{token}".encode()).decode()
    return [
        ("JOB-TOKEN header", {"JOB-TOKEN": token}),
        ("Bearer", {"Authorization": f"Bearer {token}"}),
        ("Basic gitlab-ci-token", {"Authorization": f"Basic {basic}"}),
    ]


@dataclass
class Session:
    """An HTTP client for one CI environment that records every call it makes."""

    http: Http
    env: CiEnv
    attempts: list[dict[str, Any]] = field(default_factory=list)

    def call(
        self,
        step: str,
        auth: str,
        method: str,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any] | None = None,
        raw: bytes | None = None,
    ) -> Response:
        """Make one HTTP request, record it in attempts and return the response.

        When payload is given it is sent as JSON with a Content-Type header and raw is
        ignored; otherwise raw is the request body. Each attempt records the step, auth
        label, method, status and the URL with the API base URL prefix removed (any query
        string is kept, and a URL that does not start with the API base URL is recorded in
        full). Headers are not recorded, so the token never reaches attempts. The response
        is returned whatever its status; exceptions raised by http propagate unrecorded.
        """
        hdrs = dict(headers)
        data = raw
        if payload is not None:
            hdrs["Content-Type"] = "application/json"
            data = json.dumps(payload).encode()
        resp = self.http(method, url, hdrs, data)
        self.attempts.append(
            {
                "step": step,
                "auth": auth,
                "method": method,
                "path": url.removeprefix(self.env.api_url),
                "status": resp.status,
            }
        )
        return resp


def _error_code(resp: Response) -> str:
    return str((resp.body or {}).get("error_code", "")) if isinstance(resp.body, dict) else ""


def _run_payload(gate: dict[str, Any], env: CiEnv, run_id: str) -> dict[str, Any]:
    metrics = gate["challenger"]["metrics"]
    overall = metrics["overall"]
    now_ms = int(time.time() * 1000)
    values = {k: overall[k] for k in ("auc", "f1", "accuracy", "brier", "ece")}
    values["segment_auc_gap"] = metrics["segment_auc_gap"]
    for name, seg in metrics["segments"].items():
        values[f"segment_{name}_auc"] = seg["auc"]
    config = gate["challenger"]["config"]
    params = {
        "model_family": config["model_family"],
        "output_schema": config["output_schema"],
        "features": ",".join(config["features"]),
        "seed": str(config["seed"]),
        "hyperparams": json.dumps(config["hyperparams"], sort_keys=True),
    }
    tags = {
        "gitlab.CI_JOB_ID": env.job_id,
        "modelgate.verdict": gate["verdict"],
        "modelgate.commit": env.commit,
    }
    return {
        "run_id": run_id,
        "metrics": [
            {"key": k, "value": v, "timestamp": now_ms, "step": 0}
            for k, v in values.items()
            if v is not None
        ],
        "params": [{"key": k, "value": v} for k, v in params.items()],
        "tags": [{"key": k, "value": v} for k, v in tags.items() if v],
    }


def register_model_registry(
    s: Session, version: str, files: list[Path], gate: dict[str, Any]
) -> dict[str, Any]:
    """Register version in the GitLab Model Registry through its MLflow REST API.

    Tries to create the registered model with each auth style in turn, treating a
    RESOURCE_ALREADY_EXISTS error as success, and uses the first style that works for the
    remaining MLflow calls. It then creates a model version tagged with version, logs the
    gate report's metrics and params, plus verdict, CI job ID and commit tags, to the
    version's run, reads the run's artifact URI and PUTs each file there with the JOB-TOKEN
    header. Returns the registration record, whose registered_version is the version
    number MLflow assigned, or version when none is returned. Raises RegistryError when
    every auth style is rejected or any later step fails.
    """
    env = s.env
    base = f"{env.api_url}/projects/{env.project_id}/ml/mlflow/api/2.0/mlflow"
    chosen: tuple[str, dict[str, str]] | None = None
    for name, headers in auth_styles(env.token):
        resp = s.call(
            "create_model",
            name,
            "POST",
            f"{base}/registered-models/create",
            headers,
            {"name": MODEL_NAME, "description": "ModelGate challenger models"},
        )
        exists = resp.status in (400, 409) and _error_code(resp) == "RESOURCE_ALREADY_EXISTS"
        if resp.status == 200 or exists:
            chosen = (name, headers)
            break
    if chosen is None:
        raise RegistryError("the MLflow API rejected CI_JOB_TOKEN in every form tried")
    auth, headers = chosen

    resp = s.call(
        "create_version",
        auth,
        "POST",
        f"{base}/model-versions/create",
        headers,
        {
            "name": MODEL_NAME,
            "source": "",
            "description": f"{gate['verdict']} candidate from pipeline {env.pipeline_iid}",
            "tags": [{"key": "gitlab.version", "value": version}],
        },
    )
    model_version = (resp.body or {}).get("model_version") if resp.status == 200 else None
    run_id = (model_version or {}).get("run_id")
    if not run_id:
        raise RegistryError(f"model-versions/create returned {resp.status}: {resp.text}")

    resp = s.call(
        "log_batch",
        auth,
        "POST",
        f"{base}/runs/log-batch",
        headers,
        _run_payload(gate, env, run_id),
    )
    if resp.status != 200:
        raise RegistryError(f"runs/log-batch returned {resp.status}: {resp.text}")

    query = urllib.parse.urlencode({"run_id": run_id})
    resp = s.call("get_run", auth, "GET", f"{base}/runs/get?{query}", headers)
    try:
        artifact_uri = str(resp.body["run"]["info"]["artifact_uri"]).rstrip("/")
    except (KeyError, TypeError) as exc:
        raise RegistryError(f"runs/get returned {resp.status}: {resp.text}") from exc
    if not artifact_uri.startswith("http"):
        raise RegistryError(f"unexpected artifact_uri {artifact_uri!r}")

    # The version's artifact URI is a Packages API endpoint, documented for job tokens.
    job_headers = {"JOB-TOKEN": env.token}
    for path in files:
        resp = s.call(
            "upload_file",
            "JOB-TOKEN header",
            "PUT",
            f"{artifact_uri}/{urllib.parse.quote(path.name)}",
            job_headers,
            raw=path.read_bytes(),
        )
        if resp.status not in (200, 201):
            raise RegistryError(f"upload of {path.name} returned {resp.status}: {resp.text}")
    return {
        "method": "model_registry",
        "model": MODEL_NAME,
        "registered_version": str((model_version or {}).get("version") or version),
        "run_id": run_id,
        "auth": auth,
        "artifact_uri": artifact_uri,
        "files": [p.name for p in files],
    }


def register_generic(s: Session, version: str, files: list[Path]) -> dict[str, Any]:
    """Upload files to the generic package modelgate-model/<version> with the job token.

    Returns the registration record. Raises RegistryError as soon as an upload returns a
    status other than 200 or 201.
    """
    env = s.env
    package_url = (
        f"{env.api_url}/projects/{env.project_id}/packages/generic/"
        f"{GENERIC_PACKAGE}/{urllib.parse.quote(version, safe='')}"
    )
    headers = {"JOB-TOKEN": env.token}
    for path in files:
        resp = s.call(
            "generic_upload",
            "JOB-TOKEN header",
            "PUT",
            f"{package_url}/{urllib.parse.quote(path.name)}",
            headers,
            raw=path.read_bytes(),
        )
        if resp.status not in (200, 201):
            raise RegistryError(f"generic upload of {path.name} returned {resp.status}")
    return {
        "method": "generic_package",
        "package": GENERIC_PACKAGE,
        "registered_version": version,
        "files": [p.name for p in files],
    }


def main(paths: Paths, http: Http = urllib_http) -> dict[str, Any]:
    """Register the challenger, write reports/registry.json and return the record.

    Outside GitLab CI (CI_JOB_TOKEN not set or empty) it writes and returns a "skipped"
    record. Otherwise the version is <proposed version>-rc.<pipeline IID> from
    reports/gate_report.json, and the UPLOADS files that exist are sent to the Model
    Registry, falling back to the generic package when that raises RegistryError (the
    error is kept in the record as model_registry_error). The record includes every HTTP
    attempt. When both paths fail the record is still written, with method "failed", and
    RegistryError is raised. Other exceptions, such as a missing CI variable or an HTTP
    timeout, propagate before the record is written.
    """
    out = paths.reports / "registry.json"
    env = CiEnv.from_environ()
    if env is None:
        record: dict[str, Any] = {
            "method": "skipped",
            "reason": "not running in GitLab CI: CI_JOB_TOKEN is not set",
        }
        write_json(out, record)
        logger.info("registry: skipped (not in GitLab CI)")
        return record

    gate = read_json(paths.reports / "gate_report.json")
    version = f"{gate['proposed_version_bump']['to']}-rc.{env.pipeline_iid}"
    files = [paths.root / f for f in UPLOADS if (paths.root / f).exists()]
    session = Session(http, env)
    record = {"version": version, "verdict": gate["verdict"], "commit": env.commit}
    try:
        record |= register_model_registry(session, version, files, gate)
    except RegistryError as exc:
        logger.warning("model registry failed: %s; falling back to generic package", exc)
        record["model_registry_error"] = str(exc)
        try:
            record |= register_generic(session, version, files)
        except RegistryError as exc2:
            record |= {"method": "failed", "generic_package_error": str(exc2)}
    record["attempts"] = session.attempts
    write_json(out, record)
    logger.info("registry: %s as %s", record["method"], version)
    for attempt in session.attempts:
        logger.info(
            "  %-15s %-22s %s %s",
            attempt["step"],
            attempt["auth"],
            attempt["status"],
            attempt["path"],
        )
    if record["method"] == "failed":
        raise RegistryError("both the model registry and the generic package upload failed")
    return record
