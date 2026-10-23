"""Deterministic steps of the ModelGate GitLab Duo flow.

The flow's agent runs these with `run_command` (see AGENTS.md):

    .venv/bin/python -m modelgate.flow prepare --project-url URL --project-id ID \\
        --pipeline-id N --pipeline-iid N --ref BRANCH --sha SHA --default-branch main
    .venv/bin/python -m modelgate.flow commit --candidate 0.2.0-rc.17
    .venv/bin/python -m modelgate.flow verify-mr --candidate 0.2.0-rc.17 --mr-iid 5
    .venv/bin/python -m modelgate.flow promotion-note --project-url URL --project-id ID \\
        --pipeline-id N --sha SHA --default-branch main

`prepare` checks out the pipeline's branch, collects the pipeline's reports (CI job
artifacts, or a deterministic regeneration in the sandbox if they cannot be
downloaded) and writes:

    models/decisions/<candidate>.json   decision record
    models/cards/<candidate>.md         model card with an Analysis block to fill in
    models/champion.json                set to this candidate on GO, otherwise reset to
                                        the default branch's copy (removed if it has none)
    reports/mr_description.md           MR description with a Summary block to fill in

`commit` refuses to commit unless the card and description pass `card.check_*`.
`promotion-note` runs on default-branch pipelines: it finds the merged merge request
and builds the "Promotion complete" note from the promote and deploy job artifacts.
Every command prints one JSON object.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import subprocess
import sys
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from modelgate import card
from modelgate.common import (
    Http,
    Paths,
    Response,
    default_paths,
    read_json,
    read_json_if_exists,
    urllib_http,
    write_json,
)

logger = logging.getLogger(__name__)

FLOW_PATHS = ("models/cards/", "models/decisions/", "models/champion.json")
TRAIN_JOB = "train_evaluate"
REGISTER_JOB = "register_challenger"
PROMOTE_JOB = "promote"
DEPLOY_JOB = "deploy_production"
NOTE_PREFIX = "Promotion complete"
REQUIRED_REPORTS = ("gate_report", "metrics", "scan", "sci", "champion_candidate")
DESCRIPTION_FILE = "reports/mr_description.md"


class FlowError(RuntimeError):
    """A flow step failed, such as a git command or report regeneration.

    main catches it and prints it as an "error" result.
    """

    pass


@dataclass(frozen=True)
class Target:
    """The project, pipeline, branch and commit one flow command acts on."""

    project_url: str
    project_id: str
    pipeline_id: int
    pipeline_iid: int
    ref: str
    sha: str
    default_branch: str

    @property
    def api_project(self) -> str:
        """The project's REST API base, <scheme>://<host>/api/v4/projects/<project_id>."""
        parts = urllib.parse.urlsplit(self.project_url)
        return f"{parts.scheme}://{parts.netloc}/api/v4/projects/{self.project_id}"

    @property
    def pipeline_url(self) -> str:
        """The pipeline's web page, <project_url>/-/pipelines/<pipeline_id>."""
        return f"{self.project_url.rstrip('/')}/-/pipelines/{self.pipeline_id}"


# --------------------------------------------------------------------------- git


def git(paths: Paths, *args: str, env: dict[str, str] | None = None) -> str:
    """Run git with args in the repo root and return its stripped stdout.

    env is added on top of the current environment. Raises FlowError with git's stderr
    (or stdout) when git exits non-zero. A run longer than 120 seconds raises
    subprocess.TimeoutExpired instead, which main does not catch.
    """
    out = subprocess.run(
        ["git", *args],
        cwd=paths.root,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env={**os.environ, **(env or {})},
    )
    if out.returncode != 0:
        raise FlowError(f"git {' '.join(args)} failed: {out.stderr.strip() or out.stdout.strip()}")
    return out.stdout.strip()


