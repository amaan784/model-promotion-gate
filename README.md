# ModelGate

Every model gets a merge request.

## Problem

Small ML teams that already use GitLab often ship models without the controls they apply to
code. A retrained model goes out because its headline metric went up. Nobody checks whether
one customer segment got worse, the model card is written after the fact (if at all), and
nobody knows what the training run cost in energy.

ModelGate treats a trained model like code. The audience is small ML teams on GitLab that do
not run a separate MLOps platform.

## How it works

A training-code change produces a challenger model. The challenger is scored on a fixed,
seeded holdout set, including per-segment AUC for a protected attribute. The gate then
computes a deterministic GO / NEEDS_REVIEW / NO_GO verdict from the published rules in
[config/thresholds.yaml](config/thresholds.yaml):

| Rule | Passes when |
| --- | --- |
| no_regression | challenger AUC >= champion AUC - 0.005 |
| min_absolute_auc | challenger AUC >= 0.80 |
| slice_gap | largest per-segment AUC gap <= 0.05 |
| calibration | challenger Brier <= champion Brier + 0.01 |
| scan_clean | picklescan clean and no HIGH/CRITICAL pip-audit finding; a missing scan fails |
| carbon_efficiency | gCO2e per 0.01 AUC gained <= 2.0 (only when AUC improves) |

GO means every rule passes. NEEDS_REVIEW means only `slice_gap` or `carbon_efficiency`
failed. NO_GO means anything else failed. On the first run there is no champion yet; rules
that need one are marked `bootstrap` and the proposed version is 0.1.0.

Before the verdict, the model file is checked with picklescan, the dependency lock with
pip-audit, and the run's energy is converted to a Software Carbon Intensity figure
(gCO2e per evaluated candidate). [docs/SCI.md](docs/SCI.md) gives the method and every
assumption with its source.

On GitLab the loop is:

1. A person pushes a training change to a branch.
2. The pipeline ([.gitlab-ci.yml](.gitlab-ci.yml)) trains, scans, measures and gates the
   challenger, and registers it as `<version>-rc.<pipeline>`.
3. The passed pipeline triggers a GitLab Duo custom flow
   ([.gitlab/duo/flows/modelgate.yaml](.gitlab/duo/flows/modelgate.yaml)). It writes the
   model card and opens or updates the merge request, titled by the verdict.
4. A person merges. On GO and NEEDS REVIEW the flow requests a review from the people in
   `config/review.yaml`, and CODEOWNERS can require their approval. Merging a GO candidate
   makes it the champion in `models/champion.json`.
5. The `main` pipeline checks that it reproduces the approved candidate exactly, registers the
   final version, creates the Release `model-v<version>` with the model card, records a
   `production` deployment in GitLab Environments, and republishes the Pages dashboard. The
   flow then posts "Promotion complete" on the merged merge request.

A second flow, ModelGate Ask ([.gitlab/duo/flows/modelgate-ask.yaml](.gitlab/duo/flows/modelgate-ask.yaml)),
answers questions when someone mentions it on the merge request ("what would make this GO?"),
from the committed decision record.

The flow's agent decides which pipeline it is looking at, stops on pipelines that need no
merge request, and creates or updates the merge request. It writes only two blocks of text:
the card's Analysis and the merge request's three-line Summary. Every file and git operation
comes from `python -m modelgate.flow`, and code renders every table and figure in the card.
The commit step rejects the agent's text if it changed anything outside its two blocks, used
a number that is not in the card or the decision record, or plainly called a failed rule
passed. [AGENTS.md](AGENTS.md) holds its rules, and [docs/SETUP.md](docs/SETUP.md) the
one-time GitLab setup.

A nightly schedule scores a drifted batch with the champion, and the Pages dashboard shows the
champion, the verdict and carbon cost of every candidate whose record reached `main`, and the
drift history. Branch pipelines train only when something that can change the model changed,
so a docs-only push costs no training run.

## Stage map

No lifecycle stage has a proof link yet, because nothing has run on GitLab. Stages appear here
only once they do. [docs/STAGES.md](docs/STAGES.md) lists all nine with what ModelGate does in
each, and [STATUS.md](STATUS.md) records what is implemented and what has been verified.

## Run locally

Python 3.11 or 3.12, git, and network access for pip-audit. `requirements.txt` is a full lock
compiled from `requirements.in`; install it into a fresh virtual environment, and run pip as
`python -m pip`, because the lock pins pip itself.

