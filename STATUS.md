# ModelGate status

Last updated: 2026-10-20 (Checkpoint B, local part). Deadline: 2026-10-27 13:00 UTC.

"Verified locally only" means it ran on the author's Windows machine in a Python 3.12 venv
installed from `requirements.txt`. Nothing has run on GitLab yet. Checkpoint B is complete
only when a branch pipeline passes on GitLab.

## Components

| Component | Implemented | Verified on GitLab | Verified locally only |
| --- | --- | --- | --- |
| Synthetic data generator (`modelgate/data.py`) | yes | no | yes |
| Training (`modelgate/train.py`) | yes | no | yes |
| Metrics incl. per-segment AUC, Brier, ECE (`modelgate/evaluate.py`) | yes | no | yes |
| Gate verdict and version bump (`modelgate/evaluate.py`) | yes | no | yes |
| Demo scenarios (`modelgate/scenarios.py`) | yes | no | yes, against a local v0.1.0 baseline |
| Model and dependency scan (`modelgate/scan.py`) | yes | no | yes, live pip-audit and OSV calls |
| Energy and SCI (`modelgate/carbon.py`, `config/sci.yaml`, `docs/SCI.md`) | yes | no | yes; the forced-power CodeCarbon path CI will use is the one tested |
| Registry with job-token probe and generic package fallback (`modelgate/registry.py`) | yes | no | against a fake GitLab API only |
| `.gitlab-ci.yml` (build, test, register) | yes | no | YAML parses; not run |
| Dependency lock (`requirements.in` -> `requirements.txt`) | yes | no | installed on 3.12; resolved for 3.11 with uv |
| Unit and end-to-end tests (91, about 8 s) | yes | no | yes |
| ruff check and ruff format | yes | no | yes |
| Makefile | yes | no | no (make is not installed locally; the `python -m modelgate` commands it wraps are verified) |
| Duo flow, `AGENTS.md`, `agent-config.yml`, `docs/SETUP.md` | no | no | no |
| `promote.py`, Release, Environment, Pages (`site.py`) | no | no | no |
| `drift.py` and nightly schedule | no | no | no |
| `docs/STAGES.md`, `docs/VIDEO.md`, `docs/SUBMISSION.md` | no | no | no |

## Checkpoint B: what to check on the first GitLab pipeline

1. `train_evaluate` passes and its log ends with the rule table and timing summary.
2. `reports/energy.json` shows `detected_cpu_model` (expected AMD EPYC) and
   `in_ci: true`. `reports/sci.json` shows `energy_scaled_to_job: true`.
3. `reports/scan.json` shows `pip_audit.ran: true` (network to PyPI and OSV works on
   hosted runners).
4. `reports/registry.json` records which registry path worked, with the HTTP status of
   every attempt. Expected: the MLflow API rejects the job token (401 or 403) and the
   generic package upload returns 201. If the MLflow path works, the version appears under
   Deploy > Model registry.
5. `unit_tests`, `semgrep-sast` and `secret_detection` pass.
6. Total pipeline time is under 6 minutes.

## Local results

`python -m modelgate reports` from a clean tree: bootstrap verdict GO, proposed version
0.1.0. picklescan parsed the whole model (no dangerous or unexpected globals), pip-audit
found no known vulnerabilities in the 70 locked packages, and SCI was 0.0033 gCO2e for a
4-second local run. Measured on a laptop, so the figure is only a smoke test of the
arithmetic.

Scenarios against the v0.1.0 baseline champion, with a clean scan stubbed in:

| Scenario | Verdict | AUC (delta) | Brier delta | Segment gap | Segment C AUC (delta) | Failed rules |
| --- | --- | --- | --- | --- | --- | --- |
| baseline (bootstrap) | GO | 0.8397 | | 0.0102 | 0.8467 | |
| good | GO | 0.8734 (+0.0337) | -0.0160 | 0.0147 | 0.8618 (+0.0151) | |
| bad | NEEDS_REVIEW | 0.8558 (+0.0161) | -0.0074 | 0.1658 | 0.7107 (-0.1360) | slice_gap |
| bad-auc | NO_GO | 0.8073 (-0.0324) | +0.0134 | 0.0327 | 0.8331 (-0.0136) | no_regression, calibration |

## Findings while building Checkpoint B