def sync_branch(paths: Paths, t: Target) -> dict[str, Any]:
    """Fetch the pipeline's branch and the default branch, then check out origin's branch tip.

    The local branch is reset to origin/<ref>, and the pipeline commit is fetched when it is
    missing locally. Returns status "ok" with the head SHA when the tip is the pipeline
    commit or the changes since it touch only FLOW_PATHS (listed in
    flow_commits_since_pipeline). Returns "superseded" with a reason when the branch no
    longer contains the pipeline commit or newer commits change other files: the newer
    pipeline runs the flow again.
    """
    git(
        paths,
        "fetch",
        "--no-tags",
        "origin",
        f"+refs/heads/{t.ref}:refs/remotes/origin/{t.ref}",
        f"+refs/heads/{t.default_branch}:refs/remotes/origin/{t.default_branch}",
    )
    git(paths, "checkout", "-q", "-B", t.ref, f"origin/{t.ref}")
    head = git(paths, "rev-parse", "HEAD")
    if head == t.sha:
        return {"status": "ok", "head": head}
    try:
        git(paths, "cat-file", "-e", f"{t.sha}^{{commit}}")
    except FlowError:
        git(paths, "fetch", "--no-tags", "origin", t.sha)
    try:
        git(paths, "merge-base", "--is-ancestor", t.sha, head)
    except FlowError:
        return {
            "status": "superseded",
            "head": head,
            "reason": "branch no longer contains the pipeline commit",
        }
    changed = [p for p in git(paths, "diff", "--name-only", t.sha, head).splitlines() if p]
    other = [p for p in changed if not p.startswith(FLOW_PATHS)]
    if other:
        return {
            "status": "superseded",
            "head": head,
            "reason": f"newer commits on {t.ref} change {', '.join(other[:5])}",
        }
    return {"status": "ok", "head": head, "flow_commits_since_pipeline": changed}


# --------------------------------------------------------------------- artifacts


def _auth_variants() -> list[tuple[str, dict[str, str]]]:
    variants = []
    token = os.environ.get("GITLAB_TOKEN")
    if token:
        variants.append(("GITLAB_TOKEN bearer", {"Authorization": f"Bearer {token}"}))
    variants.append(("anonymous", {}))
    return variants


@dataclass
class _Api:
    """GET calls against one project, with the first auth variant that worked."""

    t: Target
    http: Http
    attempts: list[dict[str, Any]]
    headers: dict[str, str] = field(default_factory=dict)
    auth: str = ""
    job_names: set[str] | None = None  # every job in the pipeline, once listed

    def get(self, step: str, path: str) -> Response:
        """GET path under the project API with the current headers and return the response.

        Appends the step name, auth variant and status code to attempts, whatever the status.
        """
        resp = self.http("GET", f"{self.t.api_project}{path}", self.headers, None)
        self.attempts.append({"step": step, "auth": self.auth, "status": resp.status})
        return resp

    def connect(self, step: str, path: str) -> Response | None:
        """GET path with each auth variant in turn and keep the first that returns 200.

        Returns that response, with auth and headers left set to the working variant, or
        None when no variant returns 200 (auth and headers then hold the last one tried).
        """
        for auth, headers in _auth_variants():
            self.auth, self.headers = auth, headers
            resp = self.get(step, path)
            if resp.status == 200:
                return resp
        return None

    def successful_jobs(self) -> dict[str, int] | None:
        """Map job name to job ID for the pipeline's successful jobs.

        Connects first when no auth variant has been chosen yet. Reads one page of up to
        100 jobs; when several successful jobs share a name, the highest ID wins. Returns
        None when the job list cannot be read.
        """
        path = f"/pipelines/{self.t.pipeline_id}/jobs?per_page=100"
        resp = self.get("list_jobs", path) if self.auth else self.connect("list_jobs", path)
        if resp is None or resp.status != 200 or not isinstance(resp.body, list):
            return None
        self.job_names = {job.get("name") for job in resp.body}
        ids: dict[str, int] = {}
        for job in sorted(resp.body, key=lambda j: j.get("id", 0)):
            if job.get("status") == "success":
                ids[job["name"]] = job["id"]
        return ids

    def artifact(self, job_id: int, path: str) -> bytes | None:
        """Raw bytes of one file in a job's artifacts, or None unless the response is 200."""
        resp = self.get(f"artifact {path}", f"/jobs/{job_id}/artifacts/{path}")
        return resp.raw if resp.status == 200 else None

    def artifact_by_ref(self, ref: str, path: str, job: str) -> bytes | None:
        """Raw bytes of one artifact file of the named job in ref's latest successful pipeline.

        Uses GitLab's /jobs/artifacts/<ref>/raw endpoint. ref goes into the URL as given, so
        the caller URL-encodes it. Returns None unless the response is 200.
        """
        resp = self.get(f"artifact {path}", f"/jobs/artifacts/{ref}/raw/{path}?job={job}")
        return resp.raw if resp.status == 200 else None


