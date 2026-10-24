"""Energy measurement and Software Carbon Intensity for one evaluated candidate.

    SCI = (E x I + M) / R      (Green Software Foundation SCI, ISO/IEC 21031:2024)

- E: CodeCarbon's estimate for the measured steps (data, train, metrics, scan),
  scaled to the whole CI job at the same mean power. GitLab's runner VMs expose
  no RAPL counters, so CodeCarbon cannot measure; it models CPU power from load.
  It is given the runner's vCPU share of power (config/sci.yaml) instead of the
  TDP of a whole host socket.
- I: grid carbon intensity of the runner region.
- M: embodied emissions, Cloud Carbon Footprint method:
  M = TE x (TiR / EL) x (RR / ToR).
- R: one evaluated model candidate.

Outputs reports/codecarbon.csv (CodeCarbon's raw CSV), reports/energy.json (the tracker's
final readings plus the forced power values) and reports/sci.json.
"""

from __future__ import annotations

import logging
import os
import platform
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from modelgate.common import Paths, load_yaml, read_json, write_json

logger = logging.getLogger(__name__)

SECONDS_PER_YEAR = 365.25 * 24 * 3600
_CONSTANTS = (
    "cpu_watts_per_vcpu",
    "memory_watts_per_gb",
    "pue",
    "grid_intensity_gco2e_per_kwh",
    "server_embodied_kgco2e",
    "server_lifespan_years",
    "family_max_vcpus",
)

NOT_MEASURED = (
    "The GitLab Duo flow run (card writing, merge request): it executes on GitLab's agent "
    "platform and model provider, which publish no per-run energy data.",
    "Image pull and container start before the job script; the job duration starts at "
    "CI_JOB_STARTED_AT.",
    "Network transfer for pip downloads and API calls.",
)


def sig2(value: float) -> float:
    """Two significant figures; the inputs do not support more."""
    return float(f"{value:.2g}")


@dataclass(frozen=True)
class SciConfig:
    """Runner size and SCI constants from config/sci.yaml, each with a cited source."""

    vcpus: int
    memory_gb: float
    cpu_watts_per_vcpu: float
    memory_watts_per_gb: float
    pue: float
    grid_intensity_gco2e_per_kwh: float
    server_embodied_kgco2e: float
    server_lifespan_years: float
    family_max_vcpus: int
    runner: dict[str, Any]
    sources: dict[str, str]

    @classmethod
    def from_yaml(cls, path: Path) -> SciConfig:
        """Load the config from a YAML file.

        Raises ValueError when `runner` or any constant has no non-empty entry under
        `sources`, and KeyError when a required value is missing.
        """
        raw = load_yaml(path)
        sources = {str(k): str(v) for k, v in (raw.get("sources") or {}).items()}
        missing = [k for k in ("runner", *_CONSTANTS) if not sources.get(k)]
        if missing:
            raise ValueError(f"{path}: every constant needs a source; missing {missing}")
        runner = dict(raw["runner"])
        return cls(
            vcpus=int(runner["vcpus"]),
            memory_gb=float(runner["memory_gb"]),
            runner=runner,
            sources=sources,
            **{k: float(raw[k]) for k in _CONSTANTS if k != "family_max_vcpus"},
            family_max_vcpus=int(raw["family_max_vcpus"]),
        )

    @property
    def cpu_power_w(self) -> float:
        """CPU power at full load in watts: vCPU count times watts per vCPU."""
        return self.vcpus * self.cpu_watts_per_vcpu

    @property
    def ram_power_w(self) -> float:
        """Memory power in watts: memory in GB times watts per GB."""
        return self.memory_gb * self.memory_watts_per_gb


def embodied_g(cfg: SciConfig, seconds: float) -> float:
    """M = TE x (TiR / EL) x (RR / ToR), in grams."""
    te_g = cfg.server_embodied_kgco2e * 1000.0
    time_share = seconds / (cfg.server_lifespan_years * SECONDS_PER_YEAR)
    resource_share = cfg.vcpus / cfg.family_max_vcpus
    return te_g * time_share * resource_share


def compute_sci(energy: dict[str, Any], cfg: SciConfig, job_seconds: float) -> dict[str, Any]:
    """Build the SCI report for one candidate from the tracker readings in `energy`.

    When `job_seconds` is longer than the tracked duration, the measured energy is scaled
    to the job length at the same mean power; otherwise it is used unchanged. Embodied
    emissions cover the longer of the two durations. Emissions are in gCO2e and energy in
    kWh, rounded to two significant figures; the report also lists the assumptions, their
    sources and what is not measured.
    """
    tracked_s = float(energy["duration_s"])
    tracked_kwh = float(energy["energy_kwh"])
    scale = job_seconds / tracked_s if tracked_s > 0 and job_seconds > tracked_s else 1.0
    job_kwh = tracked_kwh * scale
    operational = job_kwh * cfg.grid_intensity_gco2e_per_kwh
    embodied = embodied_g(cfg, max(job_seconds, tracked_s))
    return {
        "standard": "Software Carbon Intensity, ISO/IEC 21031:2024",
        "formula": "SCI = (E x I + M) / R",
        "functional_unit": "one evaluated model candidate",
        "sci_gco2e_per_candidate": sig2(operational + embodied),
        "operational_gco2e": sig2(operational),
        "embodied_gco2e": sig2(embodied),
        "energy_kwh": sig2(job_kwh),
        "energy_measured_kwh": sig2(tracked_kwh),
        "measured_seconds": round(tracked_s, 1),
        "job_seconds": round(max(job_seconds, tracked_s), 1),
        "energy_scaled_to_job": scale > 1.0,
        "grid_intensity_gco2e_per_kwh": cfg.grid_intensity_gco2e_per_kwh,
        "assumptions": {
            "runner": cfg.runner,
            "cpu_power_w": cfg.cpu_power_w,
            "ram_power_w": cfg.ram_power_w,
            "pue": cfg.pue,
            "server_embodied_kgco2e": cfg.server_embodied_kgco2e,
            "server_lifespan_years": cfg.server_lifespan_years,
            "vcpu_share": f"{cfg.vcpus}/{cfg.family_max_vcpus}",
            "sources": cfg.sources,
        },
        "measured_on": {
            "cpu_model": energy.get("detected_cpu_model"),
            "os": energy.get("os"),
            "in_ci": energy.get("in_ci", False),
        },
        "not_measured": list(NOT_MEASURED),
    }


