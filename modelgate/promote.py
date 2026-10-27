"""Promote the merged champion, then record its production deployment.

`promote` runs on the default branch when models/champion.json changed, which means a GO
merge request was merged. It:

1. checks that this pipeline's fresh training reproduces the approved candidate exactly
   (same config, same holdout, same metrics); if not, the merged code is not what was
   reviewed, and it fails without releasing anything;
2. registers the final version (Model Registry via CI_JOB_TOKEN, generic package fallback);
3. creates the Release model-v<version> with the model card as its description;
4. writes reports/promotion.json, including the carbon cost of every candidate evaluated
   on the merged branch against the previous champion.

`deploy` runs next, in a job with `environment: production`, and writes
reports/deployment.json. GitLab records the deployment itself from the job.
"""

from __future__ import annotations

import logging
import os
import urllib.parse
from typing import Any

from modelgate import registry
from modelgate.carbon import sig2
from modelgate.common import Http, Paths, read_json, read_json_if_exists, urllib_http, write_json

logger = logging.getLogger(__name__)

TAG_PREFIX = "model-v"


class PromotionError(RuntimeError):
    """Raised outside GitLab CI or when the champion cannot be verified, registered or released."""

    pass


def check_reproduced(champion: dict[str, Any], metrics: dict[str, Any]) -> None:
    """Raise PromotionError unless this pipeline's metrics reproduce the approved champion.

    metrics is the content of reports/metrics.json. Compares its training config, holdout
    SHA-256 and overall, segment and segment AUC gap metrics with the champion record; the
    error message names each of these three checks that failed and, for the metrics, quotes
    both overall AUCs rather than every differing value.
    """
    problems = []
    if metrics["config"] != champion["config"]:
        problems.append("config/train.yaml differs from the approved candidate's config")
    if metrics["holdout"]["sha256"] != champion.get("holdout_sha256"):
        problems.append("the holdout data differs")
    view = {k: metrics[k] for k in ("overall", "segments", "segment_auc_gap")}
    if view != champion["metrics"]:
        problems.append(
            f"metrics differ (approved AUC {champion['metrics']['overall']['auc']}, "
            f"this pipeline {metrics['overall']['auc']})"
        )
    if problems:
        raise PromotionError(
            "the default branch does not reproduce the approved candidate: " + "; ".join(problems)
        )


def cumulative_sci(paths: Paths, champion: dict[str, Any]) -> dict[str, Any]:
    """Sum the SCI of every candidate on the merged branch that faced the same previous champion.

    Reads every models/decisions/*.json record, finds the one for the champion's candidate and
    sums sci_gco2e_per_candidate (gCO2e, rounded to two significant figures; None when nothing
    was measured) over the records with the same branch and previous champion version. Returns
    that sum with the candidate and measured counts, the branch and the previous version. When
    the champion's own record is missing, returns gco2e None, zero candidates and a reason.
    """
    decisions = [read_json(p) for p in sorted((paths.models / "decisions").glob("*.json"))]
    own = next(
        (d for d in decisions if d["candidate_version"] == champion.get("candidate_version")),
        None,
    )
    if own is None:
        return {"gco2e": None, "candidates": 0, "reason": "decision record not found"}
    branch = own["lineage"]["branch"]
    previous = (own.get("champion") or {}).get("version")
    same = [
        d
        for d in decisions
        if d["lineage"]["branch"] == branch and (d.get("champion") or {}).get("version") == previous
    ]
    values = [
        d["sci"]["sci_gco2e_per_candidate"]
        for d in same
        if d.get("sci") and d["sci"].get("sci_gco2e_per_candidate") is not None
    ]
    return {
        "gco2e": sig2(sum(values)) if values else None,
        "candidates": len(same),
        "measured": len(values),
        "branch": branch,
        "previous_champion": previous,
    }


def _project_api(env: registry.CiEnv) -> str:
    return f"{env.api_url}/projects/{env.project_id}"


