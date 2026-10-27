"""Decision record, model card and merge request text for one candidate.

Everything here is rendered from the pipeline's reports, so every number in a card
or merge request description comes from code, not from the agent. The agent writes
only two marked blocks: the card's Analysis and the description's Summary.
`check_card` and `check_description` re-render both files and reject any other
difference, and flag text in either block that contradicts a rule result.
"""

from __future__ import annotations

import json
import re
from typing import Any

from modelgate import owners
from modelgate.common import Paths, load_yaml, sha256_file

SCHEMA_VERSION = 1
ANALYSIS_START = "<!-- modelgate:analysis:start -->"
ANALYSIS_END = "<!-- modelgate:analysis:end -->"
SUMMARY_START = "<!-- modelgate:summary:start -->"
SUMMARY_END = "<!-- modelgate:summary:end -->"
ANALYSIS_PLACEHOLDER = "_Written by the ModelGate flow: see AGENTS.md._"
SUMMARY_PLACEHOLDER = "_Three-line summary written by the ModelGate flow._"
MAX_ANALYSIS_WORDS = 350
MAX_SUMMARY_LINES = 3

TITLES = {
    "GO": "GO: promote model {version}",
    "NEEDS_REVIEW": "NEEDS REVIEW: model {version}",
    "NO_GO": "NO-GO: model {version}",
}
LABELS = {"GO": "modelgate::go", "NEEDS_REVIEW": "modelgate::review", "NO_GO": "modelgate::no-go"}
VERDICT_TEXT = {"GO": "GO", "NEEDS_REVIEW": "NEEDS REVIEW", "NO_GO": "NO-GO"}
# Verdicts that need a person, and why. NO_GO needs no decision: it does not ship.
REVIEW_REASONS = {
    "GO": "merging promotes this candidate to the champion",
    "NEEDS_REVIEW": "a review-level rule failed, so a person decides",
}
RULE_NAMES = {
    "no_regression": "no regression",
    "min_absolute_auc": "minimum AUC",
    "slice_gap": "segment gap",
    "calibration": "calibration",
    "scan_clean": "scan",
    "carbon_efficiency": "carbon efficiency",
}


# ---------------------------------------------------------------------- decision