def _job_seconds(tracked_s: float) -> float:
    """Seconds from CI_JOB_STARTED_AT to now, never less than `tracked_s`.

    Returns `tracked_s` when CI_JOB_STARTED_AT is unset or empty, or does not parse (logged
    as a warning).
    """
    started = os.environ.get("CI_JOB_STARTED_AT")
    if not started:
        return tracked_s
    try:
        begin = datetime.fromisoformat(started)
    except ValueError:
        logger.warning("could not parse CI_JOB_STARTED_AT=%r; using measured window", started)
        return tracked_s
    return max(tracked_s, (datetime.now(UTC) - begin).total_seconds())


@contextmanager
def measure(paths: Paths) -> Iterator[None]:
    """Run the enclosed steps under CodeCarbon and write reports/energy.json.

    Reads config/sci.yaml for the forced CPU and RAM power, PUE and grid intensity. Deletes
    any old reports/codecarbon.csv, which CodeCarbon then rewrites. energy.json is written
    in a finally block, so it is also written when the enclosed steps raise.
    """
    from codecarbon import OfflineEmissionsTracker

    cfg = SciConfig.from_yaml(paths.config / "sci.yaml")
    paths.reports.mkdir(parents=True, exist_ok=True)
    raw_csv = paths.reports / "codecarbon.csv"
    raw_csv.unlink(missing_ok=True)
    tracker = OfflineEmissionsTracker(
        project_name="modelgate",
        country_iso_code="USA",
        cloud_provider="gcp",
        cloud_region=str(cfg.runner["cloud_region"]),
        force_carbon_intensity_g_co2e_kwh=cfg.grid_intensity_gco2e_per_kwh,
        force_cpu_power=cfg.cpu_power_w,
        force_ram_power=cfg.ram_power_w,
        force_mode_cpu_load=True,
        pue=cfg.pue,
        tracking_mode="machine",
        measure_power_secs=1,
        output_dir=str(paths.reports),
        output_file=raw_csv.name,
        log_level="error",
        allow_multiple_runs=True,
    )
    started = time.perf_counter()
    tracker.start()
    try:
        yield
    finally:
        tracker.stop()
        data = tracker.final_emissions_data
        energy = {
            "method": "CodeCarbon cpu_load mode with forced vCPU-share power (no RAPL)",
            "codecarbon_version": data.codecarbon_version,
            "duration_s": data.duration,
            "wall_seconds": time.perf_counter() - started,
            "energy_kwh": data.energy_consumed,
            "cpu_energy_kwh": data.cpu_energy,
            "ram_energy_kwh": data.ram_energy,
            "forced_cpu_power_w": cfg.cpu_power_w,
            "forced_ram_power_w": cfg.ram_power_w,
            "pue": data.pue,
            "mean_cpu_utilization_percent": data.cpu_utilization_percent,
            "detected_cpu_model": data.cpu_model,
            "detected_cpu_count": data.cpu_count,
            "os": platform.platform(),
            "in_ci": bool(os.environ.get("GITLAB_CI")),
        }
        write_json(paths.reports / "energy.json", energy)
        logger.info(
            "measured %.1fs, %.2g kWh (CPU %.2g, RAM %.2g)",
            data.duration,
            data.energy_consumed,
            data.cpu_energy,
            data.ram_energy,
        )


def main(paths: Paths) -> dict[str, Any]:
    """Compute the SCI from reports/energy.json, write reports/sci.json and return it.

    Reads the constants from config/sci.yaml. The job duration is the time from
    CI_JOB_STARTED_AT to now, never less than the measured window; the measured window is
    used alone when the variable is unset or does not parse. Raises FileNotFoundError when
    reports/energy.json is missing.
    """
    energy_file = paths.reports / "energy.json"
    if not energy_file.exists():
        raise FileNotFoundError(f"{energy_file} missing; run `python -m modelgate carbon` first")
    energy = read_json(energy_file)
    cfg = SciConfig.from_yaml(paths.config / "sci.yaml")
    sci = compute_sci(energy, cfg, _job_seconds(float(energy["duration_s"])))
    write_json(paths.reports / "sci.json", sci)
    logger.info(
        "SCI %.2g gCO2e per candidate (operational %.2g, embodied %.2g) over %.0fs",
        sci["sci_gco2e_per_candidate"],
        sci["operational_gco2e"],
        sci["embodied_gco2e"],
        sci["job_seconds"],
    )
    return sci
