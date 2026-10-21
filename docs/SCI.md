# Software Carbon Intensity in ModelGate

ModelGate reports the carbon cost of every evaluated model candidate using the Green
Software Foundation's Software Carbon Intensity specification (SCI, ISO/IEC 21031:2024):

```
SCI = (E x I + M) / R
```

| Term | Meaning in ModelGate |
| --- | --- |
| R | One evaluated model candidate: one `train_evaluate` job |
| E | Energy of that job, kWh |
| I | Grid carbon intensity of the runner region, gCO2e/kWh |
| M | Embodied emissions of the runner hardware reserved for the job, gCO2e |

The code is [modelgate/carbon.py](../modelgate/carbon.py). The constants are in
[config/sci.yaml](../config/sci.yaml), and each one has a source entry; the loader rejects
the file if any source is missing. Results are written to `reports/sci.json`, and the raw
tracker readings to `reports/energy.json` and `reports/codecarbon.csv`.

## E: energy

E comes from CodeCarbon 3.3.1. A GitLab-hosted runner is a Google Cloud VM, and the VM
exposes no RAPL energy counters. CodeCarbon therefore cannot measure power; it falls back to
estimating CPU power from CPU load. Left alone, it would look up the host CPU model's TDP,
which covers a whole server socket, and charge all of it to a 2-vCPU job. ModelGate instead
gives CodeCarbon the runner's share:

- CPU: `force_cpu_power` = 2 vCPUs x 1.575 W = 3.15 W, the Cloud Carbon Footprint (CCF)
  coefficient for AMD EPYC 2nd Gen at 100% utilization. CodeCarbon's load model
  (`cpu_load` mode, machine tracking) then estimates power as
  3.15 W x (0.1 + 0.9 x load^3), sampled every second.
- Memory: `force_ram_power` = 8 GB x 0.392 W/GB = 3.1 W (CCF memory coefficient).
- `pue` = 1.1, applied by CodeCarbon to the energy total.

CodeCarbon measures the steps that do the work: data generation, training, scanning and
evaluation. The job also spends time on container setup and `pip install` before Python
starts. ModelGate scales the measured energy to the whole job at the same mean power, using
`CI_JOB_STARTED_AT`. `sci.json` reports both the measured and the scaled value
(`energy_measured_kwh`, `energy_kwh`). Outside CI there is no job start time, and no
scaling is applied.

## I: grid intensity

579 gCO2e/kWh for `us-east1` (South Carolina), from Google Cloud's 2025 region data. This is
the grid's location-based average intensity. It is not Google's market-based figure, which
would credit Google's clean-energy purchases. GitLab documents `us-east1` as the default
region of its hosted Linux runners.

## M: embodied emissions

The CCF method:

```
M = TE x (TiR / EL) x (RR / ToR)
```

| Term | Value | Basis |
| --- | --- | --- |
| TE | 2321.46 kgCO2e | CCF embodied coefficient for `n2d-standard-2`, EPYC 2nd Gen |
| TiR | job duration, seconds | time the VM is reserved for the job |
| EL | 4 years | CCF expected server lifespan |
| RR / ToR | 2 / 224 | job vCPUs over the largest `n2d` machine type |

For a 120-second job, M = 2,321,460 g x (120 / 126,230,400) x (2 / 224) = 0.020 g.

## Assumptions and sources

| Assumption | Value | Source |
| --- | --- | --- |
| Runner shape | 2 vCPU, 8 GB, GCP `us-east1` | [GitLab hosted runners on Linux](https://docs.gitlab.com/ci/runners/hosted_runners/linux/) |
| Machine type | `n2d-standard-2`, AMD EPYC 2nd Gen | Assumption. GitLab says the machine type "might change" and does not publish it |
| CPU power at full load | 1.575 W per vCPU | [CCF coefficients-gcp-use.csv](https://github.com/cloud-carbon-footprint/ccf-coefficients/blob/main/output/coefficients-gcp-use.csv) |
| Memory power | 0.392 W per GB | [CCF methodology](https://www.cloudcarbonfootprint.org/docs/methodology/) |
| PUE | 1.1 | [CCF methodology](https://www.cloudcarbonfootprint.org/docs/methodology/), from Google's published PUE |
| Grid intensity | 579 gCO2e/kWh | [Google Cloud region carbon data, 2025](https://cloud.google.com/sustainability/region-carbon) |
| Server embodied emissions | 2321.46 kgCO2e | [CCF coefficients-gcp-embodied.csv](https://github.com/cloud-carbon-footprint/ccf-coefficients/blob/main/output/coefficients-gcp-embodied.csv) |
| Server lifespan | 4 years | [CCF embodied emissions](https://www.cloudcarbonfootprint.org/docs/embodied-emissions) |
| vCPU share | 2 of 224 | [CCF embodied emissions](https://www.cloudcarbonfootprint.org/docs/embodied-emissions) |
| Untracked job time | same mean power as the measured steps | ModelGate assumption |

## Precision

Every reported SCI figure has two significant figures. CodeCarbon's figure is a model
driven by sampled CPU load, and the coefficients are fleet medians, so more digits would
claim precision the inputs do not have.

For a 90-second job, these constants give about 0.07 g per candidate at idle load and about
0.12 g at full load.

## How the gate uses it

`carbon_efficiency` in [config/thresholds.yaml](../config/thresholds.yaml) applies only when
the challenger improves AUC. It divides the candidate's SCI by the gain in units of 0.01 AUC
and fails above 2.0 gCO2e per 0.01 AUC. At about 0.1 g per candidate, the rule fails only
for gains below roughly 0.0005 AUC: a retrain that buys noise-level improvement. A failure
alone gives NEEDS_REVIEW, not NO_GO.

Because `sci.json` is a measurement, it differs between runs of the same commit. The gate
verdict is reproducible from a given `sci.json`, not from a fresh measurement.

## What is not measured

- The GitLab Duo flow run that writes the model card and the merge request. It runs on
  GitLab's agent platform and model provider, which publish no per-run energy data. ModelGate
  reports it as not measured rather than guess.
- Image pull and container start before the job script begins.
- Network transfer for package downloads and API calls.
- SAST, secret detection and unit test jobs. They run per pipeline, not per model candidate,
  and are outside the functional unit.

## Cumulative cost per promoted version

Planned for Checkpoint D. When a version is promoted, the SCI of every candidate evaluated on
its merge request branch, rejected ones included, is summed from `models/decisions/`.
