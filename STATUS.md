# ModelGate status

Last updated: 2026-10-20 (Checkpoint A). Deadline: 2026-10-27 13:00 UTC.

"Verified locally only" means it ran on the author's Windows machine (Python 3.12.5).
Nothing has run on GitLab yet.

## Components

| Component | Implemented | Verified on GitLab | Verified locally only |
| --- | --- | --- | --- |
| Synthetic data generator (`modelgate/data.py`) | yes | no | yes |
| Training (`modelgate/train.py`) | yes | no | yes |
| Metrics incl. per-segment AUC, Brier, ECE (`modelgate/evaluate.py`) | yes | no | yes |
| Gate verdict and version bump (`modelgate/evaluate.py`) | yes | no | yes |
| Demo scenarios (`modelgate/scenarios.py`) | yes | no | yes, against a local v0.1.0 baseline |
| Unit and end-to-end tests (60, about 7 s) | yes | no | yes |
| ruff check and ruff format | yes | no | yes |
| Makefile | yes | no | no (make is not installed locally; the `python -m modelgate` commands it wraps are verified) |
| Requirements pins resolve for CPython 3.11 manylinux | yes | no | yes (pip dry-run resolution only) |
| `scan.py` (picklescan, pip-audit) | no | no | no |
| `carbon.py` and SCI | no | no | no |
| `registry.py` (Model Registry, generic package fallback) | no | no | no |
| `.gitlab-ci.yml` | no | no | no |
| Duo flow, `AGENTS.md`, `agent-config.yml`, `docs/SETUP.md` | no | no | no |
| `promote.py`, Release, Environment, Pages (`site.py`) | no | no | no |
| `drift.py` and nightly schedule | no | no | no |
| `docs/STAGES.md`, `docs/VIDEO.md`, `docs/SUBMISSION.md` | no | no | no |

## Local scenario results (Checkpoint A)

Against the v0.1.0 baseline champion, with a clean scan stubbed in so that only the model
rules are under test:

| Scenario | Verdict | AUC (delta) | Brier delta | Segment gap | Segment C AUC (delta) | Failed rules |
| --- | --- | --- | --- | --- | --- | --- |
| baseline (bootstrap) | GO | 0.8397 | | 0.0102 | 0.8467 | |
| good | GO | 0.8734 (+0.0337) | -0.0160 | 0.0147 | 0.8618 (+0.0151) | |
| bad | NEEDS_REVIEW | 0.8558 (+0.0161) | -0.0074 | 0.1658 | 0.7107 (-0.1360) | slice_gap |
| bad-auc | NO_GO | 0.8073 (-0.0324) | +0.0134 | 0.0327 | 0.8331 (-0.0136) | no_regression, calibration |

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
   require a `test` stage. They will not appear in a `secure` stage.
3. Model Registry: the MLflow client docs only mention personal, project or group access
   tokens with `api` scope. The job token allowlist does not include the ML or MLflow APIs.
   `registry.py` will try the job token, record the result, and fall back to the generic
   package registry. The job token can use every Packages API endpoint.
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

## Deviations from the build prompt

- `python:3.11-slim` does not include make. CI will call `python -m modelgate ...`, which is
  what every Makefile target wraps.
- numpy 2.3 and later need Python 3.12 or newer, so the pins in `requirements.txt` target
  versions that still support 3.11.
- `config/data.yaml` was added and is kept separate from `config/train.yaml`. Training
  changes therefore never change the holdout set. The gate report notes when the holdout
  fingerprint differs from the champion's.
- `scenario-bad` swaps `region_score` for `txn_velocity` instead of only dropping
  `region_score`. Dropping it alone costs 0.019 AUC overall, which is NO_GO rather than
  NEEDS_REVIEW. The swap raises headline AUC while segment C falls, which is the harm the
  slice rule exists to catch. The NO_GO variant is `scenario-bad-auc`. A `scenario-baseline`
  target restores the baseline features.
- `scan_clean` fails closed when `reports/scan.json` is missing. Until Checkpoint B the local
  verdict is therefore NO_GO.
- `carbon_efficiency` is marked `skipped` (not failed) when `reports/sci.json` is missing.
  It is a NEEDS_REVIEW-level rule and should not block on a missing measurement.
- Each rule result has a `status` (pass, fail, bootstrap, not_applicable, skipped) alongside
  `passed`.
- `evaluate.py` writes `reports/champion_candidate.json`, the exact `models/champion.json`
  content for this candidate. On GO the flow copies it instead of writing the file itself.
- The repository's `origin` is GitHub (github.com/amaan784/model-promotion-gate). The
  hackathon needs a GitLab project, so the code has to be pushed there before Checkpoint B.
- LICENSE keeps the existing "Copyright (c) 2026 Amaan Sheikh" line instead of a placeholder.

## Known limitations

- Determinism covers the files the gate reads. `sci.json` is a measurement and changes from
  run to run, so the carbon rule reproduces only from the same `sci.json`.
- `meta.json` records wall-clock training time and the model file's hash. These are lineage
  fields, not gate inputs, and may differ between runs.