def fetch_artifacts(
    paths: Paths, t: Target, http: Http, attempts: list[dict[str, Any]]
) -> tuple[dict[str, Any], dict[str, int], set[str] | None]:
    """Download the pipeline's reports from its CI job artifacts.

    Fetches reports/<name>.json for the required reports and energy from the
    train_evaluate job and registry from register_challenger, and writes each one that
    parses as JSON to paths.reports. drift is read from the drift_check job of the default
    branch's latest successful pipeline and is not written. Returns the parsed reports by
    name and the pipeline's successful job IDs by job name, or two empty dicts when the
    job list cannot be read. Every request is recorded in attempts.
    """
    api = _Api(t, http, attempts)
    job_ids = api.successful_jobs()
    if job_ids is None:
        return {}, {}, None
    found: dict[str, Any] = {}
    wanted = [(TRAIN_JOB, name) for name in (*REQUIRED_REPORTS, "energy")]
    wanted.append((REGISTER_JOB, "registry"))
    for job_name, report in wanted:
        if job_name not in job_ids:
            continue
        raw = api.artifact(job_ids[job_name], f"reports/{report}.json")
        if raw is None:
            continue
        try:
            found[report] = json.loads(raw)
        except json.JSONDecodeError:
            continue
        out = paths.reports / f"{report}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(raw)
    # The champion's latest nightly drift check, from the default branch's schedule pipeline.
    ref = urllib.parse.quote(t.default_branch, safe="")
    raw = api.artifact_by_ref(ref, "reports/drift.json", "drift_check")
    if raw is not None:
        with contextlib.suppress(json.JSONDecodeError):
            found["drift"] = json.loads(raw)
    return found, job_ids, api.job_names


def regenerate(paths: Paths, t: Target) -> None:
    """Rebuild the train job's reports in the sandbox with `python -m modelgate reports`.

    Runs the data, train, scan, metrics, sci and gate steps in the repo root with
    MODELGATE_CHAMPION_REF set to origin/<default_branch>, so the gate compares against
    the default branch's champion. The registry and drift reports are not rebuilt. Raises
    FlowError with the last five lines of stderr (or stdout) when the command fails; a run
    longer than 900 seconds raises subprocess.TimeoutExpired.
    """
    env = {**os.environ, "MODELGATE_CHAMPION_REF": f"origin/{t.default_branch}"}
    out = subprocess.run(
        [sys.executable, "-m", "modelgate", "reports"],
        cwd=paths.root,
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
        env=env,
    )
    if out.returncode != 0:
        tail = " | ".join((out.stderr or out.stdout).strip().splitlines()[-5:])
        raise FlowError(f"regenerating reports failed: {tail}")


# ---------------------------------------------------------------------- champion


def update_champion(paths: Paths, t: Target, decision: dict[str, Any], reports: dict) -> str:
    """Write models/champion.json for the candidate's verdict and describe what changed.

    On GO the champion_candidate report, plus the commit, pipeline URL, candidate version,
    card path and registry entry, becomes the champion. Otherwise the file is reset to
    the copy on origin/<default_branch>, or deleted when that copy is missing or git
    cannot read it. Returns "set to <version>", "unchanged", "removed: ..." or
    "reset to ...".
    """
    if decision["verdict"] == "GO":
        champion = dict(reports["champion_candidate"])
        champion.update(
            {
                "commit": t.sha,
                "pipeline_url": t.pipeline_url,
                "candidate_version": decision["candidate_version"],
                "card": f"models/cards/{decision['candidate_version']}.md",
                "registered_as": decision.get("registry"),
            }
        )
        write_json(paths.champion_file, champion)
        return f"set to {champion['version']}"
    try:
        text = git(paths, "show", f"origin/{t.default_branch}:models/champion.json")
        main_copy: dict[str, Any] | None = json.loads(text)
    except FlowError:
        main_copy = None
    current = read_json_if_exists(paths.champion_file)
    if current == main_copy:
        return "unchanged"
    if main_copy is None:
        paths.champion_file.unlink(missing_ok=True)
        return f"removed: {t.default_branch} has no champion and this candidate is not GO"
    write_json(paths.champion_file, main_copy)
    return f"reset to {t.default_branch}'s champion {main_copy.get('version')}"


