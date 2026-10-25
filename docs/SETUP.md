# Setting up ModelGate on GitLab

Everything here is a one-time manual step in the GitLab UI. Nothing needs a CI/CD variable or
a personal access token: pipelines use `CI_JOB_TOKEN`, and the flow uses the token GitLab
gives it.

Do Phase 0 first. Its answers decide which of the later steps you can do yourself.

## Phase 0: what your role allows

Run these in the project GitLab created for you in the hackathon subgroup (contributor
onboarding provisions it, with the "Developer + AI" role and 500 GitLab Credits a month), and
record the answers in `STATUS.md`.

1. **AI > Triggers > New flow trigger.** Does the button work? Creating a trigger needs the
   Maintainer or Owner role, and triggers need Premium or Ultimate.
2. **AI > Flows.** Can you create a custom flow and enable it? This needs Maintainer or Owner.
3. **Settings > General > Visibility, project features, permissions.** Can you set the project
   to **Public**? The submission needs a public project with visible pipelines. Changing
   visibility needs the Owner role, and a project can be no more visible than its parent
   group.
4. **Settings > Repository > Protected branches.** Who may merge into `main`? By default only
   Maintainers. Decide now who merges the GO merge request in step 8: you, or a Maintainer, or
   protection set to "Developers + Maintainers".
5. **Push a hello-world pipeline on a throwaway branch**, not `main`, so that `main` can take
   this repository's history in step 1. Does a job run on a GitLab-hosted runner? Delete the
   branch afterwards.
6. **Run one throwaway flow, then open the GitLab Credits dashboard.** What did it cost? Every
   passed pipeline starts the flow, and the demo below needs about 15 runs, so check that the
   500 credits cover that with room for reruns.
7. **Settings > CI/CD > Variables.** Can you add one? ModelGate needs none; this only tells you
   your role.

If 1, 2 or 3 fail, ask the organizers before moving the project: the GitLab Discord channel
`#transcend-hackathon`, or the Devpost discussion board. The Official Rules ask for a public
GitLab project, not this one in particular, but the Duo Agent Platform access and the credits
come with the hackathon group. A project in your own namespace on the Free tier has no
triggers and may have no Agent Platform, so before moving, create, enable and run the flow
there and check which credits pay for it.

## 1. Push the code

From your clone, add the project as a remote and push `main`:

```bash
git remote add gitlab https://gitlab.com/<group>/<subgroup>/<project>.git
```

```bash
git push gitlab main
```

If the push is rejected because the project already has a commit (for example a generated
README), look at what it holds. If nothing in it is needed, merge it in and push again,
keeping ModelGate's README and the root `LICENSE` (GitLab shows it as MIT in the About
section):

```bash
git fetch gitlab
```

```bash
git merge --allow-unrelated-histories gitlab/main
```

The first `main` pipeline trains and gates the baseline and publishes an empty dashboard. It
promotes nothing: `promote` and `deploy_production` run only when `models/champion.json`
exists and changed, and there is no champion yet.

## 2. Project settings ModelGate depends on

- **Settings > General > Visibility, project features, permissions**: Package registry on,
  Model registry on, Pages on, CI/CD on.
- **Settings > Merge requests > Merge method**: keep **Merge commit**. Do not use fast-forward.
  The flow's card commit carries `[skip ci]`. With fast-forward, that commit would become the
  head of `main` and the promotion pipeline would never run.
- **Settings > Merge requests > Merge checks**: if you turn on "Pipelines must succeed", also
  turn on "Skipped pipelines are considered successful". The head commit of every
  flow-updated merge request is a `[skip ci]` card commit.
- **Settings > CI/CD > General pipelines**: keep "Public pipelines" on. If the flow's token
  cannot read job artifacts, the flow helper falls back to reading them anonymously, which
  works only for public pipelines. Without either, it regenerates the reports in its
  sandbox. That is slower, and the SCI figure is then measured on the flow runner, not the
  CI runner.
- **Settings > CI/CD > Job token permissions**: leave the defaults. Every API call ModelGate
  makes from CI uses `CI_JOB_TOKEN`.

## 3. Check the flow runner

**Settings > General > GitLab Duo > CI/CD runner**: the status must be green. Select **Check
again** after any change. Flows run on runners tagged `gitlab--duo`; GitLab-hosted runners on
GitLab.com have it.

