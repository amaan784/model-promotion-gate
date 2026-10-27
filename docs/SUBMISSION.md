# Devpost submission draft

The text for the Devpost form, from the GitLab runs on 2026-10-27. Every claim has a link.

## Name

ModelGate

## Tagline

Every model gets a merge request.

## Links

- GitLab project: https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43712399/showcase
- Dashboard (GitLab Pages): https://showcase-2dd78a.gitlab.io/
- Demo video: linked on the Devpost project page
- Merge requests opened by the flow: GO https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43712399/showcase/-/merge_requests/5, NEEDS REVIEW
  https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43712399/showcase/-/merge_requests/4 (with a ModelGate Ask reply), NO-GO
  https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43712399/showcase/-/merge_requests/3, the first champion https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43712399/showcase/-/merge_requests/2
- Releases: https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43712399/showcase/-/releases
- Pipelines: https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43712399/showcase/-/pipelines

## Form fields

- Path: **Start Fresh** (Path A). The project was created for this hackathon; its first commit
  is dated 2026-10-20.
- Autonomy level: **Supervised**.
- Lifecycle stages: Create, Verify, Package, Secure, Release, Configure, Monitor, Govern (8 of
  9; each has a proof link in `docs/STAGES.md`). Not Plan.
- Most Environmentally Impactful: **tick the opt-in**, and paste the "Environmental impact"
  section below.
- Built with: the list at the end, which names GitLab.

## Before the deadline (2026-10-27 13:00 UTC)

- Make every commit, including this file and the STAGES proofs, by about
  12:00 UTC. Commit docs-only changes to `main` with `[skip ci]` in the message, so they start
  no pipeline and no flow run.
- Deactivate the nightly schedule before 13:00 UTC, so the dashboard stops changing during
  judging.
- After 13:00 UTC change nothing: no commits, no reruns, no new video. To keep working, fork
  the project.

## About the project

Paste everything from "Inspiration" to the end of "What's next" into Devpost's "About the
project" field. The headings are the ones Devpost asks for.

## Inspiration

Small ML teams that already use GitLab review every line of application code, then ship a
retrained model because one number went up. Nobody checks whether a customer segment got
worse, the model card is written afterwards if at all, and nobody knows what the training run
cost in carbon. Code already has a review path on GitLab. We wanted the same for models: every
model gets a merge request.

## What it does

ModelGate treats a retrained machine learning (ML) model like code. The example in the
repository is a credit risk model that predicts how likely a customer is to miss loan payments
in the next 12 months, trained on synthetic data.

1. A person pushes a training change. GitLab CI trains a challenger and evaluates it on a fixed
   holdout, overall and for each segment of a protected attribute. It scans the model file
   (picklescan, with a module allowlist) and the dependency lock (pip-audit, with OSV
   severities), and measures the run's carbon cost as a Software Carbon Intensity figure.
2. Six published rules give a deterministic GO, NEEDS REVIEW or NO-GO, and a version bump.
3. The passed pipeline triggers a GitLab Duo custom flow. The agent works out which pipeline
   started it, stops when there is nothing to package, has code render the model card and
   decision record, writes the analysis and a three-line summary, fixes them when the commit
   step rejects them, and opens or updates the merge request, titled and labelled by the
   verdict. On GO and NEEDS REVIEW it requests a review.
4. A second flow, ModelGate Ask, answers reviewers' questions on the merge request, such as
   "what would make this GO?", from the committed decision record.
5. A person merges. CI confirms that `main` reproduces the approved metrics exactly, registers
   the version, creates a Release with the model card, records a production deployment and
   republishes the Pages dashboard. The flow posts "Promotion complete" on the merge request.
6. A nightly schedule scores a drifted batch with the champion, and the dashboard raises an
   alert when a feature's PSI passes the limit.