# ---------------------------------------------------------------------- commands


def _decision_path(paths: Paths, candidate: str) -> Path:
    return paths.models / "decisions" / f"{candidate}.json"


def _card_path(paths: Paths, candidate: str) -> Path:
    return paths.models / "cards" / f"{candidate}.md"


def _stage(paths: Paths) -> None:
    """Stage flow output, including a champion.json deletion; skip paths git never saw."""
    targets = [
        p for p in FLOW_PATHS if (paths.root / p).exists() or git(paths, "ls-files", "--", p)
    ]
    if targets:
        git(paths, "add", "-A", "--", *targets)


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def prepare(paths: Paths, t: Target, http: Http = urllib_http) -> dict[str, Any]:
    """Run the prepare command: sync the branch, gather reports and write the flow's files.

    Returns a "superseded" result, before any reports are fetched or flow files written,
    when sync_branch reports the branch has moved on. Otherwise downloads the CI
    artifacts into reports/ and uses them when every required report arrived, and else
    regenerates the reports in the sandbox, keeping any downloaded registry and drift
    reports. Writes the decision record, model card, MR description and
    champion.json, stages the flow paths under models/, and returns a "ready" result
    with the verdict, MR title and labels, next steps and the artifact download attempts.
    """
    branch = sync_branch(paths, t)
    if branch["status"] != "ok":
        return {"status": "superseded", "reason": branch["reason"], "head": branch["head"]}
    attempts: list[dict[str, Any]] = []
    reports, job_ids, job_names = fetch_artifacts(paths, t, http, attempts)
    if job_names is not None and TRAIN_JOB not in job_names:
        # CI skipped training because nothing that can change the model changed. Regenerating
        # here would spend the energy the skip saved, so there is no candidate to package.
        return {
            "status": "no_candidate",
            "reason": f"the pipeline has no {TRAIN_JOB} job: nothing that affects the model "
            "changed against the default branch",
            "artifact_attempts": attempts,
        }
    if all(reports.get(name) for name in REQUIRED_REPORTS):
        source = f"CI artifacts of job {TRAIN_JOB} #{job_ids[TRAIN_JOB]}"
    else:
        regenerate(paths, t)
        kept = {k: reports.get(k) for k in ("registry", "drift")}
        reports = {name: read_json(paths.reports / f"{name}.json") for name in REQUIRED_REPORTS}
        reports.update(kept)
        source = "regenerated in the flow sandbox (CI artifacts could not be downloaded)"
    lineage = {
        "project_url": t.project_url,
        "branch": t.ref,
        "commit": t.sha,
        "pipeline_id": t.pipeline_id,
        "pipeline_iid": t.pipeline_iid,
        "pipeline_url": t.pipeline_url,
        "train_job_id": job_ids.get(TRAIN_JOB),
        "reports_source": source,
    }
    decision = card.build_decision(paths, reports, lineage)
    candidate = decision["candidate_version"]
    card_rel = f"models/cards/{candidate}.md"
    decision["lineage"]["card_url"] = f"{t.project_url.rstrip('/')}/-/blob/{t.ref}/{card_rel}"
    write_json(_decision_path(paths, candidate), decision)
    _write_text(_card_path(paths, candidate), card.render_card(decision))
    _write_text(
        paths.root / DESCRIPTION_FILE,
        card.render_description(decision, decision["lineage"]["card_url"]),
    )
    champion_action = update_champion(paths, t, decision, reports)
    _stage(paths)
    return {
        "status": "ready",
        "candidate_version": candidate,
        "proposed_version": decision["proposed_version"],
        "verdict": decision["verdict"],
        "failed_rules": decision["failed_rules"],
        "reports_source": source,
        "champion_action": champion_action,
        "card_file": card_rel,
        "description_file": DESCRIPTION_FILE,
        "mr": {
            "source_branch": t.ref,
            "target_branch": t.default_branch,
            "title": card.mr_title(decision),
            "label": card.mr_label(decision),
            "remove_labels": sorted(set(card.LABELS.values()) - {card.mr_label(decision)}),
            "reviewers": decision["review"]["reviewers"],
        },
        "next": [
            f"write the Analysis block in {card_rel}",
            f"write the Summary block in {DESCRIPTION_FILE}",
            f"run: .venv/bin/python -m modelgate.flow commit --candidate {candidate}",
        ],
        "artifact_attempts": attempts,
    }