def find_merge_request(session: registry.Session) -> dict[str, Any] | None:
    """Return the first merged merge request GitLab relates to the pipeline's commit, or None.

    None is also returned when the API call does not answer 200 with a list.
    """
    env = session.env
    url = f"{_project_api(env)}/repository/commits/{env.commit}/merge_requests"
    resp = session.call("commit_merge_requests", "JOB-TOKEN header", "GET", url, _auth(env))
    if resp.status != 200 or not isinstance(resp.body, list):
        return None
    merged = [mr for mr in resp.body if mr.get("state") == "merged"]
    return merged[0] if merged else None


def _auth(env: registry.CiEnv) -> dict[str, str]:
    return {"JOB-TOKEN": env.token}


def release_description(
    champion: dict[str, Any],
    card_text: str,
    reg: dict[str, Any],
    mr: dict[str, Any] | None,
    cumulative: dict[str, Any],
) -> str:
    """Build the Markdown description of the model's Release.

    A heading, a sentence naming the source candidate and the merge request ("a merge to the
    default branch" when mr is None), a table of holdout AUC, registry location, this
    candidate's SCI and the cumulative carbon (gCO2e to two significant figures, "n/a" when
    missing), then a horizontal rule and the model card text.
    """
    sci = (champion.get("sci") or {}).get("sci_gco2e_per_candidate")
    if reg.get("method") == "model_registry":
        where = f"GitLab Model Registry, model `{reg.get('model')}` {reg.get('registered_version')}"
    else:
        where = f"generic package `{reg.get('package')}` {reg.get('registered_version')}"
    merged_by = f"merge request !{mr['iid']}" if mr else "a merge to the default branch"
    lines = [
        f"## ModelGate model {champion['version']}",
        "",
        f"Promoted from candidate {champion.get('candidate_version')} by {merged_by}. The "
        "promotion pipeline retrained it on the default branch and got identical holdout "
        "metrics before releasing.",
        "",
        "| | |",
        "| --- | --- |",
        f"| Holdout AUC | {champion['metrics']['overall']['auc']:.4f} |",
        f"| Registry | {where} |",
        f"| SCI of this candidate | {_g2(sci)} gCO2e |",
        f"| Carbon to reach this version | {_g2(cumulative.get('gco2e'))} gCO2e over "
        f"{cumulative.get('candidates')} candidate(s) on `{cumulative.get('branch')}` |",
        "",
        "---",
        "",
        card_text,
    ]
    return "\n".join(lines)


def _g2(value: Any) -> str:
    return "n/a" if value is None else f"{float(value):.2g}"


def create_release(
    session: registry.Session, version: str, description: str, links: list[dict[str, str]]
) -> dict[str, Any]:
    """Create the Release model-v<version> at the pipeline's commit with the given asset links.

    Returns the tag, the Release web URL (built from CI_PROJECT_URL) and a status of "created"
    (HTTP 201) or "exists" (HTTP 409, as on a re-run of the job; the existing Release is left
    unchanged). Any other status raises PromotionError.
    """
    env = session.env
    tag = f"{TAG_PREFIX}{version}"
    payload = {
        "tag_name": tag,
        "ref": env.commit,
        "name": f"ModelGate model {version}",
        "description": description,
        "assets": {"links": links},
    }
    url = f"{_project_api(env)}/releases"
    resp = session.call("create_release", "JOB-TOKEN header", "POST", url, _auth(env), payload)
    project_url = os.environ.get("CI_PROJECT_URL", "").rstrip("/")
    web = f"{project_url}/-/releases/{urllib.parse.quote(tag)}"
    if resp.status == 201:
        return {"tag": tag, "url": web, "status": "created"}
    if resp.status == 409:  # a re-run of the job: the Release already exists
        return {"tag": tag, "url": web, "status": "exists"}
    raise PromotionError(f"creating Release {tag} returned {resp.status}: {resp.text}")