`.gitlab/duo/agent-config.yml` must be on `main` before the flow runs anywhere, because GitLab
reads it from the default branch only. Step 1 already pushed it.

## 4. Create the flow (Maintainer or Owner)

1. **AI > Flows > New flow**.
2. Display name `ModelGate`. Description: "Explains the ModelGate verdict, writes the model
   card and opens the merge request."
3. Visibility **Public**, so judges and other teams can enable it from the AI Catalog
   (`docs/INSTALL.md`). Use Private only while testing.
4. Under **Configuration**, select **Flow** and paste `.gitlab/duo/flows/modelgate.yaml`
   exactly as committed.
5. **Create flow**.

## 5. Enable it with a pipeline trigger (Maintainer or Owner)

1. **AI > Flows > Managed**, select **ModelGate**, then **Enable**, and choose this project.
2. Under **Add triggers**, select **Pipeline events**, and under **Run when** select
   **Passed**. Leave the instructions empty; the flow's prompt is self-contained.
3. **Enable**. GitLab creates a service account named `ai-modelgate-<group>` and adds it to
   the project as a Developer.

Alternatively: **AI > Triggers > New flow trigger**. Event **Pipeline events**, run when
**Passed**, service account the flow's, configuration source **AI Catalog**, flow
**ModelGate**.

Only pipelines a person causes fire the trigger. The flow's own `[skip ci]` commits start no
pipeline. A scheduled pipeline belongs to the person who owns the schedule, so it probably
starts a flow run too; the flow stops at once on it, but the run still costs credits.

## 5b. Add ModelGate Ask (Maintainer or Owner; the demo video shows it)

A second flow answers questions on a ModelGate merge request. Create it the same way from
`.gitlab/duo/flows/modelgate-ask.yaml` (display name `ModelGate Ask`, visibility Public),
enable it, and add a
**Mention** trigger. GitLab creates its own service account, `ai-modelgate-ask-<group>`.
Anyone can then write, on a merge request, "@ai-modelgate-ask-<group> why is this NEEDS
REVIEW, and what would make it GO?". The flow replies once, from the decision record committed
to the branch, using each failed rule's margin. It reads and posts one note, nothing else.

## 6. Name the reviewers

Put the GitLab usernames of the people who approve promotions in `config/review.yaml`, then
regenerate the code owners file and commit both. Doing this before the push in step 1 saves a
pipeline; afterwards, put `[skip ci]` in the commit message.

```bash
python -m modelgate codeowners
```

The flow then requests their review on every GO and NEEDS REVIEW merge request. On Premium or
Ultimate, turn on **Settings > Repository > Protected branches > main > Code owner approval**
too. A merge that changes `models/champion.json`, `config/thresholds.yaml`, the flow
definition or `AGENTS.md` then needs an owner's approval. Without reviewers the flow requests
none, and a NEEDS REVIEW description says so.

## 7. Bootstrap the first champion (v0.1.0)

```bash
git checkout -b bootstrap-champion
```

```bash
git commit --allow-empty -m "Train the baseline champion [train]"
```

```bash
git push gitlab bootstrap-champion
```

The commit changes nothing, so `[train]` in its message tells CI to train anyway; branch
pipelines otherwise train only when `config/`, `modelgate/` or `requirements.txt` changed
against `main`. The branch pipeline gates the baseline with no champion to compare against. Rules that need
one show `bootstrap`, and the verdict is GO. The flow commits `models/cards/0.1.0-rc.<n>.md`,
the decision record and `models/champion.json`, then opens **GO: promote model 0.1.0**.

## 8. Merge, and watch the promotion

Merge the merge request (a Maintainer must do this unless `main`'s protection lets Developers
merge). The `main` pipeline then:

1. `train_evaluate` retrains on `main`;
2. `promote` checks that `main` reproduces the approved candidate's config, holdout and
   metrics exactly, registers version 0.1.0, and creates the Release `model-v0.1.0` with the
   model card as its description. If `main` does not reproduce the candidate (for example,
   another merge changed the training code in between), it fails and releases nothing;
3. `deploy_production` records a deployment in **Operate > Environments > production**;
4. `pages` republishes the dashboard.

The passed `main` pipeline triggers the flow again. It finds the merged merge request and posts
**Promotion complete** with the Release, registry version and deployment links.

## 9. Nightly drift schedule

Create it right after the first promotion, so the demo cards and the video have drift points.

