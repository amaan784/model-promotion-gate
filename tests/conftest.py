from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from modelgate.common import Paths, write_json

REPO = Path(__file__).resolve().parents[1]

# CI sets these in every job. On a branch, load_champion would then read the champion from
# the default branch through the API instead of the one a test builds, so the tests run
# without them. Removed at import, before any fixture of any scope; a test that needs one
# sets it with monkeypatch.
for _name in [n for n in os.environ if n.startswith(("CI_", "GITLAB_"))]:
    del os.environ[_name]
os.environ.pop("MODELGATE_CHAMPION_REF", None)

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


def copy_config(dest: Path, source: Path = REPO) -> Path:
    """Copy source/config to dest/config with the baseline feature list, and return the copy.

    The tests assert the baseline's numbers, so they must not depend on the training change
    the current branch carries. CI runs them on the demo branches, whose scenarios rewrite
    the features in config/train.yaml, and on main after such a branch is merged.
    """
    from modelgate import scenarios

    shutil.copytree(source / "config", dest / "config")
    train_yaml = dest / "config" / "train.yaml"
    scenarios.set_features(train_yaml, scenarios.SCENARIOS["baseline"].features)
    return dest / "config"


@pytest.fixture
def repo_copy(tmp_path: Path) -> Paths:
    """A scratch repo root holding a copy of config/, so tests never touch the real one."""
    copy_config(tmp_path)
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
        copy_config(root)
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
