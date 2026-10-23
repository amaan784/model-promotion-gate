"""Shared paths, config loading, deterministic JSON output, run context and HTTP."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# Metrics are stored and compared at this many decimals. Rounding before the
# gate runs means anyone recomputing the verdict from metrics.json gets the
# same answer, and tiny cross-platform float differences do not flip a rule.
METRIC_DECIMALS = 4


@dataclass(frozen=True)
class Paths:
    """The standard ModelGate directories and the champion record, all under one root.

    Other locations, such as requirements.txt or the public site directory, are built by
    callers directly from root.
    """

    root: Path

    @property
    def config(self) -> Path:
        """The config directory, <root>/config."""
        return self.root / "config"

    @property
    def data(self) -> Path:
        """The data directory, <root>/data."""
        return self.root / "data"

    @property
    def models(self) -> Path:
        """The models directory, <root>/models."""
        return self.root / "models"

    @property
    def challenger(self) -> Path:
        """The challenger model directory, <root>/models/challenger."""
        return self.models / "challenger"

    @property
    def champion_file(self) -> Path:
        """The champion record, <root>/models/champion.json."""
        return self.models / "champion.json"

    @property
    def reports(self) -> Path:
        """The reports directory, <root>/reports."""
        return self.root / "reports"


def default_paths() -> Paths:
    """Return Paths rooted at MODELGATE_ROOT, or the current directory when it is unset.

    The root is resolved to an absolute path.
    """
    return Paths(Path(os.environ.get("MODELGATE_ROOT", Path.cwd())).resolve())


def load_yaml(path: Path) -> dict[str, Any]:
    """Load a UTF-8 YAML file with safe_load and return its top-level mapping.

    Raises ValueError when the top level is not a mapping, including an empty file.
    """
    with path.open(encoding="utf-8") as fh:
        loaded = yaml.safe_load(fh)
    if not isinstance(loaded, dict):
        raise ValueError(f"{path}: expected a mapping at top level")
    return loaded


def read_json(path: Path) -> dict[str, Any]:
    """Load a UTF-8 JSON file and return its top-level object.

    Raises ValueError when the top level is not a JSON object.
    """
    with path.open(encoding="utf-8") as fh:
        loaded = json.load(fh)
    if not isinstance(loaded, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return loaded


def read_json_if_exists(path: Path) -> dict[str, Any] | None:
    """Like read_json, but return None when the file does not exist."""
    return read_json(path) if path.exists() else None


def write_json(path: Path, obj: Any) -> None:
    """Write obj as sorted, 2-space indented UTF-8 JSON with LF line endings.

    Creates missing parent directories, overwrites any existing file and ends the text with a
    newline, so the same object always produces the same bytes.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, indent=2, sort_keys=True) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")


def sha256_file(path: Path) -> str:
    """Hex SHA-256 digest of a file's bytes, read in 1 MiB chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def round_metric(value: float) -> float:
    """Convert value to float and round it to METRIC_DECIMALS decimal places."""
    return round(float(value), METRIC_DECIMALS)


def _git(*args: str) -> str | None:
    try:
        out = subprocess.run(["git", *args], capture_output=True, text=True, check=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


def run_context() -> dict[str, Any]:
    """Return lineage for this run from GitLab CI variables, with a local git fallback.

    The keys are commit, ref, pipeline_url, pipeline_iid and job_url. Only commit and ref fall
    back to local git when their CI variable is unset or empty; the other keys are None
    outside CI, and commit or ref is None when git fails or prints nothing.
    """
    return {
        "commit": os.environ.get("CI_COMMIT_SHA") or _git("rev-parse", "HEAD"),
        "ref": os.environ.get("CI_COMMIT_REF_NAME") or _git("rev-parse", "--abbrev-ref", "HEAD"),
        "pipeline_url": os.environ.get("CI_PIPELINE_URL"),
        "pipeline_iid": os.environ.get("CI_PIPELINE_IID"),
        "job_url": os.environ.get("CI_JOB_URL"),
    }


# ------------------------------------------------------------------------------ http

HTTP_TIMEOUT_S = 30


@dataclass(frozen=True)
class Response:
    """The result of one HTTP call: status code, parsed body, text and raw bytes.

    As built by urllib_http: body is the parsed JSON, or None when the response is empty
    or not JSON; text is the first 300 characters of the body decoded as UTF-8, with
    undecodable bytes replaced; raw is the full undecoded body. On status 0 body is None,
    text is the error reason and raw is empty.
    """

    status: int
    body: Any
    text: str
    raw: bytes = b""


Http = Callable[[str, str, dict[str, str], bytes | None], Response]


def urllib_http(method: str, url: str, headers: dict[str, str], data: bytes | None) -> Response:
    """Make one HTTP call with a timeout of HTTP_TIMEOUT_S seconds and return a Response.

    HTTP error statuses such as 4xx and 5xx are returned, not raised. A URLError, such as a
    DNS or connection failure, is returned as status 0 with the reason in text. Other
    exceptions, such as a timeout while waiting for or reading the response, propagate.
    """
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_S) as resp:
            status, raw = resp.status, resp.read()
    except urllib.error.HTTPError as err:
        status, raw = err.code, err.read()
    except urllib.error.URLError as err:
        return Response(0, None, str(err.reason))
    text = raw.decode("utf-8", "replace")
    try:
        body = json.loads(text) if text else None
    except json.JSONDecodeError:
        body = None
    return Response(status, body, text[:300], raw)
