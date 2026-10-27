# ModelGate status

Last updated: 2026-10-25 (all stages, the audit fixes, v8 carbon, v9 review escalation, v10 ModelGate Ask, v11 install guide, v12 fallbacks for the flow's git and pipeline lookup, v13 fixes from a check against the hackathon's rules). Deadline: 2026-10-27 13:00 UTC.

"Verified locally" means it ran on the author's machine: Windows with Python 3.12, and Linux
(WSL Ubuntu 22.04) with CPython 3.11.15 installed offline from the same lock. Nothing has run
on GitLab yet. A checkpoint is complete only when it has run on GitLab.

## Components

| Component | Implemented | Verified on GitLab | Verified locally only |
| --- | --- | --- | --- |
| Synthetic data generator (`modelgate/data.py`) | yes | no | yes; identical data and metrics on Windows and Linux |
| Training (`modelgate/train.py`) | yes | no | yes |
| Metrics incl. per-segment AUC, Brier, ECE (`modelgate/evaluate.py`) | yes | no | yes |
| Gate verdict and version bump (`modelgate/evaluate.py`) | yes | no | yes |
| Gate reads the champion from the default branch (`load_champion`) | yes | no | against a fake Files API and a real git repository |
| Demo scenarios (`modelgate/scenarios.py`) | yes | no | yes, against a local v0.1.0 baseline |
| Model and dependency scan (`modelgate/scan.py`) | yes | no | yes; fails closed without network (seen on Linux) |
| Energy and SCI (`modelgate/carbon.py`, `config/sci.yaml`, `docs/SCI.md`) | yes | no | yes |
| Registry with job-token probe and generic package fallback (`modelgate/registry.py`) | yes | no | against a fake GitLab API only |
| `.gitlab-ci.yml` (build, test, register) | yes | no | YAML parses; jobs' commands run locally |
| Dependency lock (`requirements.in` -> `requirements.txt`) | yes | no | installed on 3.12 (Windows) and 3.11 (Linux) |
| Model card, decision record and MR text (`modelgate/card.py`) | yes | no | yes |
| Flow helper: prepare, check, commit, verify-mr, promotion-note (`modelgate/flow.py`) | yes | no | end to end against a bare git origin, with artifact download and with regeneration |
| Flow git fallbacks: fetch from the project URL without origin, Commits API when git push fails | yes | no | against a bare origin and a fake Commits API |
| Duo flow (`.gitlab/duo/flows/modelgate.yaml`) | yes | no | static checks only (schema, tool names, size, helper commands) |
| `AGENTS.md`, `.gitlab/duo/agent-config.yml`, `docs/SETUP.md` | yes | no | static checks only |
| Unit and end-to-end tests (181) | yes | no | yes, also with each demo scenario applied to `config/train.yaml`, as CI sees it on the demo branches and on `main` after a merge |
| Tests independent of the branch's training config (`tests/conftest.py` `copy_config`) | yes | no | yes |
| `promote` and `deploy_production` only when `models/champion.json` exists and changed | yes | no | rule asserted in `tests/test_ci_rules.py`; GitLab evaluates it |
| Drift history survives pushes to `main` (carried in the `pages` job's `data.json`) | yes | no | against a fake API |
| The agent's numbers must appear in the card or the decision record (`card.unknown_numbers`) | yes | no | yes |
| ruff check and ruff format | yes | no | yes |
| Makefile | yes | no | no (make is not installed locally; the commands it wraps are verified) |
| Promotion: reproduce check, final registration, Release (`modelgate/promote.py`) | yes | no | against a fake GitLab API |
| Production deployment record (`deploy_production`, `promote.deploy`) | yes | no | artifact only; the Environments record needs GitLab |
| Pages dashboard (`modelgate/site.py`, `pages` job) | yes | no | built from test data; static, no external requests |
| Cumulative SCI per promoted version | yes | no | yes |
| Nightly drift check (`modelgate/drift.py`, `drift_check` job) | yes | no | yes; the schedule itself is a UI step |
| Training only when the model can change (`.trains_on_branch` rules, flow `no_candidate`) | yes | no | rules parse; flow guard tested; `rules:changes:compare_to` behavior needs GitLab |
| Carbon by verdict, avoided promotion retrains, flow cost in Credits (`site.py`, `config/sci.yaml`) | yes | no | yes; Credits value pending a real run |
| Review escalation: reviewers on GO and NEEDS REVIEW, generated CODEOWNERS (`config/review.yaml`, `modelgate/owners.py`) | yes | no | yes; code owner approval needs Premium and a real merge request |
| ModelGate Ask: Mention-triggered Q&A flow, rule margins in the decision record | yes | no | static checks and margin arithmetic; replies need GitLab, and unlike cards they are not machine-checked |
| Install guide for other teams, public AI Catalog flows (`docs/INSTALL.md`) | yes | no | doc only; publishing needs GitLab |
| Lifecycle stage map (`docs/STAGES.md`) | yes | no | proofs pending |
| Video script and Devpost draft (`docs/VIDEO.md`, `docs/SUBMISSION.md`) | yes | no | drafts; numbers and links to fill from the GitLab runs |

## Where the plan stands

Every checkpoint from A to F is implemented and tested locally. None has run on GitLab, so
nothing is verified on GitLab yet. The plan's cut line (if Checkpoint C does not work on GitLab
by the end of Oct 22, add nothing below D except Release) now decides only what gets
**verified and demonstrated**. All the code exists. The order to verify in is the order
below; stop at whichever checkpoint the time allows, and keep this file honest about where
that was.

