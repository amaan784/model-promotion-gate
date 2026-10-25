# Demo video script (target 2:40; the rules require under 3:00)

Record after the GitLab runs in `docs/SETUP.md` steps 5, 5b and 6 to 10 exist: the three demo
merge requests, the merged GO candidate, the Release, the environment, a ModelGate Ask reply
and at least two drift points on the dashboard. Every number on screen comes from those pages,
and the numbers in the narration are the ones the pages show (the runs are deterministic, so
they match `STATUS.md`). Read the narration as written, or shorten it, but do not add claims
the pages do not show.

| Time | On screen | Narration |
| --- | --- | --- |
| 0:00 to 0:15 | The NO-GO merge request: title "NO-GO: model 0.2.0", label `modelgate::no-go`, the rules table with `no_regression` and `calibration` marked FAIL. | "A GitLab Duo flow opened this merge request, not a person. Someone dropped one feature from a credit model. The pipeline retrained it, and the gate said no: AUC fell 0.0324 and calibration got worse." |
| 0:15 to 0:30 | The model card of the same merge request, scrolled slowly. | "Small ML teams on GitLab review every line of code, then ship a retrained model because one number went up. Nobody checks each customer segment, writes the model card, or knows what the run cost in carbon. ModelGate gives every model a merge request." |
| 0:30 to 0:50 | Sped up: a push to a branch, its pipeline (`train_evaluate`, `unit_tests`, `semgrep-sast`, `secret_detection`, `register_challenger`) turning green, the flow session starting, the merge request appearing with its label. | "A person pushes a training change. CI trains the challenger, scores it overall and for each segment, scans the model file and the dependency lock, measures its energy, and computes the verdict from published rules. When the pipeline passes, a Pipeline events trigger starts the flow. That is supervised autonomy: the agents run every step, and a person approves the outcome by merging." |
| 0:50 to 1:15 | The NEEDS REVIEW merge request: overall AUC +0.0161, then segment C at -0.1360 and `slice_gap` FAIL. A reviewer asks ModelGate Ask "what would make this GO?" and the reply names the slice_gap shortfall. | "This one is harder to catch. Swapping a feature raised overall AUC, but segment C got much worse. A headline metric would have shipped it. The slice rule sends it to a reviewer, and ModelGate Ask answers their questions from the decision record." |
| 1:15 to 1:30 | The flow session log: the pipeline lookup, `prepare`, `commit`, the merge request call. | "The agent finds the pipeline, stops when there is nothing to package, writes the analysis and opens or updates the merge request. Code renders the card. The commit is refused if the agent edits anything else, uses a number that is not in the card, or calls a failed rule passed." |
| 1:30 to 2:00 | The GO merge request, then merge it. The `main` pipeline: `promote`, `deploy_production`, `pages`. Open the Release `model-v0.2.0`, then **Operate > Environments > production**, then the "Promotion complete" note. | "A person merges the GO candidate. CI confirms that the default branch reproduces the approved metrics exactly, registers the version, publishes a Release with the model card, and records a production deployment. The flow posts the links back on the merge request." |
| 2:00 to 2:25 | The Pages dashboard: champion, candidate table, carbon by verdict and the avoided retrain, drift chart and the alert. | "The dashboard shows the champion, the candidates that reached main and what each cost in grams of CO2 under the Software Carbon Intensity standard, including the retrain a stopped candidate avoided. Tonight the inputs drifted past the PSI limit, so the dashboard raises a drift alert. Retraining is a person's call." |
| 2:25 to 2:40 | The repository README, then `docs/SCI.md`. | "ModelGate needs no CI/CD variables and no personal tokens. The rules, the flow and the carbon method are all in the repository." |

## Shots to capture first

1. One push followed live, sped up: its pipeline, the flow session starting, the merge request
   appearing. Capture it during the second demo-go push in `docs/SETUP.md` step 10.
2. The NO-GO, NEEDS REVIEW and GO merge requests (full description visible).
3. The ModelGate Ask question and reply on the NEEDS REVIEW merge request.
4. One flow session log (the session page or its CI job log).
5. The `main` pipeline after merging, the Release page, the production environment page.
6. The Pages dashboard with at least two drift points and the alert. A drift alert needs a run
   dated 2026-10-26 UTC or later: PSI is 0.0939 on 2026-10-25 and 0.1345 on 2026-10-26, against
   the 0.1 limit. A manual **Run** of the schedule counts.

## Timeline

| When (UTC) | What |
| --- | --- |
| 2026-10-25 | Schedule created and run once (first drift point); demo runs done |
| 2026-10-26, after that day's schedule run | Record. Check the exported file is under 3:00. |
| 2026-10-26, by 18:00 | Upload to YouTube as **Unlisted** or **Public** (not Private). Processing can take hours. Open the link in a logged-out window to check it plays. |
| 2026-10-27, before 13:00 | Paste the link into the Devpost form and `docs/SUBMISSION.md`. Never replace the video after the deadline. |

## Rules for the recording

- No music, unless it is music you have the rights to use. No third-party logos or trademarks
  beyond what the GitLab pages show: record a clean, full-screen browser window.
- If an AI voice reads the narration, use a voice service whose terms allow a public YouTube
  video.
- Show GitLab pages running for real, not slides or mockups.

## Do not say

- That the flow decides whether a model ships. It does not; the rules and a person do.
- That the carbon figure is measured with hardware counters. It is CodeCarbon's estimate with
  the runner's vCPU share of power (see `docs/SCI.md`).
- That the data is real. It is synthetic, generated from a fixed seed.
- That every push trains. Only a training change, `[train]` or a manual run does.