Autonomy level: supervised. The agents run every step from the push to the merge request, and
from the merge to the Release. A person approves one thing: the merge. ModelGate covers 8 of
the 9 lifecycle stages: Create, Verify, Package, Secure, Release, Configure, Monitor and
Govern.

See it running:

- GO, the first champion: https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43712399/showcase/-/merge_requests/2
- NO-GO: https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43712399/showcase/-/merge_requests/3
- NEEDS REVIEW, with a ModelGate Ask reply: https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43712399/showcase/-/merge_requests/4
- NEEDS REVIEW, then GO after one more change, promoted as 0.2.0: https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43712399/showcase/-/merge_requests/5
- Dashboard: https://showcase-2dd78a.gitlab.io/

## How we built it

- GitLab CI/CD on GitLab-hosted runners. Every API call uses `CI_JOB_TOKEN`, so there are no
  CI/CD variables and no personal tokens.
- GitLab Duo Agent Platform: two custom flows, ModelGate on a Pipeline events trigger and
  ModelGate Ask on a Mention trigger, each with its own service account. `AGENTS.md` holds
  their rules and `.gitlab/duo/agent-config.yml` their runtime.
- A Python helper, `python -m modelgate.flow`, does every file and git operation for the
  agent. The agent writes only two blocks of text. The commit step re-renders the card and
  rejects any other change, any number that is not in the card or the decision record, and
  text that calls a failed rule passed.
- Releases, Environments, Pages, pipeline schedules, the package registry, SAST and Secret
  Detection.
- Python with scikit-learn, CodeCarbon, picklescan and pip-audit, on synthetic, seeded data.
- Carbon follows the SCI specification (ISO/IEC 21031:2024), per evaluated candidate:
  $SCI = \frac{E \cdot I + M}{R}$, with $R$ = one candidate.

## Challenges we ran into

- Keeping the agent honest. Code renders every table and figure in the card, and the commit
  step refuses anything the agent changes outside its two blocks, any number it invents, and
  sentences that call a failed rule passed.
- GitLab's flow form treats HTML comments in a prompt as dangerous content, and it rejected
  `max_cycles`, a field in GitLab's own v1 specification. The prompt now names its markers in
  words, and a test fails on any pattern the form rejects.
- On a branch, CI's variables made the gate inside the unit tests read `main`'s champion
  through the API, so 9 tests failed only on GitLab. The tests now run without CI's variables.
- joblib model files cannot be fully scanned: picklescan stops at joblib's inline numpy arrays.
  ModelGate saves a standard pickle that it can parse end to end.
- A branch that earns a GO and is then pushed again must not compare against itself. The gate
  reads the champion from the default branch, and the flow resets the branch's champion when a
  later run is not GO.

## Accomplishments that we're proud of

- The whole loop ran on GitLab with no person between the push and the merge request: GO, NO-GO
  and NEEDS REVIEW, each card and description written by the flow and accepted by the commit
  step's checks.
- The NEEDS REVIEW candidate raised overall AUC from 0.8397 to 0.8558 while segment C fell from
  0.8467 to 0.7107. A gate on the headline metric would have shipped it.
- On !5 the same merge request went from NEEDS REVIEW to GO after one more training change.
  Merging it promoted 0.2.0: reproduced on `main`, released (https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43712399/showcase/-/releases/model-v0.2.0) and
  deployed.
- ModelGate Ask answered "what would make it GO?" on !4 with numbers that all match the
  decision record.
- The gate caught real advisories in our own dependency lock: a CRITICAL one (certifi) before
  the first pipeline ran, and later a HIGH one (filelock) published after the lock was built.
  The same code, retrained unchanged, went from GO to NO-GO until the lock moved.
- Carbon is reported for every candidate: 0.036 gCO2e median, 0.074 gCO2e to reach 0.2.0, and a
  docs-only push trains nothing.

## What we learned

- A Pipeline events trigger hands the flow the whole pipeline webhook payload as its goal.
- The flow's token can download job artifacts, so the flow packages CI's own reports instead of
  retraining.
