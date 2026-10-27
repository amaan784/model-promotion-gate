# Devpost submission draft

Fill every `<...>` from the real GitLab runs before submitting. Keep a claim only if a link
backs it up.

## Name

ModelGate

## Tagline

Every model gets a merge request.

## Links

- GitLab project: `<project URL>`
- Dashboard (GitLab Pages): `<Pages URL>`
- Demo video: `<video URL>`
- NO-GO / NEEDS REVIEW / GO merge requests: `<links>`
- Release: `<Release URL>`
- Pipelines: `<project URL>/-/pipelines`

## Form fields

- Path: **Start Fresh** (Path A). The project was created for this hackathon; its first commit
  is dated 2026-10-20.
- Autonomy level: **Supervised**.
- Lifecycle stages: select only the stages that have a proof link in `docs/STAGES.md`.
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
  flows published as Public in the AI Catalog `<confirm before submitting>`.
- Model Registry, with a generic package registry fallback when the job token is refused.
- Releases (REST API), Environments, Pages, pipeline schedules, SAST and Secret Detection.
- Python: scikit-learn, CodeCarbon, picklescan, pip-audit. Synthetic, seeded data only.

## Try it

The two flows are public in the AI Catalog `<confirm before submitting>`. Enable
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
- `<anything the GitLab runs taught us>`

## Accomplishments

- The gate caught a real CRITICAL advisory in our own dependency lock (certifi, compromised
  CA roots) before the first pipeline ran. Three days later it caught a HIGH one (filelock)
  published after the lock was built: the same code, retrained unchanged, went from GO to
  NO-GO until the lock moved.
- `<the three real runs, with links>`

## What we learned

`<from the GitLab runs: trigger context, artifact access from the flow, Model Registry with a
job token>`

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
| SCI per evaluated candidate | `<median>` gCO2e (`<min>` to `<max>`) | `reports/sci.json` of each candidate |
| Carbon to reach the promoted version | `<value>` gCO2e over `<n>` candidates | the Release notes |
| Candidates stopped before shipping | `<n>` | the NO-GO and NEEDS REVIEW merge requests |

The gains, against a pipeline that trains on every push and promotes every candidate. These
are avoided-emission estimates built from the measured SCI, not SCI scores:

- **Training only when the model can change.** Branch pipelines skip `train_evaluate` when no
  file that affects the model changed, and the flow stops instead of retraining in its
  sandbox. `<n>` of `<N>` branch pipelines skipped training, avoiding about `<n x median SCI>`
  gCO2e and `<n x median train_evaluate minutes>` CI minutes.
- **Promotion retrains that never ran.** Every stopped candidate is a promotion retrain that
  did not happen. The dashboard sums them for the candidates on `main`: `<from the
  dashboard>` gCO2e, and `<n x median SCI>` gCO2e across all `<n>` stopped candidates.
- **The flow's own cost**, in GitLab Credits per run: `<from the Credits dashboard>`. It is
  reported next to SCI, never added to it.

The gate also turns carbon into a decision. `carbon_efficiency` sends a candidate to review
when its SCI per 0.01 AUC gained exceeds 2.0 gCO2e. At this project's SCI of about `<median>` g,
that stops any retrain whose AUC gain is under about `<median / 200>`: carbon spent for a
noise-level gain. Every number has two significant figures. `docs/SCI.md` lists every
assumption with its source, and what is not measured: the Duo flow's own run, image pulls,
network transfer, and the test and scan jobs.

## Built with

GitLab CI/CD, GitLab Duo Agent Platform, GitLab Model Registry, GitLab Releases, GitLab
Environments, GitLab Pages, Python, scikit-learn, CodeCarbon, picklescan, pip-audit.

## License

MIT.