def check(paths: Paths, candidate: str) -> list[str]:
    """Return the problems that keep a candidate's card and MR description from a commit.

    Runs card.check_card on the model card and card.check_description on
    reports/mr_description.md against the decision record, prefixing each problem with
    "card: " or "description: ". An empty list means both pass.
    """
    decision = read_json(_decision_path(paths, candidate))
    card_file = _card_path(paths, candidate)
    description = paths.root / DESCRIPTION_FILE
    problems = [
        f"card: {p}" for p in card.check_card(card_file.read_text(encoding="utf-8"), decision)
    ]
    problems += [
        f"description: {p}"
        for p in card.check_description(
            description.read_text(encoding="utf-8"), decision, decision["lineage"]["card_url"]
        )
    ]
    return problems


def _git_identity() -> dict[str, str]:
    """Git author and committer variables taken from the DUO_WORKFLOW_GIT_* variables.

    Author is the flow's service account, committer the triggering user (GitLab's split).
    Unset or empty variables are left out, so git falls back to its own config for them.
    """
    pairs = {
        "GIT_AUTHOR_NAME": "DUO_WORKFLOW_GIT_AUTHOR_USER_NAME",
        "GIT_AUTHOR_EMAIL": "DUO_WORKFLOW_GIT_AUTHOR_EMAIL",
        "GIT_COMMITTER_NAME": "DUO_WORKFLOW_GIT_USER_NAME",
        "GIT_COMMITTER_EMAIL": "DUO_WORKFLOW_GIT_USER_EMAIL",
    }
    return {k: os.environ[v] for k, v in pairs.items() if os.environ.get(v)}


def commit(paths: Paths, candidate: str, push: bool = True) -> dict[str, Any]:
    """Commit the flow output for a candidate and push it to the candidate's branch.

    Returns "rejected" with the problems when check finds any, and "nothing_to_commit"
    when nothing is staged after staging the flow paths. Otherwise commits with a
    "[skip ci]" message under the identity from the DUO_WORKFLOW_* variables, pushes HEAD
    to the branch in the decision's lineage unless push is False, and returns the commit
    SHA, message and staged files.
    """
    problems = check(paths, candidate)
    if problems:
        return {"status": "rejected", "problems": problems}
    decision = read_json(_decision_path(paths, candidate))
    branch = decision["lineage"]["branch"]
    _stage(paths)
    staged = git(paths, "diff", "--cached", "--name-only")
    if not staged:
        return {"status": "nothing_to_commit", "reason": "the branch already has this card"}
    message = f"[skip ci] modelgate: card for {candidate} ({decision['verdict']})"
    git(paths, "commit", "-q", "-m", message, env=_git_identity())
    sha = git(paths, "rev-parse", "HEAD")
    if push:
        git(paths, "push", "origin", f"HEAD:refs/heads/{branch}")
    return {
        "status": "committed",
        "commit": sha,
        "message": message,
        "files": staged.splitlines(),
        "pushed": push,
    }


