# AGENTS.md

Instructions for GitLab Duo working in this repository: the ModelGate flow
(`.gitlab/duo/flows/modelgate.yaml`) and Duo Chat. The flow's prompt holds the step-by-step
procedure; this file holds the rules and the reference material behind it.

## What ModelGate is

ModelGate treats a trained model like code. A person changes the training code on a branch.
CI trains a challenger, evaluates it on a fixed synthetic holdout set (overall and per
segment of the protected attribute `segment`), scans the model file and the dependency lock,
measures the run's carbon cost as an SCI figure, and computes a verdict from the published
rules in `config/thresholds.yaml`. The flow turns that into a model card and a merge request.
A person merges. Merging a GO candidate promotes it.

The project is called ModelGate. Never rename it in anything you write.

## Rules that always apply

1. **Never say a rule passed when the report says it failed, or the reverse.** The verdict
   and every rule result come from `reports/gate_report.json`, computed by
   `modelgate/evaluate.py`. You explain the verdict; you never decide, soften or override it.
2. **Never change or invent a number.** Every number you mention must appear in the reports
   or the rendered card. Round only the way the card does: 4 decimals for metrics, 2
   significant figures for carbon.
3. **Write only inside the two marked blocks**: the Analysis block of the model card and the
   Summary block of `reports/mr_description.md`. `python -m modelgate.flow commit` re-renders
   both files and rejects any other difference, and rejects text that calls a failed rule
   passed.
4. **Never merge or approve a merge request, push to the default branch, or change project
   settings or CI/CD variables.** A person merges. The flow runs with the Developer role.
5. **Call the project ModelGate.** The commit step rejects prose that names it anything else
   (code spans such as `modelgate.flow` are fine).
6. **Stop on failure.** If a `modelgate.flow` command fails, report its output. Do not work
   around it with other tools.
7. No emojis and no marketing language. Plain sentences, short paragraphs.

## Where the facts are

| File | What it holds |
| --- | --- |
| `reports/gate_report.json` | `verdict`, `failed_rules`, `rules` (each with `id`, `status`, `passed`, `value`, `threshold`, `note`), `challenger` (config and metrics), `champion` (or null on the first run), `comparison` (`auc_delta`, `brier_delta`, `segment_auc_delta`), `proposed_version_bump` (`kind`, `from`, `to`, `reasons`), `inputs.champion_source`, `notes` |
| `reports/metrics.json` | holdout metrics: overall `auc`, `f1`, `accuracy`, `brier`, `ece`; per segment `n`, `positive_rate`, `auc`, `brier`; `segment_auc_gap` |
| `reports/sci.json` | `sci_gco2e_per_candidate`, `operational_gco2e`, `embodied_gco2e`, `energy_kwh`, `job_seconds`, and every assumption with its source |
| `reports/scan.json` | picklescan (`scan_error`, `dangerous_globals`, `unexpected_globals`) and pip-audit (`vulnerabilities` with OSV severity) |
| `reports/registry.json` | how the candidate was registered: `model_registry` or `generic_package`, with every HTTP attempt |
| `models/decisions/<candidate>.json` | the decision record for one candidate, built from the reports above |
| `models/champion.json` | the current champion on the default branch |
| `config/thresholds.yaml` | the rules and their thresholds (the decision record repeats them, with the file's sha256) |
| `docs/SCI.md` | how the carbon figure is computed and what it leaves out |

A candidate is named `<proposed version>-rc.<pipeline IID>`, for example `0.2.0-rc.17`.

## What each rule means

| Rule | Plain language | Verdict if it alone fails |
| --- | --- | --- |
| `no_regression` | The challenger's AUC must not fall more than 0.005 below the champion's. | NO-GO |
| `min_absolute_auc` | The challenger's AUC must be at least 0.80, whatever the champion scored. | NO-GO |
| `slice_gap` | The best and worst segment AUCs must be within 0.05 of each other. A model can improve overall while getting worse for one segment; this rule catches that. | NEEDS REVIEW |
| `calibration` | The Brier score must not rise more than 0.01 above the champion's: predicted probabilities must stay about as trustworthy. | NO-GO |
| `scan_clean` | picklescan must parse the whole model file and find nothing dangerous or unexpected, and pip-audit must report no HIGH or CRITICAL advisory. A scan that did not run fails. | NO-GO |
| `carbon_efficiency` | When AUC improves, the run's SCI divided by the gain (in units of 0.01 AUC) must not exceed 2.0 gCO2e. It catches retrains that cost carbon for a noise-level gain. | NEEDS REVIEW |

Statuses: `pass`, `fail`, `bootstrap` (no champion yet, so the rule cannot apply),
`not_applicable` (carbon rule when AUC did not improve), `skipped` (carbon rule without
`sci.json`). Only `fail` counts against the verdict.

## The model card

`modelgate/card.py` renders `models/cards/<candidate>.md` from the decision record. Its
sections, in order:

1. Header table: verdict, failed rules, proposed version, champion, commit, pipeline, registry
2. Overview: model family, output schema, features, hyperparameters, bump reasons
3. Intended use
4. Data: synthetic, seeded; row counts, segment shares, holdout fingerprint
5. Metrics (holdout): challenger against champion, overall and per segment
6. Calibration
7. Carbon (SCI), including gCO2e per 0.01 AUC gained and AUC gained per gCO2e
8. Champion drift: the champion's latest nightly drift check, if one has run
9. Scan
10. Gate rules: every rule with status, value, threshold and detail, and the sha256 of
    `config/thresholds.yaml`
11. Verdict
12. **Analysis: the only section you write**
13. Lineage: project, branch, commit, pipeline, where the reports and the champion came from

The Analysis covers, in at most 300 words:

- what changed, taken from the bump reasons, and whether the champion's drift (the Champion
  drift section) is part of the reason for this candidate;
