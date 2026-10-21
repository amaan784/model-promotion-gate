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

The planned GitLab loop is: a pipeline trains and gates the model, a GitLab Duo custom flow
writes the model card and opens the merge request, a human merges, and CI promotes the
version. Only the local part exists so far. [STATUS.md](STATUS.md) records what is
implemented and what has been verified on GitLab. No lifecycle stage is listed here until it
has a proof link.

## Run locally

Python 3.11 or 3.12.

```bash
pip install -r requirements.txt
```

```bash
python -m modelgate reports
```

```bash
python -m pytest
```

`python -m modelgate reports` generates the synthetic data, trains the challenger, and writes
`reports/metrics.json`, `reports/gate_report.json` and `reports/champion_candidate.json`. It
takes about a second. `make reports`, `make test` and `make all` do the same where make is
installed.

Until the scanner exists (Checkpoint B), the local verdict is NO_GO because
`reports/scan.json` is missing and `scan_clean` fails closed.

## Demo scenarios

Each scenario rewrites the feature list in `config/train.yaml`. The expected verdicts assume
the v0.1.0 baseline is the champion; `tests/test_scenarios.py` checks them end to end.

| Command | Change | Verdict |
| --- | --- | --- |
| `python -m modelgate scenario good` | adds `txn_velocity` | GO, AUC +0.034, minor bump |
| `python -m modelgate scenario bad` | swaps `region_score` (segment C only) for `txn_velocity` | NEEDS_REVIEW: AUC +0.016, segment C AUC -0.136 |
| `python -m modelgate scenario bad-auc` | drops `late_payments` | NO_GO: AUC -0.032, Brier +0.013 |
| `python -m modelgate scenario baseline` | restores the baseline features | |

## Data

All data is synthetic, generated from a fixed seed by [modelgate/data.py](modelgate/data.py).
Nothing is downloaded and no row describes a real person.

## License

MIT. See [LICENSE](LICENSE).
