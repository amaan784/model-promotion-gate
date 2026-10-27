"""SCI arithmetic with hand-computed values."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml
from conftest import REPO

from modelgate.carbon import SciConfig, _job_seconds, compute_sci, embodied_g, sig2

CFG = SciConfig(
    vcpus=2,
    memory_gb=8,
    cpu_watts_per_vcpu=1.575,
    memory_watts_per_gb=0.392,
    pue=1.1,
    grid_intensity_gco2e_per_kwh=579,
    server_embodied_kgco2e=2321.46,
    server_lifespan_years=4,
    family_max_vcpus=224,
    runner={"tag": "saas-linux-small-amd64"},
    sources={},
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0.135504, 0.14), (0.0197041, 0.02), (1234.0, 1200.0), (0.0, 0.0), (2.0, 2.0)],
)
def test_sig2(value: float, expected: float) -> None:
    assert sig2(value) == expected


def test_forced_power_is_the_vcpu_share() -> None:
    assert CFG.cpu_power_w == pytest.approx(3.15)  # 2 x 1.575 W
    assert CFG.ram_power_w == pytest.approx(3.136)  # 8 GB x 0.392 W


def test_embodied_by_hand() -> None:
    # 2,321,460 g x (120 s / 126,230,400 s) x (2 / 224) = 0.019704 g
    assert embodied_g(CFG, 120) == pytest.approx(0.019704, rel=1e-4)


def test_sci_scales_measured_energy_to_the_job() -> None:
    # measured 0.0001 kWh in 60 s; job ran 120 s -> E = 0.0002 kWh
    # E x I = 0.0002 x 579 = 0.1158 g; M = 0.019704 g; SCI = 0.1355 -> 0.14
    sci = compute_sci({"duration_s": 60.0, "energy_kwh": 0.0001}, CFG, job_seconds=120.0)
    assert sci["energy_kwh"] == 0.0002
    assert sci["energy_measured_kwh"] == 0.0001
    assert sci["operational_gco2e"] == 0.12
    assert sci["embodied_gco2e"] == 0.02
    assert sci["sci_gco2e_per_candidate"] == 0.14
    assert sci["energy_scaled_to_job"] is True
    assert sci["job_seconds"] == 120.0


def test_sci_never_scales_below_the_measured_window() -> None:
    sci = compute_sci({"duration_s": 60.0, "energy_kwh": 0.0001}, CFG, job_seconds=30.0)
    assert sci["energy_kwh"] == 0.0001
    assert sci["energy_scaled_to_job"] is False
    assert sci["job_seconds"] == 60.0


def test_sci_lists_what_it_does_not_measure() -> None:
    sci = compute_sci({"duration_s": 1.0, "energy_kwh": 1e-6}, CFG, job_seconds=1.0)
    assert any("Duo flow" in item for item in sci["not_measured"])


def test_repo_sci_config_loads_with_sources() -> None:
    cfg = SciConfig.from_yaml(REPO / "config" / "sci.yaml")
    assert cfg.vcpus == 2
    assert cfg.grid_intensity_gco2e_per_kwh == 579
    assert all(cfg.sources.values())


def test_constant_without_source_is_rejected(tmp_path: Path) -> None:
    raw = yaml.safe_load((REPO / "config" / "sci.yaml").read_text(encoding="utf-8"))
    del raw["sources"]["pue"]
    path = tmp_path / "sci.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="pue"):
        SciConfig.from_yaml(path)


def test_job_seconds_from_ci_start_time(monkeypatch: pytest.MonkeyPatch) -> None:
    started = (datetime.now(UTC) - timedelta(seconds=100)).isoformat().replace("+00:00", "Z")
    monkeypatch.setenv("CI_JOB_STARTED_AT", started)
    assert _job_seconds(10.0) == pytest.approx(100.0, abs=5.0)
    monkeypatch.setenv("CI_JOB_STARTED_AT", "not a date")
    assert _job_seconds(10.0) == 10.0
    monkeypatch.delenv("CI_JOB_STARTED_AT")
    assert _job_seconds(10.0) == 10.0