def verify_mr(paths: Paths, candidate: str, mr_iid: int, http: Http = urllib_http) -> dict:
    """Confirm the MR description on GitLab matches the local reports/mr_description.md.

    The merge request is looked up by the project path from the decision record's lineage,
    and leading and trailing whitespace is ignored. Returns "match" or "mismatch" with the
    auth variant used, or "unverified" when no auth variant can read the merge request.
    """
    decision = read_json(_decision_path(paths, candidate))
    expected = (paths.root / DESCRIPTION_FILE).read_text(encoding="utf-8")
    lin = decision["lineage"]
    parts = urllib.parse.urlsplit(lin["project_url"])
    project = urllib.parse.quote(parts.path.strip("/"), safe="")
    url = f"{parts.scheme}://{parts.netloc}/api/v4/projects/{project}/merge_requests/{mr_iid}"
    for auth, headers in _auth_variants():
        resp = http("GET", url, headers, None)
        if resp.status == 200 and isinstance(resp.body, dict):
            actual = str(resp.body.get("description") or "")
            same = actual.strip() == expected.strip()
            return {"status": "match" if same else "mismatch", "auth": auth}
    return {"status": "unverified", "reason": "the merge request could not be read"}


def render_promotion_note(promotion: dict[str, Any], deployment: dict[str, Any] | None) -> str:
    """Build the Markdown body of the "Promotion complete" merge request note.

    promotion is the promote job's reports/promotion.json and deployment the deploy job's
    reports/deployment.json, or None when there is none. The note names the release,
    registry version and deployment, then lists the champion, the promotion pipeline and,
    when cumulative_sci has a gco2e value, the carbon spent reaching this version.
    """
    release = promotion.get("release") or {}
    registry = promotion.get("registry") or {}
    method = registry.get("method")
    if method == "model_registry":
        reg = f"Model Registry `{registry.get('model')}` {registry.get('registered_version')}"
    elif method == "generic_package":
        reg = f"generic package `{registry.get('package')}` {registry.get('registered_version')}"
    else:
        reg = f"not registered ({method or 'unknown'})"
    if deployment:
        dep = f"[{deployment.get('environment')}]({deployment.get('job_url')})"
    else:
        dep = "not recorded (no deploy_production artifacts)"
    lines = [
        f"{NOTE_PREFIX}: Release [{release.get('tag')}]({release.get('url')}), "
        f"registry version {reg}, deployment {dep}.",
        "",
        f"- Champion {promotion.get('version')} from candidate "
        f"{promotion.get('candidate_version')}, commit `{str(promotion.get('commit'))[:12]}`",
        f"- Promotion pipeline: {promotion.get('pipeline_url')}",
    ]
    cumulative = promotion.get("cumulative_sci") or {}
    if cumulative.get("gco2e") is not None:
        lines.append(
            f"- Carbon to reach this version: {cumulative['gco2e']} gCO2e over "
            f"{cumulative.get('candidates')} evaluated candidate(s) (SCI, see docs/SCI.md)"
        )
    return "\n".join(lines) + "\n"


