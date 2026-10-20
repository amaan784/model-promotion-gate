"""Shared paths, config loading, deterministic JSON output and run context."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
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
    """Every file location ModelGate reads or writes, relative to one root."""

    root: Path

    @property
    def config(self) -> Path:
        return self.root / "config"

    @property
    def data(self) -> Path:
        return self.root / "data"

    @property
    def models(self) -> Path:
        return self.root / "models"

    @property
    def challenger(self) -> Path:
        return self.models / "challenger"

    @property
    def champion_file(self) -> Path:
        return self.models / "champion.json"

    @property
    def reports(self) -> Path:
        return self.root / "reports"


def default_paths() -> Paths:
    """Repo root from MODELGATE_ROOT, else the current directory."""
    return Paths(Path(os.environ.get("MODELGATE_ROOT", Path.cwd())).resolve())


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        loaded = yaml.safe_load(fh)
    if not isinstance(loaded, dict):
        raise ValueError(f"{path}: expected a mapping at top level")
    return loaded


def read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        loaded = json.load(fh)
    if not isinstance(loaded, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return loaded


def read_json_if_exists(path: Path) -> dict[str, Any] | None:
    return read_json(path) if path.exists() else None


def write_json(path: Path, obj: Any) -> None:
    """Write sorted, indented JSON with LF line endings so output is byte-stable."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, indent=2, sort_keys=True) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def round_metric(value: float) -> float:
    return round(float(value), METRIC_DECIMALS)


def _git(*args: str) -> str | None:
    try:
        out = subprocess.run(["git", *args], capture_output=True, text=True, check=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


def run_context() -> dict[str, Any]:
    """Lineage for this run: CI variables when present, local git otherwise."""
    return {
        "commit": os.environ.get("CI_COMMIT_SHA") or _git("rev-parse", "HEAD"),
        "ref": os.environ.get("CI_COMMIT_REF_NAME") or _git("rev-parse", "--abbrev-ref", "HEAD"),
        "pipeline_url": os.environ.get("CI_PIPELINE_URL"),
        "pipeline_iid": os.environ.get("CI_PIPELINE_IID"),
        "job_url": os.environ.get("CI_JOB_URL"),
    }