```bash
python -m pip install -r requirements.txt
```

```bash
python -m modelgate reports
```

```bash
python -m pytest
```

`python -m modelgate reports` is what CI's `train_evaluate` job runs. It generates the
synthetic data, trains the challenger, scans it, evaluates it under CodeCarbon, and writes to
`reports/`:

| File | Contents |
| --- | --- |
| `metrics.json` | holdout metrics, overall and per segment |
| `scan.json` | picklescan and pip-audit results, with OSV severities |
| `energy.json`, `sci.json` | CodeCarbon readings and the SCI per candidate |
| `gate_report.json` | every rule's result, the verdict, the proposed version |
| `champion_candidate.json` | what `models/champion.json` becomes if this candidate is merged |

It takes about 12 seconds, most of it CodeCarbon start-up and the pip-audit network calls;
`reports` plus the full test suite (what `make all` runs) takes about a minute on a laptop.
`make reports`, `make test` and `make all` do the same where make is installed.

The flow's steps can be run by hand too. `prepare` needs a git checkout with an `origin`
remote and the pipeline's details. When the pipeline's artifacts cannot be downloaded, it
regenerates the reports, comparing against `origin/main`'s champion:

```bash
python -m modelgate.flow prepare --project-url https://gitlab.com/<namespace>/<project> --project-id <id> --pipeline-id <id> --pipeline-iid <iid> --ref <branch> --sha <sha>
```

It writes the card, the decision record and `reports/mr_description.md` and stages them.
`python -m modelgate.flow commit --candidate <version>` refuses to commit until the Analysis
and Summary blocks are written and nothing else was edited.

### Demo scenarios

Each scenario rewrites the feature list in `config/train.yaml`. The expected verdicts assume
the v0.1.0 baseline is the champion; `tests/test_scenarios.py` checks them end to end.

| Command | Change | Verdict |
| --- | --- | --- |
| `python -m modelgate scenario good` | adds `txn_velocity` | GO, AUC +0.034, minor bump |
| `python -m modelgate scenario bad` | swaps `region_score` (segment C only) for `txn_velocity` | NEEDS_REVIEW: AUC +0.016, segment C AUC -0.136 |
| `python -m modelgate scenario bad-auc` | drops `late_payments` | NO_GO: AUC -0.032, Brier +0.013 |
| `python -m modelgate scenario baseline` | restores the baseline features | |

## Setup on GitLab

[docs/SETUP.md](docs/SETUP.md) has every step with the exact UI paths. In short:

1. Run the Phase 0 checklist to learn what your role allows. Creating the flow and its
   trigger needs Maintainer or Owner, and triggers need Premium or Ultimate.
2. Push the repository to a public GitLab project. Keep the merge method on "Merge commit".
   No CI/CD variables are needed.
3. Create the flow under **AI > Flows** from `.gitlab/duo/flows/modelgate.yaml`, enable it for
   the project, and add a **Pipeline events > Passed** trigger.
4. Push an empty commit whose message contains `[train]` on a new branch. The flow opens
   **GO: promote model 0.1.0**; merging it makes the first champion, and the `main` pipeline
   releases it.
5. Add the nightly `Nightly drift check` schedule on `main`, run the demo scenarios on their
   own branches, and open the Pages site.

To put the same gate on your own model, see [docs/INSTALL.md](docs/INSTALL.md): enable the
public flows from the AI Catalog, copy the pipeline side, and point it at your data.

## Documents

| File | What it is |
| --- | --- |
| [docs/SETUP.md](docs/SETUP.md) | one-time GitLab setup, the Phase 0 checklist, the demo runs |
| [docs/INSTALL.md](docs/INSTALL.md) | using ModelGate on your own model and data |
| [docs/SCI.md](docs/SCI.md) | the carbon method and every assumption with its source |
| [docs/STAGES.md](docs/STAGES.md) | the nine lifecycle stages and their proof links |
| [AGENTS.md](AGENTS.md) | the rules for the GitLab Duo flow |
| [STATUS.md](STATUS.md) | implemented, verified on GitLab, verified locally |
| [docs/VIDEO.md](docs/VIDEO.md), [docs/SUBMISSION.md](docs/SUBMISSION.md) | demo script and Devpost draft |

## Data

All data is synthetic, generated from a fixed seed by [modelgate/data.py](modelgate/data.py).
Nothing is downloaded and no row describes a real person.

## License

MIT. See [LICENSE](LICENSE).