- **joblib model files cannot be fully scanned.** joblib writes numpy array bytes inline
  between pickle opcodes. picklescan stops at the first array (`scan_err`) and never sees
  later globals; in the test model, `LogisticRegression` was missing from its list. The model
  is now a protocol-5 pickle (`model.pkl`), which picklescan parses end to end.
  `tests/test_scan.py` keeps the joblib case as evidence.
- **The scan caught a real CRITICAL advisory.** certifi 2026.7.22 bundles compromised emSign
  roots (GHSA-6q48-6c7w-9gw2), fixed in 2026.10.19. The lock excludes releases younger than
  14 days, which had excluded that fix; certifi is now exempt from the cutoff.
  pytest 8.4.2 had a MODERATE advisory (GHSA-6w46-j5rx-g56g) and moved to 9.0.3.
- **pip-audit does not report severity.** `scan.py` looks each finding up in OSV and uses the
  GitHub advisory's label (LOW, MODERATE, HIGH, CRITICAL). Findings with no label are
  recorded as UNKNOWN and do not block.
- **CodeCarbon would overstate energy on a cloud VM.** Without RAPL it charges the TDP of the
  whole host CPU model to the job. `carbon.py` passes the runner's vCPU share instead (see
  `docs/SCI.md`).

## Licenses to review

No dependency is GPL. Two transitive dependencies are weak-copyleft, OSI-approved licenses,
used unmodified as libraries:

- certifi (MPL-2.0), via requests
- pycountry (LGPL-2.1-only), via codecarbon

Removing them would mean dropping CodeCarbon, which the plan requires. The author should
decide whether that is acceptable.

## Phase 0 answers (pending, to be filled in by the author)

| # | Check | Answer |
| --- | --- | --- |
| 1 | AI > Triggers > New flow trigger works? (docs: Maintainer or Owner, Premium or Ultimate) | pending |
| 2 | AI > Flows: can create and enable a custom flow? (docs: Maintainer or Owner) | pending |
| 3 | Settings > CI/CD > Variables: can add one? (role probe only) | pending |
| 4 | Settings > Repository > Protected branches: can edit? | pending |
| 5 | Hello-world pipeline runs on a GitLab-hosted runner? | pending |
| 6a | Throwaway flow run: credits used, and drawn from the 500? | pending |
| 6b | Project public? Package registry enabled? Pages available? | pending |
| 6c | Settings > General > GitLab Duo > CI/CD runner shows a working runner? | pending |

If 1 or 2 fail: the organizers said any public MIT GitLab project is acceptable, and a
project in the author's own namespace gives Owner. Triggers need Premium or Ultimate, so test
the trigger there before moving.

## Where the docs differ from the build prompt

The docs win in each case.

1. Releases: `release-cli` is deprecated and will not be supported after GitLab 20.0. The job
   token can call every Releases API endpoint, so `promote` will create the Release through
   the REST API with `CI_JOB_TOKEN`.
2. The SAST and Secret Detection templates put their jobs in the `test` stage, and the docs
   require a `test` stage. There is therefore no `secure` stage.
3. Model Registry: the MLflow client docs only mention personal, project or group access
   tokens with `api` scope. The job token allowlist does not include the ML or MLflow APIs.
   `registry.py` tries the job token, records the result, and falls back to the generic
   package registry. The job token can use every Packages API endpoint, and a model
   version's file upload goes through one (`packages/ml_models/<id>/files`).
4. Pages: a job becomes a Pages deployment through the `pages:` keyword. The job name alone
   no longer matters.
5. Trigger context: for non-mention events the flow receives the web address of the merge
   request, work item or project, plus the action. The docs do not promise a pipeline URL,
   so the flow's guard has to find the pipeline through the API. To check in Checkpoint C.
6. Flow jobs do not get `CI_COMMIT_SHA`, `CI_COMMIT_BRANCH`, `CI_PIPELINE_SOURCE`,
   `CI_PIPELINE_IID` or custom CI/CD variables. The guard reads source and ref from the
   pipeline API.
7. `.gitlab/duo/agent-config.yml` is read only from the default branch. It has to be on
   `main` before the flow runs on a feature branch.
8. `AGENTS.md` reaches a custom flow only if the flow YAML passes the `user_rule` context.
9. The Duo tool registry has no label tool (classes seen include GitLabApiGet, RunCommand,
   CreateCommit, CreateMergeRequest, UpdateMergeRequest, CreateMergeRequestNote). Labels are
   skipped unless UpdateMergeRequest accepts them.
