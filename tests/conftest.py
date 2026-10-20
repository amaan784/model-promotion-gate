from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest

from modelgate.common import Paths

REPO = Path(__file__).resolve().parents[1]

# A scan.json that passes scan_clean, for tests that exercise the other rules.
CLEAN_SCAN: dict[str, Any] = {
    "picklescan": {"ran": True, "dangerous_globals": 0},
    "pip_audit": {"ran": True, "vulnerabilities": []},
}


@pytest.fixture
def repo_copy(tmp_path: Path) -> Paths:
    """A scratch repo root holding a copy of config/, so tests never touch the real one."""
    shutil.copytree(REPO / "config", tmp_path / "config")
    return Paths(tmp_path)