def build_decision(
    paths: Paths,
    reports: dict[str, dict[str, Any] | None],
    lineage: dict[str, Any],
) -> dict[str, Any]:
    """Build the decision record that the flow writes to models/decisions/<candidate>.json.

    Combines the gate report with the optional sci, scan, registry and drift reports in
    `reports`, the run's `lineage`, and values read from config/data.yaml and
    config/thresholds.yaml (whose sha256 is recorded). The candidate version is
    `<proposed version>-rc.<pipeline_iid>`. Returns the record and writes nothing.
    """
    gate = reports["gate_report"] or {}
    sci = reports.get("sci")
    scan = reports.get("scan") or {}
    registry = reports.get("registry")
    bump = gate["proposed_version_bump"]
    candidate = f"{bump['to']}-rc.{lineage['pipeline_iid']}"
    rules = {r["id"]: r for r in gate["rules"]}
    data_cfg = load_yaml(paths.config / "data.yaml")
    pickle = scan.get("picklescan") or {}
    audit = scan.get("pip_audit") or {}
    thresholds_file = paths.config / "thresholds.yaml"
    thresholds = load_yaml(thresholds_file)
    gain = (gate.get("comparison") or {}).get("auc_delta")
    grams = sci.get("sci_gco2e_per_candidate") if sci else None
    per_gram = round(gain / grams, 4) if gain and gain > 0 and grams else None
    drift = reports.get("drift")
    sci_cfg = paths.config / "sci.yaml"
    credits = (load_yaml(sci_cfg) or {}).get("flow_credits_per_run") if sci_cfg.exists() else None
    return {
        "schema_version": SCHEMA_VERSION,
        "candidate_version": candidate,
        "proposed_version": bump["to"],
        "bump": bump,
        "verdict": gate["verdict"],
        "failed_rules": gate["failed_rules"],
        "rules": [{**r, "margin": rule_margin(r)} for r in gate["rules"]],
        "challenger": gate["challenger"],
        "champion": gate["champion"],
        "comparison": gate["comparison"],
        "notes": gate.get("notes", []),
        "data": {
            "seed": data_cfg["seed"],
            "n_train": data_cfg["n_train"],
            "n_holdout": data_cfg["n_holdout"],
            "segment_mix": data_cfg["segment_mix"],
        },
        "sci": None
        if sci is None
        else {
            **{
                k: sci.get(k)
                for k in (
                    "sci_gco2e_per_candidate",
                    "operational_gco2e",
                    "embodied_gco2e",
                    "energy_kwh",
                    "job_seconds",
                    "energy_scaled_to_job",
                    "grid_intensity_gco2e_per_kwh",
                )
            },
            "gco2e_per_0_01_auc": rules.get("carbon_efficiency", {}).get("value"),
            "auc_gain_per_gco2e": per_gram,
            "flow_credits_per_run": credits,
        },
        "thresholds": {
            "file": "config/thresholds.yaml",
            "sha256": sha256_file(thresholds_file),
            "rules": {
                r["id"]: {k: v for k, v in r.items() if k not in ("id", "description")}
                for r in thresholds["rules"]
            },
        },
        "champion_drift": None
        if not drift or drift.get("status") != "checked"
        else {
            "date": drift["date"],
            "champion_version": drift["champion_version"],
            "holdout_auc": drift["holdout_auc"],
            "drift_auc": drift["drift"]["auc"],
            "max_psi": drift["max_psi"],
            "alert": drift["alert"],
            "reasons": drift.get("reasons", []),
        },
        "scan": {
            "picklescan": {
                k: pickle.get(k)
                for k in ("ran", "scan_error", "dangerous_globals", "unexpected_globals")
            },
            "pip_audit": {
                "ran": audit.get("ran"),
                "packages_audited": audit.get("packages_audited"),
                "vulnerabilities": [
                    {k: v.get(k) for k in ("package", "version", "id", "severity")}
                    for v in audit.get("vulnerabilities") or []
                ],
            },
        },
        "registry": None
        if registry is None
        else {
            k: registry.get(k)
            for k in ("method", "registered_version", "model", "package", "model_registry_error")
            if registry.get(k) is not None
        },
        "review": {
            "reviewers": owners.reviewers(paths) if gate["verdict"] in REVIEW_REASONS else [],
            "reason": REVIEW_REASONS.get(gate["verdict"]),
        },
        "lineage": {**lineage, "champion_source": gate.get("inputs", {}).get("champion_source")},
    }


# Which side of the threshold passes, per rule.
AT_LEAST = frozenset({"no_regression", "min_absolute_auc"})
AT_MOST = frozenset({"slice_gap", "calibration", "carbon_efficiency"})


def rule_margin(rule: dict[str, Any]) -> float | None:
    """Distance from the rule's threshold, in the rule's own units; None when not measured.

    Positive means room to spare, negative means the shortfall that makes the rule fail: for
    example a slice_gap of 0.1658 against 0.05 has margin -0.1158. scan_clean counts blocking
    findings, so its margin is minus that count. Carbon margins have two significant figures,
    the others four decimals.
    """
    value, threshold = rule.get("value"), rule.get("threshold")
    if value is None or threshold is None:
        return None
    if rule["id"] in AT_LEAST:
        return round(value - threshold, 4)
    if rule["id"] == "carbon_efficiency":
        return float(f"{threshold - value:.2g}")
    if rule["id"] in AT_MOST:
        return round(threshold - value, 4)
    return -float(value)  # scan_clean: findings must be 0


def mr_title(decision: dict[str, Any]) -> str:
    """Merge request title for the decision's verdict and proposed version."""
    return TITLES[decision["verdict"]].format(version=decision["proposed_version"])


def mr_label(decision: dict[str, Any]) -> str:
    """Scoped GitLab label, such as `modelgate::go`, for the decision's verdict."""
    return LABELS[decision["verdict"]]


