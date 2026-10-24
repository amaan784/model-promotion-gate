# Demo video script (3 minutes)

Record after the GitLab runs in `docs/SETUP.md` steps 6 to 9 exist. Show the three real
merge requests, the Release, the environment and the dashboard. Every number on screen comes
from those pages. Read the narration as written, or shorten it, but do not add claims the
pages do not show.

| Time | On screen | Narration |
| --- | --- | --- |
| 0:00 to 0:20 | The NO-GO merge request: title "NO-GO: model 0.2.0", label `modelgate::no-go`, the rules table with `no_regression` and `calibration` marked FAIL. | "This merge request was opened by a GitLab Duo flow, not a person. Someone dropped one feature from a credit model. The pipeline retrained it and the gate said no: AUC fell 0.032 and calibration got worse. Nobody had to notice." |
| 0:20 to 0:40 | Scroll to the three-line summary, then open the model card. | "ModelGate treats a model like code. Every training change gets a merge request with a verdict, a model card and the evidence. The verdict comes from published rules in the repository, and the agent only explains it." |
| 0:40 to 1:05 | The branch pipeline graph: `train_evaluate`, `unit_tests`, `semgrep-sast`, `secret_detection`, `register_challenger`. Open the `train_evaluate` log at the rule table. | "On every push, CI trains the challenger, evaluates it on a fixed holdout overall and for each customer segment, scans the model file and the dependency lock, measures the run's energy, and computes GO, NEEDS REVIEW or NO-GO. The candidate is registered as a release candidate." |
| 1:05 to 1:35 | The NEEDS REVIEW merge request. Point at overall AUC +0.016, then segment C: -0.136, `slice_gap` FAIL. | "This one is harder to catch. Swapping one feature raised overall AUC. But that feature only mattered for one segment, and for that segment the model got much worse. A headline metric would have shipped it. The slice rule sends it to a person instead." |
| 1:35 to 1:50 | On the NEEDS REVIEW merge request, a reviewer asks ModelGate Ask "what would make this GO?" and the reply names the slice_gap shortfall. Then the flow session log. | "The flow runs when a person's pipeline passes. Code renders every number in the card; the agent writes the analysis and the summary. If it calls a failed rule passed, or edits anything else, the commit is refused." |
| 1:50 to 2:20 | The GO merge request, then merge it. The `main` pipeline: `promote`, `deploy_production`, `pages`. Open the Release `model-v<version>`, then **Operate > Environments > production**, then the "Promotion complete" note. | "A person merges the GO candidate. CI confirms that the default branch reproduces the approved metrics exactly, registers the version, publishes a Release with the model card, and records a production deployment. The flow posts the links back on the merge request." |
| 2:20 to 2:45 | The Pages dashboard: champion, candidate table, carbon bars, drift chart. | "The dashboard shows the champion, every candidate the gate has judged, what each cost in grams of CO2 by the Software Carbon Intensity standard, and a nightly drift check. Here the inputs drifted past the PSI limit, so the champion is flagged for retraining." |
| 2:45 to 3:00 | The repository README and `docs/SCI.md`. | "ModelGate runs on GitLab-hosted runners with no secrets and no paid services. The code, the rules and the carbon method are in the repository." |

## Shots to capture first

1. The NO-GO, NEEDS REVIEW and GO merge requests (full description visible).
2. A branch pipeline graph and the `train_evaluate` log's rule table.
3. One flow run: its session page or its CI job log.
4. The `main` pipeline after merging, the Release page, the production environment page.
5. The Pages dashboard after at least two nightly drift checks.

## Do not say

- That the flow decides whether a model ships. It does not; the rules and a person do.
- That the carbon figure is measured with hardware counters. It is CodeCarbon's estimate with
  the runner's vCPU share of power (see `docs/SCI.md`).
- That the data is real. It is synthetic, generated from a fixed seed.
