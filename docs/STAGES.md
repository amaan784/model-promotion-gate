# Lifecycle stages

GitLab's DevSecOps lifecycle has nine stages. This table says what ModelGate does in each one
and where the proof is. A proof is a link to a real GitLab page from this project: a pipeline,
a job, a merge request, a Release, an environment, the Pages site. Until a stage has one, it
says **pending** and the README does not list it.

| Stage | What ModelGate does | GitLab feature | Proof |
| --- | --- | --- | --- |
| Plan | Not covered. ModelGate starts at the training change; it does not plan work. | | not covered |
| Create | A training change on a branch is the unit of work. The flow writes the model card and the decision record onto the same branch, and opens or updates one merge request per branch, titled and labelled by the verdict. A second flow, ModelGate Ask, answers reviewers' questions on the merge request when mentioned. | Merge requests, labels, two GitLab Duo custom flows (Pipeline events and Mention triggers) | pending: the GO, NEEDS REVIEW and NO-GO merge requests |
| Verify | Every training change (to `config/`, `modelgate/` or `requirements.txt`, a `[train]` commit, or a manual run) trains the challenger and evaluates it on a fixed holdout, overall and per segment. Six published rules give a deterministic GO, NEEDS REVIEW or NO-GO. The unit tests run alongside. | CI/CD pipelines, JUnit test report | pending: a branch pipeline with `train_evaluate` and `unit_tests` |
| Package | Each candidate is registered as `<version>-rc.<pipeline>`. The promoted model is registered as `<version>`, in the Model Registry when the job token is accepted and in the generic package registry otherwise. | Model Registry, package registry | pending: the registered candidate and final version |
| Secure | picklescan checks the model file against a module allowlist. pip-audit checks the dependency lock, with OSV severities, and a HIGH or CRITICAL finding blocks the verdict. SAST and secret detection run on every branch pipeline. | SAST and Secret Detection templates | pending: `semgrep-sast`, `secret_detection` and `reports/scan.json` |
| Release | Merging a GO candidate triggers `promote`. It confirms that the default branch reproduces the approved metrics exactly, then creates the Release `model-v<version>` with the model card as its notes. | Releases (REST API with `CI_JOB_TOKEN`) | pending: the Release page |
| Configure | Everything the automation runs on is configuration in the repository, applied by CI and the flow. The `production` environment, its tier and its URL (the dashboard) are declared in `.gitlab-ci.yml`, and `deploy_production` records each promoted version there. The flow's own runtime, its Python setup, cache and network allow-list, is `.gitlab/duo/agent-config.yml`. The gate rules, the SCI constants and the reviewers are `config/*.yaml`. | Environments and deployments, Agent Platform runtime configuration | pending: the production environment's deployment list |
| Monitor | A nightly schedule scores a drifted batch with the champion: AUC on the batch against the holdout, and PSI per feature, with an alert past the limits in `config/thresholds.yaml`. The dashboard charts the AUC history and lists the latest PSI per feature, and the flow writes the champion's latest drift result into every new model card. | Pipeline schedules, Pages | pending: a scheduled `drift_check` job and the Pages drift chart |
| Govern | The gate rules are versioned config, and every candidate's rule results are committed as a decision record. A person must merge: the flow requests a review from the people in `config/review.yaml` on every GO and NEEDS REVIEW merge request, and CODEOWNERS makes them owners of the champion, the gate rules and the flow's instructions. The flow can never merge or change settings. Every candidate's SCI is reported, and so is the total carbon spent to reach each version. | CODEOWNERS, code owner approval on the protected branch, review requests, audit trail in git | pending: a decision record and a Release with its cumulative SCI |

When a stage gets its proof, replace **pending** with the link, and add the stage to the
README's stage map.