# ------------------------------------------------------------------------ format


def _f4(value: Any) -> str:
    return "n/a" if value is None else f"{float(value):.4f}"


def _d4(value: Any) -> str:
    return "n/a" if value is None else f"{float(value):+.4f}"


def _g2(value: Any) -> str:
    return "n/a" if value is None else f"{float(value):.2g}"


def _value(rule: dict[str, Any]) -> str:
    value = rule["value"]
    if value is None:
        return "-"
    if rule["id"] in ("carbon_efficiency", "scan_clean"):
        return f"{value:.2g}"
    return f"{value:.4f}"


def _threshold(rule: dict[str, Any]) -> str:
    t = rule["threshold"]
    if t is None:
        return "-"
    op = {
        "no_regression": ">=",
        "min_absolute_auc": ">=",
        "slice_gap": "<=",
        "calibration": "<=",
        "scan_clean": "=",
        "carbon_efficiency": "<=",
    }[rule["id"]]
    if rule["id"] in ("carbon_efficiency", "scan_clean"):
        return f"{op} {t:.2g}"
    return f"{op} {t:.4f}"


def _rules_table(decision: dict[str, Any], with_notes: bool) -> list[str]:
    head = "| Rule | Status | Value | Threshold |" + (" Detail |" if with_notes else "")
    sep = "| --- | --- | --- | --- |" + (" --- |" if with_notes else "")
    rows = [head, sep]
    for r in decision["rules"]:
        status = r["status"].upper() if r["status"] == "fail" else r["status"]
        row = f"| `{r['id']}` | {status} | {_value(r)} | {_threshold(r)} |"
        if with_notes:
            row += f" {r['note'].replace('|', '/')} |"
        rows.append(row)
    return rows


def _registry_line(decision: dict[str, Any]) -> str:
    reg = decision.get("registry")
    candidate = decision["candidate_version"]
    if not reg:
        return f"not recorded (register_challenger artifacts unavailable); candidate {candidate}"
    method = reg.get("method")
    if method == "model_registry":
        model, version = reg.get("model"), reg.get("registered_version")
        return f"GitLab Model Registry, model `{model}` version {version}"
    if method == "generic_package":
        return (
            f"generic package `{reg.get('package')}` version {reg.get('registered_version')} "
            "(the Model Registry API did not accept CI_JOB_TOKEN)"
        )
    return f"{method}; candidate {candidate}"


def _drift_lines(drift: dict[str, Any] | None) -> list[str]:
    if not drift:
        return ["No nightly drift check of the champion has run yet."]
    status = "alert: " + "; ".join(drift["reasons"]) if drift["alert"] else "within limits"
    return [
        f"Latest nightly check of champion {drift['champion_version']} ({drift['date']}): AUC "
        f"{_f4(drift['drift_auc'])} on the drift batch against {_f4(drift['holdout_auc'])} on "
        f"the holdout, largest PSI {_f4(drift['max_psi'])}; {status}. A drift alert is a "
        "possible reason for this candidate, not a gate rule."
    ]


def _scan_lines(scan: dict[str, Any]) -> list[str]:
    pickle = scan["picklescan"]
    audit = scan["pip_audit"]
    if not pickle.get("ran"):
        model = "- picklescan: did not run"
    else:
        parsed = "could not parse" if pickle.get("scan_error") else "parsed"
        model = (
            f"- picklescan: {parsed} the whole model file; "
            f"{pickle.get('dangerous_globals')} dangerous global(s), "
            f"{len(pickle.get('unexpected_globals') or [])} outside the allowlist"
        )
    vulns = audit.get("vulnerabilities") or []
    audited = f"- pip-audit: {audit.get('packages_audited')} locked packages"
    if not audit.get("ran"):
        deps = "- pip-audit: did not run"
    elif vulns:
        found = "; ".join(
            f"{v['package']} {v['version']} {v['id']} ({v['severity']})" for v in vulns
        )
        deps = f"{audited}; findings: {found}"
    else:
        deps = f"{audited}, no known vulnerabilities"
    return [model, deps]


