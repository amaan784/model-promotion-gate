# Devpost submission draft

The text for the Devpost form, from the GitLab runs on 2026-10-27. Every claim has a link.
Only the video link is left to fill.

## Name

ModelGate

## Tagline

Every model gets a merge request.

## Links

- GitLab project: https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43712399/showcase
- Dashboard (GitLab Pages): https://showcase-2dd78a.gitlab.io/
- Demo video: `<video URL>`
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

## Inspiration

Small ML teams that already use GitLab review every line of application code, then ship a
retrained model because one number went up. Nobody checks whether a customer segment got
worse. The model card, if it exists, is written afterwards. Nobody knows what the training run
cost in energy.

## What it does

ModelGate puts a trained model through the same merge request process as code.

1. A person pushes a training change. GitLab CI trains a challenger and evaluates it on a fixed
   holdout, overall and for each segment of a protected attribute. It scans the model file
   (picklescan, with a module allowlist) and the dependency lock (pip-audit with OSV
   severities), and measures the run's carbon cost as a Software Carbon Intensity figure.
2. Six published rules in `config/thresholds.yaml` give a deterministic GO, NEEDS REVIEW or
   NO-GO. The candidate is registered as a release candidate.
3. The passed pipeline triggers a GitLab Duo custom flow; branch pipelines train only when
   something that can change the model changed. The agent works out which pipeline started
   it, stops on pipelines that need no merge request (schedules, tags, a superseded or
   untrained pipeline), and has code render the model card and decision record. It writes the
   analysis and a three-line summary, fixes them when the commit step rejects them, commits
   them to the branch, and opens or updates the merge request, titled and labelled by the
   verdict.
4. On GO and NEEDS REVIEW the flow requests review from the configured owners, and a second
   flow, ModelGate Ask, answers their questions on the merge request. A person merges. CI confirms that the default branch reproduces the approved metrics
   exactly, registers the version, creates a Release with the model card, records a production
   deployment in GitLab Environments, and republishes the Pages dashboard. The flow posts the
   links on the merged merge request.
5. A nightly schedule scores a drifted batch with the champion. The dashboard charts AUC over
   time, lists each feature's latest PSI and raises an alert past the limit.

Autonomy level: supervised. CI and the flow run every step from the push to the merge
request, and after the merge to the Release, with no person in between. A person approves one
outcome: merging.

## How we built it

- GitLab CI/CD on GitLab-hosted runners; every API call uses `CI_JOB_TOKEN`, so no CI/CD
  variables or personal tokens exist.
- GitLab Duo Agent Platform: two custom flows, ModelGate on a Pipeline events trigger and
  ModelGate Ask on a Mention trigger, each with its own service account; `AGENTS.md` for their
  rules; `.gitlab/duo/agent-config.yml` for the flow's runtime and network allow-list; both
  flows created as Public in the AI Catalog (awaiting GitLab's approval on 2026-10-27).
- Model Registry, with a generic package registry fallback when the job token is refused.
- Releases (REST API), Environments, Pages, pipeline schedules, SAST and Secret Detection.
- Python: scikit-learn, CodeCarbon, picklescan, pip-audit. Synthetic, seeded data only.

## Try it

Both flows are in the AI Catalog as Public, awaiting GitLab's approval; until then, create them
from the YAML in `.gitlab/duo/flows/`. Enable
**ModelGate** with a Pipeline events > Passed trigger, copy the pipeline side, and point it
at your data (`docs/INSTALL.md`).
Reviewers can ask **ModelGate Ask** "what would make this GO?" on any ModelGate merge request.

## Challenges

- Keeping the agent honest. Code renders every table and figure in the card. The commit step
  re-renders the card and refuses any change outside the two blocks the agent writes, any
  number in them that is not in the card or the decision record, and sentences that plainly
  call a failed rule passed.
- The tests had to stop depending on the branch they run on. The demo branches rewrite the
  feature list, and the first version of the suite asserted the baseline's numbers from
  whatever config it found, so it would have failed every demo pipeline.
- joblib model files cannot be fully scanned: picklescan stops at joblib's inline numpy arrays.
  ModelGate saves a standard pickle that it can parse end to end.
- A branch that earns a GO and is then pushed again must not compare against itself, or keep a
  stale GO. The gate reads the champion from the default branch, and the flow resets the
  branch's champion when a later run is not GO.
- GitLab's flow form treats HTML comments in a prompt as dangerous content, and it rejected
  `max_cycles`, a field in GitLab's own v1 specification. The prompt now names its markers in
  words, and a test fails on any pattern the form rejects.
- On a branch, CI's variables made the gate inside the unit tests read `main`'s champion
  through the API, so 9 tests failed only on GitLab. The tests now run without CI's variables.

## Accomplishments

- The gate caught a real CRITICAL advisory in our own dependency lock (certifi, compromised
  CA roots) before the first pipeline ran. Three days later it caught a HIGH one (filelock)
  published after the lock was built: the same code, retrained unchanged, went from GO to
  NO-GO until the lock moved.
- The full loop ran on GitLab with no person between the push and the merge request: GO
  (https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43712399/showcase/-/merge_requests/2), NO-GO
  (https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43712399/showcase/-/merge_requests/3) and NEEDS REVIEW
  (https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43712399/showcase/-/merge_requests/4), each card and
  description written by the flow and accepted by the commit step's checks.
- The NEEDS REVIEW candidate raised overall AUC from 0.8397 to 0.8558 while segment C fell from
  0.8467 to 0.7107. A headline-metric gate would have shipped it.
- On `demo-go` the same merge request went from NEEDS REVIEW to GO after one more training
  change, and merging it promoted 0.2.0: reproduced on `main`, released
  (https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43712399/showcase/-/releases/model-v0.2.0) and deployed.
- ModelGate Ask answered "what would make it GO?" on !4 with numbers that all match the
  decision record.

## What we learned

- A Pipeline events trigger hands the flow the whole pipeline webhook payload as its goal.
- The flow's token can download job artifacts, so the flow packages CI's own reports instead
  of retraining.
- The Model Registry does not accept `CI_JOB_TOKEN`; the generic package registry does, and
  the job token can create Release tags.
- Enabling a flow adds Mention and Assign triggers by default. Remove the ones the flow does
  not expect, or every mention starts a run.
- Deleting a merged branch breaks links to the model card on it, so ModelGate's branches stay.

## What's next

- Real datasets with their own fairness slices.
- Comparing candidates against the champion on the champion's stored predictions, not a
  retrain.
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
