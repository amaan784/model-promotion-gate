from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest

from modelgate.common import Paths, write_json

REPO = Path(__file__).resolve().parents[1]

# A scan.json that passes scan_clean, for tests that exercise the other rules.
CLEAN_SCAN: dict[str, Any] = {
    "picklescan": {
        "ran": True,
        "scan_error": False,
        "dangerous_globals": 0,
        "unexpected_globals": [],
    },
    "pip_audit": {"ran": True, "vulnerabilities": []},
}

# A sci.json with the fields the gate and the card read.
FAKE_SCI: dict[str, Any] = {
    "sci_gco2e_per_candidate": 0.05,
    "operational_gco2e": 0.04,
    "embodied_gco2e": 0.01,
    "energy_kwh": 7e-05,
    "job_seconds": 64.0,
    "energy_scaled_to_job": True,
    "grid_intensity_gco2e_per_kwh": 579.0,
}


@pytest.fixture
def repo_copy(tmp_path: Path) -> Paths:
    """A scratch repo root holding a copy of config/, so tests never touch the real one."""
    shutil.copytree(REPO / "config", tmp_path / "config")
    return Paths(tmp_path)


def build_reports(
    root: Path, scan: dict[str, Any] | None = None, features: tuple[str, ...] | None = None
) -> Paths:
    """Run data, train, metrics and gate in `root` with a stubbed scan and SCI.

    The real scan and CodeCarbon are tested separately; stubbing them keeps this fast
    and offline.
    """
    from modelgate import data, evaluate, scenarios, train

    if not (root / "config").exists():
        shutil.copytree(REPO / "config", root / "config")
    paths = Paths(root)
    if features is not None:
        scenarios.set_features(paths.config / "train.yaml", features)
    data.main(paths)
    train.main(paths)
    write_json(paths.reports / "scan.json", scan if scan is not None else CLEAN_SCAN)
    write_json(paths.reports / "sci.json", FAKE_SCI)
    evaluate.evaluate_challenger(paths)
    evaluate.gate(paths)
    return paths