# -------------------------------------------------------------------------- card


def render_card(decision: dict[str, Any], analysis: str = ANALYSIS_PLACEHOLDER) -> str:
    """Render the Markdown model card for a decision record.

    `analysis` is stripped of surrounding whitespace and placed between the analysis
    markers; the default is the placeholder the flow's agent replaces. Returns the card
    text and writes nothing.
    """
    d = decision
    ch = d["challenger"]
    cfg = ch["config"]
    m = ch["metrics"]
    champ = d.get("champion")
    cm = champ["metrics"] if champ else None
    lin = d["lineage"]
    verdict = VERDICT_TEXT[d["verdict"]]
    failed = ", ".join(f"`{r}`" for r in d["failed_rules"]) or "none"
    bump = d["bump"]

    lines = [
        f"# Model card: ModelGate candidate {d['candidate_version']}",
        "",
        "| | |",
        "| --- | --- |",
        f"| Verdict | **{verdict}** |",
        f"| Failed rules | {failed} |",
        f"| Proposed version | {d['proposed_version']} ({bump['kind']} bump) |",
        f"| Champion | {champ['version'] if champ else 'none (first candidate)'} |",
        f"| Commit | `{lin['commit']}` on `{lin['branch']}` |",
        f"| Pipeline | {lin['pipeline_url']} |",
        f"| Registry | {_registry_line(d)} |",
        "",
        "## Overview",
        "",
        "Binary classifier that estimates the probability of `default_12m` (a credit default "
        "within 12 months) for one applicant.",
        "",
        f"- Model family: `{cfg['model_family']}`; output schema `{cfg['output_schema']}`",
        f"- Features ({len(cfg['features'])}): " + ", ".join(f"`{f}`" for f in cfg["features"]),
        "- Hyperparameters: "
        + (", ".join(f"`{k}={v}`" for k, v in sorted(cfg["hyperparams"].items())) or "defaults")
        + f"; seed {cfg['seed']}",
        "- Version bump reasons: " + "; ".join(bump["reasons"]),
        "",
        "## Intended use",
        "",
        "Demonstration of ModelGate's promotion gate on synthetic data. Not for decisions "
        "about real people. The protected attribute `segment` is used only to measure "
        "per-segment performance and is never a model input.",
        "",
        "## Data",
        "",
        f"Synthetic rows from `modelgate/data.py`, seed {d['data']['seed']}: "
        f"{d['data']['n_train']} training rows and {d['data']['n_holdout']} holdout rows. "
        "Segment shares: "
        + ", ".join(f"{k} {v:.0%}" for k, v in d["data"]["segment_mix"].items())
        + f". Holdout sha256 `{ch['holdout_sha256'][:12]}`.",
        "",
        "## Metrics (holdout)",
        "",
        "| Metric | Challenger | Champion | Delta |",
        "| --- | --- | --- | --- |",
    ]
    for key, label in (
        ("auc", "AUC"),
        ("f1", "F1 at 0.5"),
        ("accuracy", "Accuracy at 0.5"),
        ("brier", "Brier score"),
        ("ece", "Expected calibration error"),
    ):
        value = m["overall"].get(key)
        champ_value = cm["overall"].get(key) if cm else None
        delta = None if champ_value is None else value - champ_value
        lines.append(f"| {label} | {_f4(value)} | {_f4(champ_value)} | {_d4(delta)} |")
    lines += [
        "",
        "Per segment:",
        "",
        "| Segment | Rows | Positive rate | AUC | Champion AUC | Delta |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for name, seg in m["segments"].items():
        champ_auc = cm["segments"].get(name, {}).get("auc") if cm else None
        delta = None if champ_auc is None or seg["auc"] is None else seg["auc"] - champ_auc
        lines.append(
            f"| {name} | {seg['n']} | {_f4(seg['positive_rate'])} | {_f4(seg['auc'])} "
            f"| {_f4(champ_auc)} | {_d4(delta)} |"
        )
    lines += [
        "",
        f"Largest segment AUC gap: {_f4(m['segment_auc_gap'])}.",
        "",
        "## Calibration",
        "",
        f"Brier score {_f4(m['overall']['brier'])} and expected calibration error "
        f"{_f4(m['overall']['ece'])} (10 equal-width bins)"
        + (f"; champion Brier {_f4(cm['overall']['brier'])}." if cm else "."),
        "",
        "## Carbon (SCI)",
        "",
    ]
    sci = d.get("sci")
    if sci:
        per_auc = sci.get("gco2e_per_0_01_auc")
        per_gram = sci.get("auc_gain_per_gco2e")
        lines += [
            "| Quantity | Value |",
            "| --- | --- |",
            f"| SCI per evaluated candidate | {_g2(sci['sci_gco2e_per_candidate'])} gCO2e |",
            f"| Operational (E x I) | {_g2(sci['operational_gco2e'])} gCO2e |",
            f"| Embodied (M) | {_g2(sci['embodied_gco2e'])} gCO2e |",
            f"| Energy | {_g2(sci['energy_kwh'] * 1000)} Wh over {sci['job_seconds']} s |",
            f"| Grid intensity | {float(sci['grid_intensity_gco2e_per_kwh']):g} gCO2e/kWh "
            "(us-east1) |",
            "| gCO2e per 0.01 AUC gained | "
            + (f"{_g2(per_auc)} |" if per_auc is not None else "not applicable |"),
            "| AUC gained per gCO2e | "
            + (f"{_g2(per_gram)} |" if per_gram is not None else "no AUC gain |"),
            "",
            "Method and sources: `docs/SCI.md`. "
            + (
                "The flow run that wrote this card is not measured in energy; one run costs "
                f"about {sci['flow_credits_per_run']:g} GitLab Credits (Credits dashboard)."
                if sci.get("flow_credits_per_run") is not None
                else "The flow run that wrote this card is not measured."
            ),
        ]
        if str(lin.get("reports_source", "")).startswith("regenerated"):
            lines += [
                "",
                "These figures were measured on the flow's runner, because the CI artifacts "
                "could not be read. The model metrics and their rule results are identical to "
                "CI's; the energy figure, and so the carbon rule, may differ.",
            ]
    else:
        lines.append("Not measured: `reports/sci.json` was not available.")
    lines += ["", "## Champion drift", "", *_drift_lines(d.get("champion_drift"))]
    lines += ["", "## Scan", "", *_scan_lines(d["scan"]), ""]
    sha = (d.get("thresholds") or {}).get("sha256", "")
    lines += [
        "## Gate rules",
        "",
        f"From `config/thresholds.yaml` (sha256 `{sha[:12]}`), applied by `modelgate/evaluate.py`:",
        "",
        *_rules_table(d, with_notes=True),
        "",
        "## Verdict",
        "",
        f"**{verdict}**. Failed rules: {failed}. GO requires every rule to pass; NEEDS REVIEW "
        "means only `slice_gap` or `carbon_efficiency` failed; NO-GO means any other rule "
        "failed. The verdict is computed by code and is not changed by the analysis below.",
    ]
    for note in d.get("notes") or []:
        lines.append(f"\nNote: {note}.")
    lines += [
        "",
        "## Analysis",
        "",
        ANALYSIS_START,
        analysis.strip(),
        ANALYSIS_END,
        "",
        "## Lineage",
        "",
        f"- Project: {lin['project_url']}",
        f"- Branch `{lin['branch']}`, commit `{lin['commit']}`",
        f"- Pipeline {lin['pipeline_id']} (IID {lin['pipeline_iid']}): {lin['pipeline_url']}",
        f"- Reports: {lin['reports_source']}",
        f"- Champion read from: {lin.get('champion_source') or 'unknown'}",
        f"- Candidate version {d['candidate_version']}; registry: {_registry_line(d)}",
        "",
    ]
    return "\n".join(lines)


def _review_lines(decision: dict[str, Any]) -> list[str]:
    review = decision.get("review") or {}
    names = review.get("reviewers") or []
    if names:
        people = ", ".join(f"@{n}" for n in names)
        return [f"Review requested from {people}: {review['reason']}.", ""]
    if decision["verdict"] == "NEEDS_REVIEW":
        return ["No reviewers are configured in `config/review.yaml`; assign one by hand.", ""]
    return []


def render_description(
    decision: dict[str, Any], card_url: str, summary: str = SUMMARY_PLACEHOLDER
) -> str:
    """Render the Markdown merge request description for a decision record.

    `card_url` is written as given on the Model card line, and `summary` is stripped of
    surrounding whitespace and placed between the summary markers. Returns the text and
    writes nothing.
    """
    d = decision
    verdict = VERDICT_TEXT[d["verdict"]]
    sci = d.get("sci")
    lin = d["lineage"]
    lines = [
        f"**Verdict: {verdict}** for candidate `{d['candidate_version']}` "
        f"(proposed version {d['proposed_version']}, {d['bump']['kind']} bump).",
        "",
        SUMMARY_START,
        summary.strip(),
        SUMMARY_END,
        "",
        *_rules_table(d, with_notes=False),
        "",
        *_review_lines(d),
        f"- Model card: {card_url}",
        f"- Pipeline: {lin['pipeline_url']}",
        f"- Registry: {_registry_line(d)}",
        "- SCI: "
        + (
            f"{_g2(sci['sci_gco2e_per_candidate'])} gCO2e per evaluated candidate "
            f"({_g2(sci['operational_gco2e'])} operational, {_g2(sci['embodied_gco2e'])} "
            "embodied)"
            if sci
            else "not measured"
        ),
        "",
        "Written by the ModelGate flow from this pipeline's reports. The verdict comes from "
        "`config/thresholds.yaml`."
        + (
            " Merging this merge request promotes `models/champion.json` to this candidate."
            if d["verdict"] == "GO"
            else " This candidate is not the champion; merging does not promote it."
        ),
        "",
    ]
    return "\n".join(lines)


# ------------------------------------------------------------------------- check


def _between(text: str, start: str, end: str) -> str | None:
    i, j = text.find(start), text.find(end)
    if i < 0 or j < i:
        return None
    return text[i + len(start) : j].strip()


_PASS_WORDS = re.compile(r"\b(pass|passed|passes|passing|satisf\w*|met)\b", re.IGNORECASE)
_FAIL_WORDS = re.compile(r"\b(fail|failed|fails|failing|violat\w*|breach\w*)\b", re.IGNORECASE)
_NEGATION = re.compile(r"\b(not|no|never|didn't|did not|does not|doesn't|fails? to)\b", re.I)


def contradictions(text: str, decision: dict[str, Any]) -> list[str]:
    """Problem messages for sentences that call a failed rule passed, or a passed rule failed.

    Text is split into sentences at `.`, `!` or `?` followed by whitespace, and at newlines.
    A sentence refers to a rule when it contains the rule id, or its `RULE_NAMES` name in
    any case. A sentence with a negation word, or with both pass and fail words, is not
    flagged. Each message names the rule and quotes the sentence.
    """
    problems = []
    status = {r["id"]: r["passed"] for r in decision["rules"]}
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
        for rule_id, passed in status.items():
            if rule_id not in sentence and RULE_NAMES[rule_id] not in sentence.lower():
                continue
            negated = bool(_NEGATION.search(sentence))
            says_pass = bool(_PASS_WORDS.search(sentence)) and not negated
            says_fail = bool(_FAIL_WORDS.search(sentence)) and not negated
            if not passed and says_pass and not says_fail:
                problems.append(f"says {rule_id} passed, but it failed: {sentence.strip()!r}")
            if passed and says_fail and not says_pass:
                problems.append(f"says {rule_id} failed, but it passed: {sentence.strip()!r}")
    return problems


_DECIMAL = re.compile(r"\d+\.\d+")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def unknown_numbers(text: str, reference: str) -> list[str]:
    """Problem messages for decimal numbers in text whose value appears nowhere in reference.

    AGENTS.md lets the agent repeat numbers, never compute or invent them; reference is the
    rendered card and the decision record. Numbers are compared by value, so 0.80 matches
    0.8, and signs do not matter. Whole numbers such as counts, dates and word limits are
    not checked. Returns one message per distinct number, smallest first.
    """
    known = {float(n) for n in _NUMBER.findall(reference)}
    unknown = sorted({n for n in _DECIMAL.findall(text) if float(n) not in known}, key=float)
    return [
        f"{n} is not a number in the card or the decision record; repeat the card's numbers "
        "exactly instead of computing new ones"
        for n in unknown
    ]


def _reference(decision: dict[str, Any]) -> str:
    return render_card(decision) + json.dumps(decision)


_CODE_SPAN = re.compile(r"`[^`]*`")
_NAME_VARIANTS = re.compile(
    r"\b(?:model[\s_-]+gate|modelgate|model[\s_-]+promotion[\s_-]+gate)\b", re.I
)


def naming_problems(text: str) -> list[str]:
    """Problem messages for spellings of the project name other than `ModelGate`.

    Matches "modelgate", "model gate" and "model promotion gate" in any case, with spaces,
    hyphens or underscores between the words, and ignores text inside backtick code spans.
    Returns one message per distinct spelling found, sorted.
    """
    prose = _CODE_SPAN.sub("", text)
    wrong = sorted({m.group(0) for m in _NAME_VARIANTS.finditer(prose)} - {"ModelGate"})
    return [f"the project is called ModelGate, not {name!r}" for name in wrong]


def check_card(text: str, decision: dict[str, Any]) -> list[str]:
    """Problems with a model card's text, or an empty list when there are none.

    If the analysis markers are missing or out of order, that is the only problem
    returned. Otherwise it reports an unwritten Analysis or one over the word limit, any
    difference from `render_card` with the same analysis, analysis sentences that
    contradict a rule result, numbers that are not in the card or the decision record, and
    other spellings of the project name.
    """
    analysis = _between(text, ANALYSIS_START, ANALYSIS_END)
    if analysis is None:
        return ["the analysis markers are missing"]
    problems = []
    if not analysis or analysis == ANALYSIS_PLACEHOLDER:
        problems.append("the Analysis section has not been written")
    if len(analysis.split()) > MAX_ANALYSIS_WORDS:
        problems.append(f"the Analysis section exceeds {MAX_ANALYSIS_WORDS} words")
    if text != render_card(decision, analysis):
        problems.append("text outside the Analysis section differs from the rendered card")
    problems += contradictions(analysis, decision)
    problems += unknown_numbers(analysis, _reference(decision))
    problems += naming_problems(analysis)
    return problems


def check_description(text: str, decision: dict[str, Any], card_url: str) -> list[str]:
    """Problems with a merge request description, or an empty list when there are none.

    If the summary markers are missing or out of order, that is the only problem
    returned. Otherwise it reports an unwritten summary or one with more than
    `MAX_SUMMARY_LINES` non-blank lines, any difference from `render_description` with
    the same summary and `card_url`, summary sentences that contradict a rule result,
    numbers that are not in the card or the decision record, and other spellings of the
    project name.
    """
    summary = _between(text, SUMMARY_START, SUMMARY_END)
    if summary is None:
        return ["the summary markers are missing"]
    problems = []
    lines = [line for line in summary.splitlines() if line.strip()]
    if not lines or summary == SUMMARY_PLACEHOLDER:
        problems.append("the summary has not been written")
    if len(lines) > MAX_SUMMARY_LINES:
        problems.append(f"the summary has more than {MAX_SUMMARY_LINES} lines")
    if text != render_description(decision, card_url, summary):
        problems.append("text outside the summary differs from the rendered description")
    problems += contradictions(summary, decision)
    problems += unknown_numbers(summary, _reference(decision))
    problems += naming_problems(summary)
    return problems