## What to check on GitLab

Checkpoint B, the first branch pipeline:

1. `train_evaluate` passes and its log ends with the rule table and timing summary.
2. `reports/energy.json` shows `detected_cpu_model` (expected AMD EPYC) and `in_ci: true`;
   `reports/sci.json` shows `energy_scaled_to_job: true`.
3. `reports/scan.json` shows `pip_audit.ran: true`.
4. `reports/registry.json` records which registry path worked. Expected: the MLflow API
   rejects the job token (401 or 403) and the generic package upload returns 201.
5. `reports/gate_report.json` shows `inputs.champion_source` as "default branch main via
   Files API".
6. `unit_tests` (with git installed), `semgrep-sast` and `secret_detection` pass. Total under
   6 minutes.

Checkpoint C, the first flow run (after `docs/SETUP.md` steps 1 to 7):

1. The flow session log shows which pipeline it picked from the trigger context. This
   settles open question 1 below.
2. `prepare` reports `reports_source` "CI artifacts of job train_evaluate #...". If it says
   "regenerated", the flow's token could not read artifacts. Record which `artifact_attempts`
   failed (open question 2).
3. The branch gets one `[skip ci]` commit with the card, the decision record and, on GO,
   `models/champion.json`. No new pipeline starts.
4. The merge request has the verdict title, the label, and a description identical to
   `reports/mr_description.md` (`verify-mr` says "match").
5. Push again to the same branch: the same merge request is updated, not duplicated.
6. Run it twice end to end, as the plan requires.
7. The commit result says how it pushed (`pushed_with`): "git push", or the Commits API with
   the reason git push failed (open question 5).

Checkpoint D, the first merge of a GO merge request:

1. `promote` logs "promoted 0.1.0: release model-v0.1.0 (created)". The Release page shows
   the card and three asset links.
2. **Operate > Environments > production** lists one deployment of the `deploy_production`
   job.
3. **Deploy > Pages** serves the dashboard with the champion and its candidate history.
4. The flow posts one "Promotion complete" note on the merged merge request. A re-run of the
   pipeline does not post a second one, and a re-run of `promote` finds the existing Release.

Checkpoint E, the schedule and the demo runs (`docs/SETUP.md` steps 9 and 10):

