from __future__ import annotations

import json
import os
import pickle
import subprocess
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pytest
from conftest import REPO
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from modelgate.evaluate import Thresholds, rule_scan_clean
from modelgate.scan import audit_dependencies, lookup_severity, scan_model

THRESHOLDS = Thresholds.from_yaml(REPO / "config" / "thresholds.yaml")


class _Payload:
    """Unpickling this would run a shell command; it is only ever scanned."""

    def __reduce__(self) -> tuple[Any, tuple[str]]:
        return (os.system, ("echo pwned",))


@pytest.fixture(scope="module")
def fitted() -> Any:
    rng = np.random.default_rng(0)
    x = rng.normal(size=(60, 3))
    y = (x[:, 0] > 0).astype(int)
    return make_pipeline(StandardScaler(), LogisticRegression()).fit(x, y)


def _write(path: Path, obj: Any) -> Path:
    path.write_bytes(pickle.dumps(obj, protocol=5))
    return path


def test_sklearn_pickle_is_clean(tmp_path: Path, fitted: Any) -> None:
    result = scan_model(_write(tmp_path / "model.pkl", fitted))
    assert result["ran"] is True
    assert result["scan_error"] is False
    assert result["dangerous_globals"] == 0
    assert result["unexpected_globals"] == []
    assert any("LogisticRegression" in g for g in result["globals"])


def test_payload_is_dangerous_and_unexpected(tmp_path: Path, fitted: Any) -> None:
    result = scan_model(_write(tmp_path / "model.pkl", {"model": fitted, "x": _Payload()}))
    assert result["dangerous_globals"] == 1
    assert any(g.endswith(".system") for g in result["unexpected_globals"])


def test_joblib_dump_cannot_be_fully_parsed(tmp_path: Path, fitted: Any) -> None:
    # Why train.py writes plain pickle: picklescan stops at joblib's inline arrays.
    path = tmp_path / "model.joblib"
    joblib.dump(fitted, path)
    result = scan_model(path)
    assert result["scan_error"] is True
    assert not any("LogisticRegression" in g for g in result["globals"])


def test_missing_model_file_is_recorded(tmp_path: Path) -> None:
    result = scan_model(tmp_path / "absent.pkl")
    assert result["ran"] is False


def _audit_run(stdout: str, code: int = 1) -> Any:
    def run(requirements: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(["pip-audit"], code, stdout=stdout, stderr="")

    return run


AUDIT_JSON = json.dumps(
    {
        "dependencies": [
            {"name": "safe", "version": "1.0", "vulns": []},
            {
                "name": "certifi",
                "version": "2026.7.22",
                "vulns": [
                    {"id": "PYSEC-1", "aliases": ["GHSA-aaaa"], "fix_versions": ["2026.10.19"]},
                    {"id": "PYSEC-1", "aliases": ["GHSA-aaaa"], "fix_versions": ["2026.10.19"]},
                ],
            },
            {"name": "local", "version": "0", "skip_reason": "not on PyPI"},
        ],
        "fixes": [],
    }
)


def test_audit_dedupes_and_labels_severity() -> None:
    fetched: list[str] = []

    def fetch(url: str) -> dict[str, Any]:
        fetched.append(url)
        return {"database_specific": {"severity": "CRITICAL"}}

    result = audit_dependencies(Path("requirements.txt"), _audit_run(AUDIT_JSON), fetch)
    assert result["ran"] is True
    assert result["packages_audited"] == 3
    assert result["skipped"] == ["local"]
    assert len(result["vulnerabilities"]) == 1
    vuln = result["vulnerabilities"][0]
    assert (vuln["package"], vuln["severity"]) == ("certifi", "CRITICAL")
    assert fetched == ["https://api.osv.dev/v1/vulns/GHSA-aaaa"]  # the GHSA alias first
    verdict = rule_scan_clean(
        {"picklescan": {"ran": True, "scan_error": False}, "pip_audit": result},
        THRESHOLDS.params["scan_clean"],
    )
    assert verdict.status == "fail"


def test_audit_output_that_is_not_json_means_not_run() -> None:
    result = audit_dependencies(Path("r.txt"), _audit_run("Traceback ...", code=2), lambda u: {})
    assert result["ran"] is False


def test_severity_unknown_when_osv_unreachable() -> None:
    def fetch(url: str) -> dict[str, Any]:
        raise OSError("offline")

    assert lookup_severity("PYSEC-1", ["GHSA-aaaa"], fetch) == (
        "UNKNOWN",
        "no severity label found in OSV",
    )


def test_real_scan_output_passes_the_gate(tmp_path: Path, fitted: Any) -> None:
    clean_audit = audit_dependencies(
        Path("r.txt"), _audit_run(json.dumps({"dependencies": []}), code=0), lambda u: {}
    )
    scan = {"picklescan": scan_model(_write(tmp_path / "m.pkl", fitted)), "pip_audit": clean_audit}
    assert rule_scan_clean(scan, THRESHOLDS.params["scan_clean"]).status == "pass"