**Build > Pipeline schedules > New schedule**: description `Nightly drift check`, interval
pattern `0 3 * * *`, time zone UTC, target branch `main`, activated. Select **Run** once to
test it. Schedule pipelines run only `drift_check` and `pages`: SAST and secret detection are
switched off for schedules, and the flow stops on them. Developers can create schedules; a
schedule runs with its owner's permissions.

Each night's batch is drawn for that date, and its drift grows from nothing on
`drift.start_date` (`config/data.yaml`) to full strength after `drift.ramp_days`. The
dashboard's drift chart gains one point per run date. An alert appears once a feature's PSI
passes `drift.max_psi` in `config/thresholds.yaml`: for this project, from runs dated
2026-10-26 UTC on (PSI 0.0939 on 2026-10-25, 0.1345 on 2026-10-26). A push to `main` does not
reset the chart: its `pages` job carries the history forward.

## 10. The demo runs

Each starts from an up-to-date `main` (after step 8), on its own branch:

```bash
git checkout main && git pull gitlab main && git checkout -b demo-no-go
```

```bash
python -m modelgate scenario bad-auc
```

```bash
git commit -am "Drop late_payments as redundant with debt_ratio" && git push gitlab demo-no-go
```

Repeat with `demo-needs-review` and `python -m modelgate scenario bad` ("Replace region_score
with txn_velocity"). Expected verdicts against the 0.1.0 champion: NO-GO and NEEDS REVIEW.
Leave both merge requests open as evidence.

`demo-go` takes two pushes, so that the merged branch also carries a stopped candidate and the
dashboard's carbon-by-verdict split and avoided-retrain figure have one to count:

1. On a new branch `demo-go`, run `python -m modelgate scenario bad`, commit ("Replace
   region_score with txn_velocity") and push. Wait for the flow's card commit
   (NEEDS REVIEW).
2. `git pull gitlab demo-go`, then run `python -m modelgate scenario good`, commit ("Keep
   region_score and add txn_velocity") and push. Record this push for the video: the
   pipeline, the flow session, the merge request turning into **GO: promote model 0.2.0**.
3. Merge it. Step 8 repeats for 0.2.0.

Last, show the training skip. On a new branch from `main`, change one line in `docs/`,
commit and push. The pipeline has no `train_evaluate` or `register_challenger` job, and the
flow's session ends with `no_candidate`. Count such pipelines for the Environmental section of
`docs/SUBMISSION.md`. Close the branch without merging.

## 11. Pages

After the first `main` pipeline with a `pages` job, **Deploy > Pages** shows the site URL.
GitLab.com publishes it publicly unless Pages access control is on. The `production`
environment links to it.

## 12. Before the deadline (2026-10-27 13:00 UTC)

1. Fill `docs/STAGES.md`, the README stage map, `docs/SUBMISSION.md`, `flow_credits_per_run`
   in `config/sci.yaml` and the "Verified on GitLab" column of `STATUS.md`. Commit them to
   `main` by about 12:00 UTC with `[skip ci]` in the message.
2. Check from a logged-out browser: the project is public, the About section shows MIT, the
   pipeline list is visible, the Pages site loads.
3. Deactivate the nightly schedule before 13:00 UTC, so nothing changes during judging.
4. After 13:00 UTC change nothing until the winners are announced (about 2026-11-16): no
   commits, no pipeline reruns, no new video. Fork the project to keep working.

## If you only have the Developer role

| Step | Needs | What to do with Developer only |
| --- | --- | --- |
| Pipelines, schedules, branches, merge requests | Developer | works |
| Create and enable the flow, create the trigger | Maintainer or Owner | ask a Maintainer to do steps 4 and 5 once (about five minutes); everything else stays yours |
| Start the flow without a trigger | Developer, once a Maintainer has created and enabled the flow | start it from GitLab Duo Agentic Chat with the flow's slash command and the passed pipeline's URL as the goal; the flow reads the pipeline from its goal. A person then starts each run, so the demo would no longer show a pipeline-triggered flow; reword the README, the video and the submission if you use this |
| Merge into `main` | depends on branch protection | a Maintainer merges, or protection allows "Developers + Maintainers" to merge |
| Model Registry | Developer, but only documented for access tokens | automatic: `register_challenger` falls back to the generic package registry and records why in `reports/registry.json` |
| CI/CD variables | Maintainer | not needed anywhere |
