"""Scan the challenger model file and the dependency lock; write reports/scan.json.

- picklescan parses the model pickle and lists every global it would import.
  Beyond picklescan's own "Dangerous" denylist, any global outside
  ALLOWED_MODULE_ROOTS counts as unexpected, and a parse error counts too,
  because globals after the error point are never seen.
- pip-audit checks requirements.txt (the full lock CI installs). pip-audit does not
  report severity, so each finding is looked up in OSV; the GitHub advisory's
  severity label (LOW, MODERATE, HIGH, CRITICAL) is used when one exists.

Tool failures never raise. They are recorded, and the gate's scan_clean rule
fails closed on them.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from modelgate.common import Paths, sha256_file, write_json

logger = logging.getLogger(__name__)

# Module roots a scikit-learn model pickle legitimately needs. `_loss` is the
# Cython module behind sklearn._loss, which pickles under its bare name.
ALLOWED_MODULE_ROOTS = frozenset({"sklearn", "numpy", "scipy", "_loss"})
OSV_VULN_URL = "https://api.osv.dev/v1/vulns/{id}"
HTTP_TIMEOUT_S = 15

Fetch = Callable[[str], dict[str, Any]]
RunAudit = Callable[[Path], subprocess.CompletedProcess[str]]


def scan_model(path: Path) -> dict[str, Any]:
    from picklescan.scanner import scan_file_path

    try:
        result = scan_file_path(str(path))
    except Exception as exc:  # picklescan raises on unreadable input
        return {"ran": False, "file": path.name, "error": f"{type(exc).__name__}: {exc}"}
    found = sorted({(g.module, g.name, g.safety.name) for g in result.globals})
    dangerous = [f"{m}.{n}" for m, n, safety in found if safety == "Dangerous"]
    unexpected = [f"{m}.{n}" for m, n, _ in found if m.split(".")[0] not in ALLOWED_MODULE_ROOTS]
    return {
        "ran": True,
        "file": path.name,
        "sha256": sha256_file(path),
        "scan_error": bool(result.scan_err),
        "dangerous_globals": len(dangerous),
        "dangerous": dangerous,
        "unexpected_globals": unexpected,
        "allowed_module_roots": sorted(ALLOWED_MODULE_ROOTS),
        "globals": [f"{m}.{n} ({safety})" for m, n, safety in found],
    }


def _run_pip_audit(requirements: Path) -> subprocess.CompletedProcess[str]:
    # --no-deps with --disable-pip audits the exact pinned lock without building a venv.
    cmd = [
        sys.executable,
        "-m",
        "pip_audit",
        "--requirement",
        str(requirements),
        "--no-deps",
        "--disable-pip",
        "--format",
        "json",
        "--progress-spinner",
        "off",
    ]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=300, check=False)


def _fetch_json(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT_S) as resp:
        return json.load(resp)


def lookup_severity(vuln_id: str, aliases: list[str], fetch: Fetch) -> tuple[str, str]:
    """Severity label from the first GitHub advisory OSV knows for this finding."""
    candidates = [a for a in [vuln_id, *aliases] if a.startswith("GHSA-")] + [vuln_id]
    for candidate in dict.fromkeys(candidates):
        try:
            record = fetch(OSV_VULN_URL.format(id=candidate))
        except Exception as exc:
            logger.warning("OSV lookup for %s failed: %s", candidate, exc)
            continue
        label = (record.get("database_specific") or {}).get("severity")
        if label:
            return str(label).upper(), f"OSV {candidate}"
    return "UNKNOWN", "no severity label found in OSV"


def audit_dependencies(
    requirements: Path, run: RunAudit = _run_pip_audit, fetch: Fetch = _fetch_json
) -> dict[str, Any]:
    try:
        proc = run(requirements)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ran": False, "error": f"{type(exc).__name__}: {exc}"}
    # pip-audit exits 1 when it finds vulnerabilities; only unparseable output is a failure.
    try:
        report = json.loads(proc.stdout)
    except json.JSONDecodeError:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-5:]
        return {"ran": False, "exit_code": proc.returncode, "error": " | ".join(tail)}
    dependencies = report.get("dependencies", [])
    findings: dict[tuple[str, str], dict[str, Any]] = {}
    for dep in dependencies:
        for vuln in dep.get("vulns") or []:
            key = (dep["name"], vuln["id"])
            if key in findings:  # pip-audit can list the same advisory twice
                continue
            aliases = sorted(set(vuln.get("aliases") or []))
            severity, source = lookup_severity(vuln["id"], aliases, fetch)
            findings[key] = {
                "package": dep["name"],
                "version": dep.get("version"),
                "id": vuln["id"],
                "aliases": aliases,
                "fix_versions": vuln.get("fix_versions") or [],
                "severity": severity,
                "severity_source": source,
            }
    return {
        "ran": True,
        "requirements": requirements.name,
        "packages_audited": len(dependencies),
        "skipped": [d["name"] for d in dependencies if d.get("skip_reason")],
        "vulnerabilities": list(findings.values()),
    }


def main(paths: Paths) -> dict[str, Any]:
    report = {
        "picklescan": scan_model(paths.challenger / "model.pkl"),
        "pip_audit": audit_dependencies(paths.root / "requirements.txt"),
    }
    write_json(paths.reports / "scan.json", report)
    pscan, audit = report["picklescan"], report["pip_audit"]
    logger.info(
        "picklescan: ran=%s scan_error=%s dangerous=%s unexpected=%s",
        pscan.get("ran"),
        pscan.get("scan_error"),
        pscan.get("dangerous_globals"),
        len(pscan.get("unexpected_globals") or []),
    )
    vulns = audit.get("vulnerabilities") or []
    logger.info(
        "pip-audit: ran=%s packages=%s findings=%s",
        audit.get("ran"),
        audit.get("packages_audited"),
        ", ".join(f"{v['package']} {v['id']} {v['severity']}" for v in vulns) or "none",
    )
    return report