1. A scheduled pipeline runs only `drift_check` and `pages`, and the flow stops on it.
2. `reports/drift_history.json` carries the previous entries forward, also across a push to
   `main` in between (through the `pages` job's `data.json`). If the job token cannot download
   the previous artifacts, the history restarts at one entry.
3. Three merge requests exist: NO-GO (`scenario bad-auc`), NEEDS REVIEW (`scenario bad`) and
   GO (`demo-go`: `scenario bad`, then `scenario good`, merged). On the dashboard the merged
   branch's NEEDS REVIEW candidate appears in the carbon-by-verdict split and the avoided
   retrain. Their links go into `docs/STAGES.md`.
4. A docs-only branch push runs no `train_evaluate`, and the flow ends with `no_candidate`.
5. ModelGate Ask replies once to a mention on the NEEDS REVIEW merge request.
6. Both flows show as Public in the AI Catalog.

## Open questions only GitLab can answer

1. **Trigger context.** For non-mention events the docs promise the "web address of the
   merge request, work item, or project, plus the action". The flow looks for a pipeline URL
   first and otherwise takes the most recent successful pipeline whose source is push, web
   or api, saying so in its answer. Pipelines from any other source stop the flow, so an
   agent session's own pipeline is never packaged as a candidate.
2. **Artifact access from the flow.** The flow's OAuth token is limited to endpoints with the
   `ai_workflows` scope, and the docs do not say whether job artifacts are among them. The
   helper tries the token, then anonymous access (public project with public pipelines), then
   regenerates the reports in the sandbox.
3. **Model Registry with `CI_JOB_TOKEN`.** Not documented; `registry.py` records the outcome.
4. **Tag creation by the job token.** The Releases API creates the `model-v<version>` tag
   from `ref`. A protected `model-v*` tag pattern would block that for a Developer.
5. **Git inside the sandbox.** GitLab's tool code says `run_command` with git "inherits the
   runner's git config (credential.helper ...)", so `git fetch` and `git push` from the helper
   should authenticate. Not yet seen working. Two fallbacks keep the flow going if they do not:
   `prepare` fetches from the public project URL when fetching from origin fails, and `commit`
   creates the same commit through the Commits API with `GITLAB_TOKEN` when git push fails.
   That is the endpoint GitLab's own `create_commit` tool uses, and the file contents still come
   from the checked local commit, not from the model. A push rejected because the branch moved
   is not retried: the newer pipeline runs the flow again.

## Local results

`python -m modelgate reports` from a clean tree gives bootstrap verdict GO and proposed
version 0.1.0. picklescan parses the whole model with no dangerous or unexpected globals.
pip-audit finds no known vulnerabilities in the 70 locked packages. SCI is 0.0033 gCO2e for a
4-second laptop run, a smoke test of the arithmetic only.

On Linux with CPython 3.11.15 and no network: the holdout fingerprint, AUC 0.8397, Brier
0.1458 and segment gap 0.0102 are identical to Windows, all tests pass, and the missing
network makes pip-audit record `ran: false`, so `scan_clean` fails closed and the verdict is
NO_GO. With no network, pip-audit spends about 60 seconds on retries first.

Scenarios against the v0.1.0 baseline champion, with a clean scan stubbed in:

| Scenario | Verdict | AUC (delta) | Brier delta | Segment gap | Segment C AUC (delta) | Failed rules |
| --- | --- | --- | --- | --- | --- | --- |
| baseline (bootstrap) | GO | 0.8397 | | 0.0102 | 0.8467 | |
| good | GO | 0.8734 (+0.0337) | -0.0160 | 0.0147 | 0.8618 (+0.0151) | |
| bad | NEEDS_REVIEW | 0.8558 (+0.0161) | -0.0074 | 0.1658 | 0.7107 (-0.1360) | slice_gap |
| bad-auc | NO_GO | 0.8073 (-0.0324) | +0.0134 | 0.0327 | 0.8331 (-0.0136) | no_regression, calibration |

Flow helper, end to end in a scratch repository: with the pipeline's API unreachable,
`prepare` regenerated every report in 24 s (real pip-audit and CodeCarbon). It checked out
the branch, compared against `origin/main`'s champion and staged the card, decision record
and `champion.json`. `commit` then rejected the unwritten card, accepted it once both blocks
were filled, and pushed one `[skip ci]` commit to the bare origin.

## Findings while building

- **joblib model files cannot be fully scanned.** joblib writes numpy array bytes inline
  between pickle opcodes. picklescan stops at the first array and never sees later globals.
  The model is a protocol-5 pickle (`model.pkl`) instead. `tests/test_scan.py` keeps the
  joblib case as evidence.
- **The scan caught a real CRITICAL advisory.** certifi 2026.7.22 bundles compromised emSign
  roots (GHSA-6q48-6c7w-9gw2), fixed in 2026.10.19. The lock's 14-day release cutoff had
  excluded the fix; certifi is exempt from the cutoff. pytest moved to 9.0.3 for a MODERATE
  advisory.
- **It caught a second one three days later.** On 2026-10-24 a local run turned NO_GO on
  `scan_clean`: a HIGH advisory against filelock 4.0.12 (GHSA-v4x8-3wr5-3wq8, via pip-audit's
  cachecontrol) had been published since the lock was built. filelock is now pinned to 4.2.1
  and exempt from the cutoff like certifi. No other pin changed, and the metrics are
  identical. Every pipeline would have been NO_GO until the lock moved. The scan runs on
  every pipeline, not only on dependency changes, for exactly this reason.
- **pip-audit does not report severity.** `scan.py` takes the GitHub advisory label from OSV.
  Findings with no label are UNKNOWN and do not block.
- **CodeCarbon would overstate energy on a cloud VM.** Without RAPL it charges the whole host
  CPU's TDP to the job; `carbon.py` passes the runner's vCPU share (see `docs/SCI.md`).
- **A branch's own champion.json would corrupt later comparisons.** Once the flow commits a GO
  card, the branch's working tree holds the challenger as champion. The gate therefore reads
  the default branch's copy (Files API in CI, `git show` in the flow), and fails the job
  rather than compare against the wrong model.
- **A later non-GO run must undo an earlier GO on the same branch.** Otherwise merging would
  promote a candidate the latest pipeline rejected. `prepare` resets `champion.json` to the
  default branch's copy for any verdict other than GO.
- **Labels are supported after all.** The Duo tool source shows `labels` on
  `create_merge_request` and `add_labels` / `remove_labels` on `update_merge_request`.
- **The default flow image has `python3.12`, not `python3`.** The setup script tries
  `python3.12` first.
- **The tests would have failed every demo pipeline.** A check against the hackathon's rules
  on 2026-10-25 found that the test fixtures copied the branch's own `config/` and then
  asserted the baseline's numbers. With each demo scenario applied, 4 to 13 tests failed, so
  `unit_tests` would have failed on every demo branch and on `main` after the GO merge: no
  passed pipeline, no flow run, no promotion. The fixtures now copy `config/` with the baseline
  feature list (`tests/conftest.py` `copy_config`), and the full suite passes with every
  scenario applied. The bootstrap branch had hidden it: its training config is the baseline.
- **The first `main` pipeline would have failed.** `rules:changes` is always true when a
  branch is first pushed, so `promote` would have run on the first push of `main`, before any
  champion existed. `promote` and `deploy_production` now also need
  `exists: models/champion.json`.
- **Every push to `main` would have reset the drift chart.** GitLab's artifacts-by-ref
  endpoint reads only the branch's latest successful pipeline, and a push pipeline has no
  `drift_check` job. The history now also travels in the `pages` job's `data.json`, which runs
  in every `main` pipeline.

## Licenses to review

No dependency is GPL. Two transitive dependencies are weak-copyleft, OSI-approved licenses,
used unmodified as libraries:

- certifi (MPL-2.0), via requests
- pycountry (LGPL-2.1-only), via codecarbon

The hackathon's Official Rules allow third-party open source under any OSI-approved license
and say the rules prevail over other hackathon materials. The FAQ's shorter line,
"everything that you use or make must be MIT-licensed", is read as being about the
project's own code, which is MIT. Removing these two would mean dropping CodeCarbon.

## Phase 0 answers (pending, to be filled in by the author)

| # | Check | Answer |
| --- | --- | --- |
| 1 | AI > Triggers > New flow trigger works? (docs: Maintainer or Owner, Premium or Ultimate) | pending |
| 2 | AI > Flows: can create and enable a custom flow? (docs: Maintainer or Owner) | pending |
| 3 | Settings > General > Visibility: can the project be made Public? (Owner; parent group must allow it) | pending |
| 4 | Settings > Repository > Protected branches: who may merge into main, and who will merge the GO merge request? | pending |
| 5 | Hello-world pipeline (on a throwaway branch) runs on a GitLab-hosted runner? | pending |
| 6a | Throwaway flow run: credits used, and do 500 cover about 15 runs plus reruns? | pending |
| 6b | Package registry enabled? Pages available? Public pipelines on? | pending |
| 6c | Settings > General > GitLab Duo > CI/CD runner shows a working runner? | pending |
| 7 | Settings > CI/CD > Variables: can add one? (role probe only) | pending |

If 1, 2 or 3 fail, ask the organizers (GitLab Discord `#transcend-hackathon`, or the Devpost
discussion board) before moving: the Agent Platform access and the credits come with the
hackathon group, and a Free-tier personal namespace has no triggers.

## Where the docs differ from the build prompt

The docs win in each case.

1. Releases: `release-cli` is deprecated and will not be supported after GitLab 20.0. The job
   token can call every Releases API endpoint, so `promote` will create the Release through
   the REST API with `CI_JOB_TOKEN`.
2. The SAST and Secret Detection templates put their jobs in the `test` stage, and the docs
   require a `test` stage. There is therefore no `secure` stage.
3. Model Registry: the MLflow client docs only mention personal, project or group access
   tokens with `api` scope, and the job token allowlist does not include the ML or MLflow
   APIs. `registry.py` tries the job token, records the result, and falls back to the generic
   package registry.
4. Pages: a job becomes a Pages deployment through the `pages:` keyword.
5. Trigger context: see open question 1.
6. Flow jobs do not get `CI_COMMIT_SHA`, `CI_COMMIT_BRANCH`, `CI_PIPELINE_SOURCE`,
   `CI_PIPELINE_IID` or custom CI/CD variables. The flow reads source, ref and IID from the
   pipeline API.
7. `.gitlab/duo/agent-config.yml` is read only from the default branch.
8. `AGENTS.md` reaches a custom flow only through the `user_rule` context. The ModelGate flow
   tells the agent to read the file instead, because GitLab's flow form rejects fields beyond
   its schema (it rejected `max_cycles`), and an optional input is another such field.
9. Creating a flow, enabling it and creating a trigger all need Maintainer or Owner.
10. Bots, service accounts and other flows cannot fire a trigger. The docs say nothing about
    schedules or a flow's own commits, so the flow ignores schedules and card commits use
    `[skip ci]`.
11. The flow's API token is `GITLAB_TOKEN` (OAuth, `Authorization: Bearer`).
12. With `CI_JOB_TOKEN`, the merge request and notes APIs are read-only, so only the flow
    writes to merge requests.
13. SCI is ISO/IEC 21031:2024. The embodied term is M = TE x (TiR / EL) x (RR / ToR).
14. The 40 KiB flow YAML and 80 KiB prompt limits were not in the pages fetched. They are
    kept as given and checked by `tests/test_flow_config.py`.
15. GitLab does not publish the hosted runner's machine type. SCI assumes `n2d-standard-2`
    (AMD EPYC 2nd Gen); `energy.json` records the CPU model actually seen.
16. The flow sandbox allows only the GitLab instance by default. `agent-config.yml` adds
    `pypi.org` and `api.osv.dev`, needed only when the flow regenerates reports.

## Deviations from the build prompt

- `python:3.11-slim` does not include make. CI calls `python -m modelgate ...`, which is what
  every Makefile target wraps.
- The model file is `models/challenger/model.pkl` (stdlib pickle, protocol 5), not
  `model.joblib`, so the scan can see every global.
- `registry.py` calls GitLab's MLflow-compatible REST endpoints with `urllib` instead of
  installing `mlflow-skinny`.
- `requirements.in` lists direct dependencies; `requirements.txt` is the full lock.
- The scan runs inside `train_evaluate`, before any step unpickles the model, because the gate
  needs `scan.json` and the flow reads every report from that one job.
- `train_evaluate` does not run pytest; `unit_tests` does, and publishes the JUnit report.
- `register_challenger` has `allow_failure: true`, so a registry problem does not hide the
  verdict.
- E is scaled from the measured steps to the whole job at the same mean power, and CodeCarbon
  gets forced vCPU-share power values.
- `config/data.yaml` was added, separate from `config/train.yaml`, so training changes never
  change the holdout set.
- `scenario-bad` swaps `region_score` for `txn_velocity` instead of only dropping it.
  Dropping it alone costs 0.019 AUC overall, which is NO_GO rather than NEEDS_REVIEW.
- `scan_clean` fails closed; `carbon_efficiency` is `skipped`, not failed, without
  `sci.json`.
- Each rule result has a `status` alongside `passed`.
- The flow does not write the card itself. `modelgate/card.py` renders every factual section,
  the agent writes the Analysis and Summary blocks, and `modelgate.flow commit` re-renders
  both files and refuses any other change. This keeps numbers out of the model's hands.
- The flow commits with git through `run_command` (the helper), not with the `create_commit`
  tool, so file contents never pass through the model.
- Candidate cards and decisions are named `<version>-rc.<pipeline IID>`, so every candidate
  keeps its own record.
- When the flow regenerates reports because it cannot read the artifacts, scan and SCI are
  re-run on the flow's runner. The model metrics and their rule results are identical. The
  SCI figure, the carbon rule and the pip-audit result can differ, and the card says so.
- The flow takes rule results from the gate report rather than re-applying
  `config/thresholds.yaml`. It reads the file, and records its parameters and sha256 in the
  decision record, so the published rules sit next to every verdict.
- "Accuracy per gram" is reported as AUC gained per gCO2e, the inverse of the carbon rule's
  gCO2e per 0.01 AUC, and only when AUC improved.
- The repository's `origin` is GitHub (github.com/amaan784/model-promotion-gate). The
  hackathon needs a GitLab project; nothing can be verified until the code is pushed there.
- LICENSE keeps the existing "Copyright (c) 2026 Amaan Sheikh" line instead of a placeholder.

## Drift check numbers (local)

Against the 0.1.0 baseline champion, retrained from its own `champion.json` config (holdout
AUC reproduced exactly, 0.8397):

| Date | Drift strength | Batch AUC | Largest PSI | Alert |
| --- | --- | --- | --- | --- |
| 2026-10-20 | 0.00 | 0.8404 | 0.0014 (tenure_years) | no |
| 2026-10-24 | 0.57 | 0.8316 | 0.063 (utilization) | no |
| 2026-10-27 | 1.00 | 0.8343 | 0.214 (utilization) | yes |

The drift is a covariate shift: the inputs move but the label process stays the same, so the
champion's AUC holds while PSI rises. The alert therefore comes from PSI, not from AUC.

## Audit against the build prompt

Five read-only auditors graded 339 requirements of the build prompt against the repository:
270 implemented, 36 that only GitLab can complete, 18 deliberate deviations (listed above),
and 15 claimed gaps. Three skeptics then tried to refute each gap. Thirteen survived, and all
thirteen are fixed:

- The commit step rejects analysis or summary text that names the project anything but
  ModelGate.
- Public functions in `modelgate/` have docstrings.
- The README has a "Setup on GitLab" section, in the order the plan gives.
- The dashboard renders each model card as an HTML page.
- A reordered feature list is a minor bump, like any other feature list change.
- The flow reads `config/thresholds.yaml`. The decision record carries the rule parameters and
  the file's sha256, and the card prints the hash.
- The champion's latest nightly drift result is in the decision record and the card, so the
  analysis can name drift as a driver.
- The card reports AUC gained per gCO2e (accuracy per gram) next to gCO2e per 0.01 AUC.
- `docs/SCI.md` and the dashboard describe and show the cumulative SCI per promoted version as
  implemented.
- The card says when its SCI figure came from a regeneration on the flow's runner (see the
  deviations above).
- `reports` plus the full test suite, what `make all` runs, took about a minute locally, under
  the 2-minute target. CI timing is still to be measured.

## Known limitations

- Determinism covers the files the gate reads. `sci.json` is a measurement and changes from
  run to run, so the carbon rule reproduces only from the same `sci.json`.
- `meta.json` records wall-clock training time and the model file's hash: lineage fields, not
  gate inputs.
- Cumulative SCI counts the candidates whose decision records reached the default branch: every
  pipeline on the merged branch that faced the same previous champion. Candidates on branches
  that were never merged are not on `main`, so they are not counted.
- pip-audit and OSV need network access. Without it, the scan records `ran: false` and the
  gate fails closed.
- `card.contradictions` is a guard rail, not proof. It catches a sentence that names a failed
  rule next to "passed" (and the reverse), not every way prose can mislead: a sentence with a
  negation word is not flagged, so "slice_gap did not fail" gets through. The number check is
  stricter: every decimal number in the agent's text must equal one in the card or the
  decision record, but a copied number can still be attached to the wrong quantity.