10. Creating a flow, enabling it and creating a trigger all need Maintainer or Owner. A
    "Developer + AI" role probably cannot do Phase 0 steps 1 and 2.
11. Bots, service accounts and other flows cannot fire a trigger. The docs say nothing about
    schedules or a flow's own commits, so the guard keeps the schedule check and card commits
    use `[skip ci]`.
12. The flow's API token is `GITLAB_TOKEN` (OAuth, sent as `Authorization: Bearer`). A
    `PRIVATE-TOKEN` header returns 401.
13. With `CI_JOB_TOKEN`, the merge request and notes APIs are read-only. CI cannot post MR
    notes, so only the flow writes to merge requests.
14. SCI is ISO/IEC 21031:2024. The embodied term is M = TE x (TiR / EL) x (RR / ToR).
15. The 40 KiB flow YAML and 80 KiB prompt limits were not in the pages fetched. They are
    kept as given.
16. GitLab does not publish the hosted runner's machine type ("might change"). SCI assumes
    `n2d-standard-2` (AMD EPYC 2nd Gen); `energy.json` records the CPU model actually seen.

## Deviations from the build prompt

- `python:3.11-slim` does not include make. CI calls `python -m modelgate ...`, which is what
  every Makefile target wraps.
- The model file is `models/challenger/model.pkl` (stdlib pickle, protocol 5), not
  `model.joblib`, so the scan can see every global (see Findings).
- `registry.py` calls GitLab's MLflow-compatible REST endpoints with `urllib` instead of
  installing `mlflow-skinny`. These are the same endpoints the MLflow client uses, without
  its large dependency tree and the CI minutes to install it.
- `requirements.in` lists direct dependencies; `requirements.txt` is the full lock compiled
  by uv for Python 3.11. pip-audit audits the lock, which is exactly what CI installs.
- The scan runs inside `train_evaluate`, because the gate verdict needs `scan.json`, and the
  flow reads every report from that one job. It runs before any step unpickles the model.
- `train_evaluate` does not run pytest; `unit_tests` does, and it publishes the JUnit
  report. Running the suite twice would spend CI minutes for nothing.
- `register_challenger` has `allow_failure: true`. If the registry is down, the pipeline
  still passes so the flow can run, and `registry.json` records the failure.
- E is scaled from the measured steps to the whole job at the same mean power, and CodeCarbon
  gets forced vCPU-share power values (see `docs/SCI.md`).
- `config/data.yaml` was added and is kept separate from `config/train.yaml`. Training
  changes therefore never change the holdout set. The gate report notes when the holdout
  fingerprint differs from the champion's.
- `scenario-bad` swaps `region_score` for `txn_velocity` instead of only dropping
  `region_score`. Dropping it alone costs 0.019 AUC overall, which is NO_GO rather than
  NEEDS_REVIEW. The swap raises headline AUC while segment C falls, which is the harm the
  slice rule exists to catch. The NO_GO variant is `scenario-bad-auc`. A `scenario-baseline`
  target restores the baseline features.
- `scan_clean` fails closed: a missing `scan.json`, a tool that did not run, or a
  picklescan parse error all fail it.
- `carbon_efficiency` is marked `skipped` (not failed) when `reports/sci.json` is missing.
  It is a NEEDS_REVIEW-level rule and should not block on a missing measurement.
- Each rule result has a `status` (pass, fail, bootstrap, not_applicable, skipped) alongside
  `passed`.
- `evaluate.py` writes `reports/champion_candidate.json`, the exact `models/champion.json`
  content for this candidate. On GO the flow copies it instead of writing the file itself.
- The repository's `origin` is GitHub (github.com/amaan784/model-promotion-gate). The
  hackathon needs a GitLab project, and Checkpoint B cannot be verified until the code is
  pushed there.
- LICENSE keeps the existing "Copyright (c) 2026 Amaan Sheikh" line instead of a placeholder.

## Known limitations

- Determinism covers the files the gate reads. `sci.json` is a measurement and changes from
  run to run, so the carbon rule reproduces only from the same `sci.json`.
- `meta.json` records wall-clock training time and the model file's hash. These are lineage
  fields, not gate inputs, and may differ between runs.
- Cumulative SCI per promoted version is not computed yet (planned with `promote`,
  Checkpoint D).
- pip-audit and OSV need network access. Without it the scan records `ran: false` and the
  gate fails closed.
