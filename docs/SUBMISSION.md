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
   something that can change the model changed. Code renders the model card and
   decision record. The agent writes the analysis and a three-line summary, commits them to
   the branch, and opens or updates the merge request, titled and labelled by the verdict.
4. On GO and NEEDS REVIEW the flow requests review from the configured owners, and a second
   flow, ModelGate Ask, answers their questions on the merge request. A person merges. CI confirms that the default branch reproduces the approved metrics
   exactly, registers the version, creates a Release with the model card, records a production
   deployment in GitLab Environments, and republishes the Pages dashboard. The flow posts the
   links on the merged merge request.
5. A nightly schedule scores a drifted batch with the champion and plots AUC and PSI.

Autonomy level: supervised. The agent does everything up to the merge; a person approves one
outcome by merging.

## How we built it

- GitLab CI/CD on GitLab-hosted runners; every API call uses `CI_JOB_TOKEN`, so no CI/CD
  variables or personal tokens exist.
- GitLab Duo Agent Platform: a custom ambient flow with a Pipeline events trigger, and
  `AGENTS.md` for its rules.
- Model Registry, with a generic package registry fallback when the job token is refused.
- Releases (REST API), Environments, Pages, pipeline schedules, SAST and Secret Detection.
- Python: scikit-learn, CodeCarbon, picklescan, pip-audit. Synthetic, seeded data only.

## Try it

The two flows are public in the AI Catalog. Enable **ModelGate** with a Pipeline events >
Passed trigger, copy the pipeline side, and point it at your data (`docs/INSTALL.md`).
Reviewers can ask **ModelGate Ask** "what would make this GO?" on any ModelGate merge request.

## Challenges

- Keeping the agent honest. Code renders every number. The commit step re-renders the card and
  refuses any change outside the two blocks the agent writes, and it refuses text that calls a
  failed rule passed.
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

Results from this project's runs:

| Quantity | Value |
| --- | --- |
| SCI per evaluated candidate | `<from reports/sci.json>` gCO2e |
| Carbon to reach the promoted version | `<from the Release>` gCO2e over `<n>` candidates |
| Candidates stopped before shipping | `<NO-GO and NEEDS REVIEW count>` |

Three more things cut or account for carbon:

- **Training only when the model can change.** Branch pipelines skip `train_evaluate` when no
  file that affects the model changed, and the flow stops instead of retraining in its
  sandbox. Pushes skipped this way: `<count from the project's pipelines>`.
- **Carbon by verdict.** The dashboard splits SCI by verdict and estimates the promotion
  retrains that rejected candidates did not trigger: `<from the dashboard>` gCO2e.
- **The flow's own cost**, in GitLab Credits per run: `<from the Credits dashboard>`. It is
  reported next to SCI, never added to it.

The gate turns carbon into a decision. `carbon_efficiency` sends a candidate to review when its
SCI per 0.01 AUC gained exceeds 2.0 gCO2e: a retrain that costs carbon for a noise-level gain.
Every number has two significant figures. `docs/SCI.md` lists every assumption with its
source, and what is not measured: the Duo flow's own run, image pulls, and network transfer.

## Built with

GitLab CI/CD, GitLab Duo Agent Platform, GitLab Model Registry, GitLab Releases, GitLab
Environments, GitLab Pages, Python, scikit-learn, CodeCarbon, picklescan, pip-audit.

## License

MIT.
