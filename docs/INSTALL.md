# Use ModelGate in your own project

This is for a team that wants the same gate on its own model. `docs/SETUP.md` covers setting
up this repository itself. Everything below runs on GitLab-hosted runners with no CI/CD
variables.

## 1. Enable the flows from the AI Catalog

Once the ModelGate flows are published as Public (`docs/SETUP.md`, step 4), any project can
enable them without copying the YAML:

1. **Search or go to > Explore > AI Catalog > Flows**, open **ModelGate**, select **Enable**
   and choose your project.
2. Under **Add triggers**, select **Pipeline events**, run when **Passed**.
3. Optionally do the same for **ModelGate Ask** with a **Mention** trigger.

Enabling needs the Maintainer or Owner role in your project, and triggers need Premium or
Ultimate.

## 2. Copy the pipeline side

The flows call `python -m modelgate.flow` and read the files CI produces, so your repository
needs the same pieces:

| Copy | Why |
| --- | --- |
| `modelgate/` | data, training, scan, carbon, gate, card, flow helper, promotion, dashboard |
| `config/` | the gate rules, training config, SCI constants, reviewers |
| `.gitlab-ci.yml` | the jobs the flow expects: `train_evaluate`, `register_challenger`, `promote`, `deploy_production`, `pages`, `drift_check` |
| `.gitlab/duo/agent-config.yml` | the flow's Python environment; must be on your default branch |
| `AGENTS.md` | the flow's rules; edit the "What ModelGate is" section for your model |
| `requirements.in`, `requirements.txt` | the dependency lock pip-audit checks |
| `models/cards/`, `models/decisions/` | empty folders the flow writes into |

Keep the job names: the flow helper looks them up by name.

## 3. Point it at your data

ModelGate ships a seeded synthetic generator (`modelgate/data.py`). For your own model, replace
`generate_all` with a loader that writes the same three files into `data/`:

| File | Used by |
| --- | --- |
| `train.csv` | `train.py` |
| `holdout.csv` | `evaluate.py`. Keep it fixed between candidates; the gate flags a changed holdout fingerprint |
| `drift_batch.csv` | for reference; the nightly check draws its own batch in `drift.py`, which you would point at fresh production-like data |

Each file has the columns in `data.COLUMNS`: `row_id`, `segment` (the attribute whose slices
the gate compares), one numeric column per entry in `data.FEATURES`, and the binary label
`data.LABEL`. Edit those three constants to match your data. `config/train.yaml` then lists
the features the model uses.

Use real data only where you are allowed to: the model card, the decision records and the
dashboard publish per-segment metrics.

## 4. Set your rules

- `config/thresholds.yaml`: AUC tolerance, minimum AUC, largest segment gap, calibration
  tolerance, blocking scan severities, carbon per 0.01 AUC, and the drift limits. The
  verdict logic in `modelgate/evaluate.py` reads only this file.
- `config/review.yaml`: who reviews GO and NEEDS REVIEW candidates, then
  `python -m modelgate codeowners`.
- `config/sci.yaml`: change the runner and grid constants if you do not use GitLab's small
  Linux runners in `us-east1`. Every value needs a source.

## 5. Bootstrap

Push a branch whose commit message contains `[train]`. The first candidate has no champion to
compare against; merging its GO merge request makes it 0.1.0.

## Limits to know

- Two model families are supported: scikit-learn logistic regression and gradient boosting
  (`modelgate/train.py`). Anything else needs its own entry in `build_model`.
- The primary metric is AUC for a binary label. Regression or multi-class models need new rules.
- The model file must be a standard pickle that picklescan can parse, with globals from
  sklearn, numpy or scipy only (`modelgate/scan.py`).
- The carbon figures assume GitLab-hosted runners (`docs/SCI.md`).