- The Model Registry does not accept `CI_JOB_TOKEN`; the generic package registry does, and the
  job token can create Release tags.
- Enabling a flow adds Mention and Assign triggers by default. Remove the ones the flow does not
  expect, or every mention starts a run.
- Deleting a merged branch breaks links to the model card on it, so ModelGate's branches stay.

## What's next for ModelGate: a model promotion gate for GitLab

- Real datasets with their own fairness slices.
- Comparing candidates against the champion on the champion's stored predictions, instead of a
  retrain.
- The Plan stage: open an issue on NO-GO that says what to change.
- Measuring the flow's own energy once GitLab exposes it.

## Environmental impact (Most Environmentally Impactful)

ModelGate reports a Software Carbon Intensity figure (ISO/IEC 21031:2024) for every evaluated
model candidate: SCI = (E x I + M) / R, with R = one candidate.

- **E**: CodeCarbon's estimate for the training job, given the runner's 2-vCPU share of power
  (Cloud Carbon Footprint coefficients). GitLab's runner VMs expose no hardware energy
  counters. Measured steps are scaled to the whole job.
- **I**: 579 gCO2e/kWh for GCP us-east1 (Google Cloud, 2025 data).
- **M**: embodied emissions of an n2d-standard-2 share over a 4-year server life (Cloud Carbon
  Footprint method).

The Green Software Foundation's SCI for AI (ratified late 2025) builds on the same ISO
standard; `docs/SCI.md` says how ModelGate's functional unit relates to it, without claiming
conformance.

Measured on this project's runs:

| Quantity | Value | Source |
| --- | --- | --- |
| SCI per evaluated candidate | 0.036 gCO2e median (0.032 to 0.041), 5 candidates | each candidate's decision record |
| Carbon to reach the promoted version | 0.074 gCO2e over 2 candidates for 0.2.0 | the Release notes of model-v0.2.0 |
| Candidates stopped before shipping | 3 | !3 (NO-GO), !4 and the first candidate on !5 (NEEDS REVIEW) |

The gains, against a pipeline that trains on every push and promotes every candidate. These
are avoided-emission estimates built from the measured SCI, not SCI scores:

- **Training only when the model can change.** Branch pipelines skip `train_evaluate` when no
  file that affects the model changed, and the flow stops instead of retraining in its
  sandbox. The docs-only `docs-proofs` pipeline that added these links
  (https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43712399/showcase/-/pipelines?ref=docs-proofs) ran no training. Every one of the
  flow's 5 card commits carries `[skip ci]`, so packaging a verdict never retrains. Four of them
  are on branches whose training config differs from `main`'s and would otherwise have
  retrained: about 0.14 gCO2e that did not run, at the median SCI.
- **Promotion retrains that never ran.** Every stopped candidate is a promotion retrain that
  did not happen. The dashboard sums them for the candidates on `main`: 0.038 gCO2e, and
  0.11 gCO2e across all 3 stopped candidates.
- **The flow's own cost**, in GitLab Credits: 35.64 credits for 10 sessions on 2026-10-27,
  about 3.6 per run. It is reported next to SCI, never added to it.

The gate also turns carbon into a decision. `carbon_efficiency` sends a candidate to review
when its SCI per 0.01 AUC gained exceeds 2.0 gCO2e. At this project's SCI of about 0.036 g,
that stops any retrain whose AUC gain is under about 0.00018: carbon spent for a
noise-level gain. Every number has two significant figures. `docs/SCI.md` lists every
assumption with its source, and what is not measured: the Duo flow's own run, image pulls,
network transfer, and the test and scan jobs.

## Built with

GitLab CI/CD, GitLab Duo Agent Platform, GitLab Model Registry, GitLab Releases, GitLab
Environments, GitLab Pages, Python, scikit-learn, CodeCarbon, picklescan, pip-audit.

## License

MIT.