def main(paths: Paths, http: Http = urllib_http) -> dict[str, Any]:
    """Verify, register and release the merged GO champion, then record the promotion.

    Registers in the Model Registry and falls back to the generic package registry if that
    raises RegistryError. Writes reports/promotion.json and returns the same record. Raises
    PromotionError when CI_JOB_TOKEN is not set, when models/champion.json is missing or not GO,
    when reports/metrics.json does not reproduce it, when both registrations fail, or when
    creating the Release returns a status other than 201 or 409 (an existing Release).
    """
    env = registry.CiEnv.from_environ()
    if env is None:
        raise PromotionError("promote runs only in GitLab CI (CI_JOB_TOKEN is not set)")
    champion = read_json_if_exists(paths.champion_file)
    if champion is None or champion.get("verdict") != "GO":
        raise PromotionError("models/champion.json is missing or not a GO candidate")
    check_reproduced(champion, read_json(paths.reports / "metrics.json"))
    version = champion["version"]
    card_path = paths.root / champion.get("card", "")
    card_text = card_path.read_text(encoding="utf-8") if card_path.is_file() else ""
    files = [
        p
        for p in (
            paths.challenger / "model.pkl",
            paths.challenger / "meta.json",
            paths.reports / "metrics.json",
            paths.champion_file,
            card_path,
        )
        if p.is_file()
    ]
    session = registry.Session(http, env)
    model_view = {
        "verdict": "GO",
        "challenger": {"metrics": champion["metrics"], "config": champion["config"]},
    }
    try:
        reg = registry.register_model_registry(session, version, files, model_view)
    except registry.RegistryError as exc:
        logger.warning("model registry failed: %s; using the generic package registry", exc)
        try:
            reg = {
                "model_registry_error": str(exc),
                **registry.register_generic(session, version, files),
            }
        except registry.RegistryError as exc2:
            raise PromotionError(f"registering {version} failed: {exc2}") from exc2
    mr = find_merge_request(session)
    cumulative = cumulative_sci(paths, champion)
    project_url = os.environ.get("CI_PROJECT_URL", "").rstrip("/")
    links = [{"name": "Pipeline", "url": os.environ.get("CI_PIPELINE_URL", project_url)}]
    if champion.get("card"):
        links.append(
            {"name": "Model card", "url": f"{project_url}/-/blob/{env.commit}/{champion['card']}"}
        )
    if reg["method"] == "generic_package":
        links.append(
            {
                "name": "Model file (model.pkl)",
                "url": f"{_project_api(env)}/packages/generic/{registry.GENERIC_PACKAGE}/"
                f"{urllib.parse.quote(version, safe='')}/model.pkl",
                "link_type": "package",
            }
        )
    else:
        links.append(
            {"name": "Model Registry", "url": f"{project_url}/-/ml/models", "link_type": "package"}
        )
    release = create_release(
        session, version, release_description(champion, card_text, reg, mr, cumulative), links
    )
    promotion = {
        "version": version,
        "candidate_version": champion.get("candidate_version"),
        "commit": env.commit,
        "pipeline_url": os.environ.get("CI_PIPELINE_URL"),
        "merge_request": {"iid": mr["iid"], "url": mr.get("web_url")} if mr else None,
        "reproduced": True,
        "registry": {k: v for k, v in reg.items() if k != "attempts"},
        "release": release,
        "cumulative_sci": cumulative,
        "attempts": session.attempts,
    }
    write_json(paths.reports / "promotion.json", promotion)
    logger.info(
        "promoted %s: release %s (%s), registry %s",
        version,
        release["tag"],
        release["status"],
        reg["method"],
    )
    return promotion


def deploy(paths: Paths) -> dict[str, Any]:
    """Write reports/deployment.json from reports/promotion.json and the CI environment.

    Returns the deployment record. The environment name and tier default to "production", and
    the commit falls back to the one in promotion.json when CI_COMMIT_SHA is not set.
    """
    promotion = read_json(paths.reports / "promotion.json")
    deployment = {
        "environment": os.environ.get("CI_ENVIRONMENT_NAME", "production"),
        "tier": os.environ.get("CI_ENVIRONMENT_TIER", "production"),
        "environment_url": os.environ.get("CI_ENVIRONMENT_URL"),
        "job_url": os.environ.get("CI_JOB_URL"),
        "version": promotion["version"],
        "release": promotion["release"],
        "registry": promotion["registry"],
        "commit": os.environ.get("CI_COMMIT_SHA", promotion["commit"]),
    }
    write_json(paths.reports / "deployment.json", deployment)
    logger.info("deployed %s to %s", deployment["version"], deployment["environment"])
    return deployment