def promotion_note(t: Target, http: Http = urllib_http) -> dict[str, Any]:
    """Build the "Promotion complete" note for the MR merged by this default-branch commit.

    Uses the first merged merge request targeting the default branch that GitLab lists for
    the commit, and posts nothing. Returns "note" with the MR IID and body;
    "already_posted" when one of its first 100 notes starts with "Promotion complete";
    "no_merge_request"; "no_promotion" when the pipeline has no successful promote job
    (also when its job list cannot be read); or "error" when the commit's merge requests
    or the promote job's reports/promotion.json cannot be read.
    """
    attempts: list[dict[str, Any]] = []
    api = _Api(t, http, attempts)
    resp = api.connect("commit_merge_requests", f"/repository/commits/{t.sha}/merge_requests")
    if resp is None or not isinstance(resp.body, list):
        return {"status": "error", "error": "could not read this commit's merge requests"}
    merged = [
        mr
        for mr in resp.body
        if mr.get("state") == "merged" and mr.get("target_branch") == t.default_branch
    ]
    if not merged:
        return {"status": "no_merge_request", "reason": f"{t.sha[:12]} is not a merge commit"}
    iid = merged[0]["iid"]
    job_ids = api.successful_jobs() or {}
    if PROMOTE_JOB not in job_ids:
        return {
            "status": "no_promotion",
            "merge_request_iid": iid,
            "reason": "no successful promote job: models/champion.json did not change",
        }
    raw = api.artifact(job_ids[PROMOTE_JOB], "reports/promotion.json")
    if raw is None:
        return {"status": "error", "error": "promote job artifacts could not be downloaded"}
    deployment = None
    if DEPLOY_JOB in job_ids:
        dep_raw = api.artifact(job_ids[DEPLOY_JOB], "reports/deployment.json")
        deployment = json.loads(dep_raw) if dep_raw else None
    notes = api.get("list_notes", f"/merge_requests/{iid}/notes?per_page=100")
    posted = (
        notes.status == 200
        and isinstance(notes.body, list)
        and any(str(n.get("body", "")).startswith(NOTE_PREFIX) for n in notes.body)
    )
    if posted:
        return {"status": "already_posted", "merge_request_iid": iid}
    body = render_promotion_note(json.loads(raw), deployment)
    return {"status": "note", "merge_request_iid": iid, "body": body}


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point: run one flow subcommand and print its result as JSON.

    Subcommands are prepare, check, commit (which always pushes), verify-mr and
    promotion-note. FlowError, OSError, KeyError and ValueError become an "error" result.
    Returns 0 for the statuses ready, superseded, ok, committed, nothing_to_commit, match,
    note, no_merge_request, no_promotion and already_posted, and 1 for any other, such as
    rejected, problems, mismatch, unverified or error.
    """
    parser = argparse.ArgumentParser(prog="python -m modelgate.flow", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--project-url", required=True)
    p.add_argument("--project-id", required=True)
    p.add_argument("--pipeline-id", type=int, required=True)
    p.add_argument("--pipeline-iid", type=int, required=True)
    p.add_argument("--ref", required=True)
    p.add_argument("--sha", required=True)
    p.add_argument("--default-branch", default="main")
    for name in ("check", "commit"):
        sub.add_parser(name).add_argument("--candidate", required=True)
    v = sub.add_parser("verify-mr")
    v.add_argument("--candidate", required=True)
    v.add_argument("--mr-iid", type=int, required=True)
    n = sub.add_parser("promotion-note")
    n.add_argument("--project-url", required=True)
    n.add_argument("--project-id", required=True)
    n.add_argument("--pipeline-id", type=int, required=True)
    n.add_argument("--sha", required=True)
    n.add_argument("--default-branch", default="main")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)-7s %(name)s: %(message)s")
    paths = default_paths()
    try:
        if args.command == "prepare":
            target = Target(
                project_url=args.project_url.rstrip("/"),
                project_id=str(args.project_id),
                pipeline_id=args.pipeline_id,
                pipeline_iid=args.pipeline_iid,
                ref=args.ref,
                sha=args.sha,
                default_branch=args.default_branch,
            )
            result: dict[str, Any] = prepare(paths, target)
        elif args.command == "check":
            problems = check(paths, args.candidate)
            result = {"status": "ok" if not problems else "problems", "problems": problems}
        elif args.command == "commit":
            result = commit(paths, args.candidate)
        elif args.command == "verify-mr":
            result = verify_mr(paths, args.candidate, args.mr_iid)
        else:
            target = Target(
                project_url=args.project_url.rstrip("/"),
                project_id=str(args.project_id),
                pipeline_id=args.pipeline_id,
                pipeline_iid=0,
                ref=args.default_branch,
                sha=args.sha,
                default_branch=args.default_branch,
            )
            result = promotion_note(target)
    except (FlowError, OSError, KeyError, ValueError) as exc:
        result = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(result, indent=2, sort_keys=True))
    ok = {
        "ready",
        "no_candidate",
        "superseded",
        "ok",
        "committed",
        "nothing_to_commit",
        "match",
        "note",
        "no_merge_request",
        "no_promotion",
        "already_posted",
    }
    return 0 if result["status"] in ok else 1


if __name__ == "__main__":
    sys.exit(main())