- challenger against champion: overall AUC, every segment whose AUC moved by more than 0.01,
  and the Brier change;
- each failed rule in plain language, with its value and threshold, and the most likely cause;
- the carbon cost: SCI per candidate and, when AUC improved, gCO2e per 0.01 AUC gained and
  AUC gained per gCO2e (accuracy per gram);
- a recommendation: for GO, why promoting is reasonable; for NEEDS REVIEW, what exactly a
  reviewer should check; for NO-GO, what to change before trying again;
- optionally, an argument for a different version bump. The merge request still uses the
  computed one.

## The merge request

| Verdict | Title | Label |
| --- | --- | --- |
| GO | `GO: promote model <version>` | `modelgate::go` |
| NEEDS_REVIEW | `NEEDS REVIEW: model <version>` | `modelgate::review` |
| NO_GO | `NO-GO: model <version>` | `modelgate::no-go` |

On GO and NEEDS REVIEW the flow also requests a review from the people in
`config/review.yaml` (`mr.reviewers` in the prepare output); a person decides. The description
names them.

`<version>` is the proposed final version (for example `0.2.0`), not the candidate name. The
description is `reports/mr_description.md`, used unchanged once its Summary block holds three
lines: the verdict and its main reason, the biggest driver, the recommendation. There is one
merge request per branch; later pipelines on the same branch update it.

## Answering questions (ModelGate Ask)

`.gitlab/duo/flows/modelgate-ask.yaml` runs when someone mentions its service account on a
merge request. It reads the newest decision record on the merge request's branch and replies
once, in at most 200 words. The same honesty rules apply. Every rule in the record has a
`margin`: positive is room to spare, negative is the shortfall against the threshold, in the
rule's own units. Use it to answer "what would make this GO?", without promising an outcome.
A reply never changes a verdict; only a new pipeline does.

## Commits

The flow commits with `python -m modelgate.flow commit`, never by hand. The message is
`[skip ci] modelgate: card for <candidate> (<VERDICT>)`. `[skip ci]` keeps the card commit from
retraining. The commit changes only `models/cards/`, `models/decisions/` and
`models/champion.json`. On GO, `models/champion.json` becomes this candidate, which is what
merging promotes. Otherwise it is reset to the default branch's copy.

## Version bumps

Computed in `modelgate/evaluate.py`: major when `model_family` or `output_schema` changes,
minor when the feature list changes (a feature added, removed or reordered), patch for anything else (hyperparameters, a plain
retrain). The first champion is 0.1.0.
