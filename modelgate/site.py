"""Build the GitLab Pages dashboard into public/.

Static HTML with inline CSS and SVG: no scripts, fonts or other external requests. Inputs:
models/champion.json, models/decisions/*.json, models/cards/, reports/promotion.json when it
exists and, when available, the nightly drift history (reports/drift_history.json from this
pipeline, or the latest drift_check job's artifacts on the default branch). public/data.json
holds the same data for machines, and the cards and decision records are copied alongside.
"""

from __future__ import annotations

import html
import json
import logging
import os
import re
import shutil
import urllib.parse
from typing import Any

from modelgate.common import (
    Http,
    Paths,
    load_yaml,
    read_json,
    read_json_if_exists,
    urllib_http,
    write_json,
)
from modelgate.promote import cumulative_sci

logger = logging.getLogger(__name__)

VERDICT_CLASS = {"GO": "go", "NEEDS_REVIEW": "review", "NO_GO": "nogo"}
VERDICT_TEXT = {"GO": "GO", "NEEDS_REVIEW": "NEEDS REVIEW", "NO_GO": "NO-GO"}

CSS = """
:root { --bg:#ffffff; --fg:#1f2328; --muted:#59636e; --line:#d1d9e0; --panel:#f6f8fa;
  --go:#1a7f37; --review:#9a6700; --nogo:#cf222e; --accent:#0969da; }
@media (prefers-color-scheme: dark) { :root { --bg:#0d1117; --fg:#e6edf3; --muted:#9198a1;
  --line:#3d444d; --panel:#151b23; --go:#3fb950; --review:#d29922; --nogo:#f85149;
  --accent:#4493f8; } }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--fg);
  font:15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 1040px; margin: 0 auto; padding: 24px 16px 48px; }
h1 { margin: 0 0 4px; font-size: 28px; } h2 { margin: 32px 0 8px; font-size: 20px; }
p.lede, .muted { color: var(--muted); }
.panel { background: var(--panel); border: 1px solid var(--line); border-radius: 8px;
  padding: 16px; }
.grid { display: grid; gap: 12px; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); }
.stat b { display: block; font-size: 22px; } .stat span { color: var(--muted); font-size: 13px; }
.table-wrap { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: 14px; }
th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--line);
  white-space: nowrap; }
th { color: var(--muted); font-weight: 600; }
td.num { font-variant-numeric: tabular-nums; text-align: right; }
.badge { display: inline-block; padding: 1px 8px; border-radius: 10px; font-size: 12px;
  font-weight: 700; color: var(--bg); }
.badge.go { background: var(--go); } .badge.review { background: var(--review); }
.badge.nogo { background: var(--nogo); }
a { color: var(--accent); }
svg { width: 100%; height: auto; display: block; }
svg text { fill: var(--muted); font-size: 11px; }
svg .bar { fill: var(--accent); } svg .axis { stroke: var(--line); }
svg .s0 { stroke: var(--accent); } svg .s1 { stroke: var(--review); }
svg .dot0 { fill: var(--accent); } svg .dot1 { fill: var(--review); }
footer { margin-top: 40px; color: var(--muted); font-size: 13px; }
"""


def e(value: Any) -> str:
    """HTML-escape str(value), quotes included; None becomes an empty string."""
    return html.escape("" if value is None else str(value))


def f4(value: Any) -> str:
    """Format a number with four decimal places, or "n/a" when it is None."""
    return "n/a" if value is None else f"{float(value):.4f}"


def g2(value: Any) -> str:
    """Format a number to two significant figures with %g, or "n/a" when it is None."""
    return "n/a" if value is None else f"{float(value):.2g}"


def badge(verdict: str) -> str:
    """HTML badge for a gate verdict (GO, NEEDS_REVIEW or NO_GO).

    An unknown verdict gets no color class and is shown as its raw text.
    """
    css, text = VERDICT_CLASS.get(verdict, ""), VERDICT_TEXT.get(verdict, verdict)
    return f'<span class="badge {css}">{e(text)}</span>'


def _svg_open(width: int, height: int, label: str) -> str:
    return f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{e(label)}">'


def _axis(width: int, height: int, pad: int) -> str:
    y = height - pad
    return f'<line class="axis" x1="{pad}" y1="{y}" x2="{width - pad}" y2="{y}"/>'


def bar_chart(values: list[float], labels: list[str], unit: str) -> str:
    """Inline SVG with one vertical bar per value, scaled to the largest value.

    Each bar has its value (two significant figures) above it and its label below. The SVG's
    aria-label reads "SCI per candidate in {unit}". With no values, returns a short paragraph
    saying there are no measured candidates instead of an SVG.
    """
    if not values:
        return '<p class="muted">No measured candidates yet.</p>'
    width, height, pad = 640, 220, 32
    top = max(values) or 1.0
    step = (width - 2 * pad) / len(values)
    bar = step * 0.6
    parts = [
        _svg_open(width, height, f"SCI per candidate in {unit}"),
        _axis(width, height, pad),
    ]
    for i, (value, label) in enumerate(zip(values, labels, strict=True)):
        h = (height - 2 * pad - 14) * value / top
        x = pad + i * step + (step - bar) / 2
        y = height - pad - h
        parts.append(
            f'<rect class="bar" x="{x:.1f}" y="{y:.1f}" width="{bar:.1f}" height="{h:.1f}"/>'
        )
        parts.append(
            f'<text x="{x + bar / 2:.1f}" y="{y - 4:.1f}" text-anchor="middle">{g2(value)}</text>'
        )
        base = height - pad + 14
        parts.append(
            f'<text x="{x + bar / 2:.1f}" y="{base}" text-anchor="middle">{e(label)}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def line_chart(labels: list[str], series: dict[str, list[float | None]], unit: str) -> str:
    """Inline SVG line chart of up to two series over the labels, sharing one y axis.

    Only two series have line colors in CSS. None points are skipped, so the line joins
    across them. The y axis shows its minimum and maximum, the x axis only the first and last
    label, and unit appears only in the aria-label. With no labels, returns a short paragraph
    saying there are no drift checks; with labels but no non-None value, min() raises
    ValueError.
    """
    if not labels:
        return '<p class="muted">No drift checks yet.</p>'
    width, height, pad = 640, 220, 36
    values = [v for s in series.values() for v in s if v is not None]
    lo, hi = min(values), max(values)
    if hi - lo < 1e-9:
        lo, hi = lo - 0.01, hi + 0.01
    n = len(labels)

    def xy(i: int, v: float) -> tuple[float, float]:
        """SVG coordinates of point i with value v."""
        x = pad + (width - 2 * pad) * (i / (n - 1) if n > 1 else 0.5)
        y = height - pad - (height - 2 * pad) * (v - lo) / (hi - lo)
        return x, y

    parts = [
        _svg_open(width, height, f"Drift over time in {unit}"),
        _axis(width, height, pad),
        f'<text x="4" y="{pad}">{hi:.3f}</text><text x="4" y="{height - pad}">{lo:.3f}</text>',
    ]
    for k, (name, points) in enumerate(series.items()):
        coords = [xy(i, v) for i, v in enumerate(points) if v is not None]
        path = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
        parts.append(f'<polyline class="s{k}" fill="none" stroke-width="2" points="{path}"/>')
        parts += [f'<circle class="dot{k}" cx="{x:.1f}" cy="{y:.1f}" r="3"/>' for x, y in coords]
        parts.append(
            f'<text x="{width - pad}" y="{14 + 14 * k}" text-anchor="end">{e(name)}</text>'
        )
    for i in sorted({0, n - 1}):
        x, _ = xy(i, lo)
        parts.append(
            f'<text x="{x:.1f}" y="{height - pad + 14}" text-anchor="middle">{e(labels[i])}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


_INLINE_CODE = re.compile(r"`([^`]+)`")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_URL = re.compile(r"(?<![\"=])(https?://[^\s<|)]+)")


def _inline(text: str) -> str:
    out = e(text)
    out = _INLINE_CODE.sub(r"<code>\1</code>", out)
    out = _BOLD.sub(r"<strong>\1</strong>", out)
    return _URL.sub(_link, out)


def _link(match: re.Match[str]) -> str:
    url = match.group(1)
    trail = ""
    while url and url[-1] in ".,;:":  # sentence punctuation is not part of the address
        url, trail = url[:-1], url[-1] + trail
    return f'<a href="{url}">{url}</a>{trail}'


def markdown_to_html(markdown: str) -> str:
    """Convert the subset of Markdown that modelgate/card.py writes to HTML.

    Handles # to ### headings, pipe tables (the first row becomes the header, separator rows
    are skipped), "- " bullet lists, paragraphs, inline code, bold and bare http(s) links. All
    text is HTML-escaped first. A line that is a whole HTML comment, such as card.py's section
    markers, is dropped; a comment spanning several lines is not.
    """
    out: list[str] = []
    table: list[list[str]] = []
    items: list[str] = []
    para: list[str] = []

    def flush() -> None:
        """Emit the pending table, list or paragraph and clear it."""
        if table:
            head, *rows = table
            out.append('<div class="table-wrap"><table><tr>')
            out.extend(f"<th>{_inline(c)}</th>" for c in head)
            out.append("</tr>")
            for row in rows:
                out.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in row) + "</tr>")
            out.append("</table></div>")
            table.clear()
        if items:
            out.append("<ul>" + "".join(f"<li>{_inline(i)}</li>" for i in items) + "</ul>")
            items.clear()
        if para:
            out.append(f"<p>{_inline(' '.join(para))}</p>")
            para.clear()

    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("<!--") and stripped.endswith("-->"):
            continue
        if stripped.startswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if all(set(c) <= {"-", " ", ":"} for c in cells):
                continue
            if items or para:
                flush()
            table.append(cells)
            continue
        if stripped.startswith("- "):
            if table or para:
                flush()
            items.append(stripped[2:])
            continue
        if not stripped:
            flush()
            continue
        if table or items:
            flush()
        heading = re.match(r"(#{1,3}) (.*)", stripped)
        if heading:
            flush()
            level = len(heading.group(1))
            out.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
        else:
            para.append(stripped)
    flush()
    return "\n".join(out)


def card_page(name: str, markdown: str) -> str:
    """Standalone HTML page for one model card, rendered from the card's Markdown.

    The page links back to ../index.html and to the card's own {name}.md file next to it.
    """
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ModelGate card {e(name)}</title><style>{CSS}</style></head>
<body><main><p><a href="../index.html">ModelGate dashboard</a> |
<a href="{e(name)}.md">markdown</a></p>
{markdown_to_html(markdown)}
</main></body></html>
"""


def fetch_drift_history(http: Http) -> list[dict[str, Any]] | None:
    """Fetch the nightly drift history from the default branch's latest successful pipeline.

    GitLab's /jobs/artifacts/<ref>/raw endpoint reads only that one pipeline. After a nightly
    run it is the schedule pipeline, and the history is its drift_check job's
    reports/drift_history.json. After a push to the default branch, such as a merge, it is a
    push pipeline with no drift_check job; the history is then the drift_history that its
    pages job carried forward in public/data.json. Uses CI_JOB_TOKEN, CI_API_V4_URL,
    CI_PROJECT_ID and CI_DEFAULT_BRANCH (main when unset). Returns None when CI_JOB_TOKEN is
    unset or neither file can be read; raises KeyError when the token is set but
    CI_API_V4_URL or CI_PROJECT_ID is not.
    """
    token = os.environ.get("CI_JOB_TOKEN")
    if not token:
        return None
    ref = urllib.parse.quote(os.environ.get("CI_DEFAULT_BRANCH", "main"), safe="")
    base = (
        f"{os.environ['CI_API_V4_URL'].rstrip('/')}/projects/{os.environ['CI_PROJECT_ID']}"
        f"/jobs/artifacts/{ref}/raw"
    )
    headers = {"JOB-TOKEN": token}
    resp = http("GET", f"{base}/reports/drift_history.json?job=drift_check", headers, None)
    if resp.status == 200:
        return json.loads(resp.raw)
    pages = http("GET", f"{base}/public/data.json?job=pages", headers, None)
    if pages.status == 200:
        history = json.loads(pages.raw).get("drift_history")
        if history:
            return history
    logger.info("no drift history yet (HTTP %s, pages HTTP %s)", resp.status, pages.status)
    return None


def _flow_credits(paths: Paths) -> float | None:
    """flow_credits_per_run from config/sci.yaml, or None when not recorded."""
    sci = paths.config / "sci.yaml"
    if not sci.exists():
        return None
    value = (load_yaml(sci) or {}).get("flow_credits_per_run")
    return None if value is None else float(value)


def load(paths: Paths, http: Http) -> dict[str, Any]:
    """Collect everything the dashboard shows into one dict.

    Decision records from models/decisions/*.json are sorted by pipeline IID. The drift history
    is read from reports/drift_history.json when that file exists (and holds non-null JSON),
    otherwise from fetch_drift_history, and is an empty list when neither has one. The
    champion and reports/promotion.json are None when their files are missing, and the
    champion's cumulative SCI is None when there is no champion. project_url and commit come
    from CI_PROJECT_URL and CI_COMMIT_SHA.
    """
    decisions = [read_json(p) for p in sorted((paths.models / "decisions").glob("*.json"))]
    decisions.sort(key=lambda d: d["lineage"]["pipeline_iid"])
    local = paths.reports / "drift_history.json"
    drift = json.loads(local.read_text(encoding="utf-8")) if local.exists() else None
    if drift is None:
        drift = fetch_drift_history(http)
    champion = read_json_if_exists(paths.champion_file)
    return {
        "champion": champion,
        "champion_cumulative_sci": cumulative_sci(paths, champion) if champion else None,
        "carbon_by_verdict": carbon_by_verdict(decisions),
        "flow_credits_per_run": _flow_credits(paths),
        "decisions": decisions,
        "drift_history": drift or [],
        "promotion": read_json_if_exists(paths.reports / "promotion.json"),
        "project_url": os.environ.get("CI_PROJECT_URL"),
        "commit": os.environ.get("CI_COMMIT_SHA"),
    }


def _champion_section(data: dict[str, Any]) -> str:
    champ = data["champion"]
    if not champ:
        return '<p class="muted">No champion yet. The first GO merge request creates 0.1.0.</p>'
    o = champ["metrics"]["overall"]
    sci = (champ.get("sci") or {}).get("sci_gco2e_per_candidate")
    stats = [
        (champ["version"], "champion version"),
        (f4(o["auc"]), "holdout AUC"),
        (f4(o["brier"]), "Brier score"),
        (f4(champ["metrics"]["segment_auc_gap"]), "segment AUC gap"),
        (f"{g2(sci)} g", "SCI of its candidate (gCO2e)"),
    ]
    rows = "".join(
        f"<tr><td>{e(name)}</td><td class='num'>{e(seg['n'])}</td>"
        f"<td class='num'>{f4(seg['positive_rate'])}</td><td class='num'>{f4(seg['auc'])}</td></tr>"
        for name, seg in champ["metrics"]["segments"].items()
    )
    links = []
    url = data.get("project_url")
    if url and champ.get("card"):
        links.append(f'<a href="{e(url)}/-/blob/main/{e(champ["card"])}">model card on GitLab</a>')
    if champ.get("card"):
        stem = champ["card"].rsplit("/", 1)[-1].removesuffix(".md")
        links.append(f'<a href="cards/{e(stem)}.html">model card</a>')
    promotion = data.get("promotion") or {}
    if promotion.get("release"):
        release = promotion["release"]
        links.append(f'<a href="{e(release["url"])}">Release {e(release["tag"])}</a>')
    features = ", ".join(champ["config"]["features"])
    return (
        '<div class="panel"><div class="grid">'
        + "".join(f'<div class="stat"><b>{e(v)}</b><span>{e(k)}</span></div>' for v, k in stats)
        + "</div>"
        + f'<p class="muted">Candidate {e(champ.get("candidate_version"))}, '
        f"{e(champ['config']['model_family'])} on {e(features)}.</p>"
        + '<div class="table-wrap"><table><tr><th>Segment</th><th>Rows</th><th>Positive rate</th>'
        f"<th>AUC</th></tr>{rows}</table></div>"
        + (f"<p>{' | '.join(links)}</p>" if links else "")
        + "</div>"
    )


def _history_section(data: dict[str, Any]) -> str:
    if not data["decisions"]:
        return '<p class="muted">No candidates yet.</p>'
    rows = []
    for d in reversed(data["decisions"]):
        o = d["challenger"]["metrics"]["overall"]
        delta = (d.get("comparison") or {}).get("auc_delta")
        sci = (d.get("sci") or {}).get("sci_gco2e_per_candidate")
        name = d["candidate_version"]
        rows.append(
            f"<tr><td><a href='cards/{e(name)}.html'>{e(name)}</a></td>"
            f"<td>{badge(d['verdict'])}</td>"
            f"<td>{e(d['lineage']['branch'])}</td><td class='num'>{f4(o['auc'])}</td>"
            f"<td class='num'>{'n/a' if delta is None else f'{delta:+.4f}'}</td>"
            f"<td class='num'>{f4(d['challenger']['metrics']['segment_auc_gap'])}</td>"
            f"<td>{e(', '.join(d['failed_rules']) or 'none')}</td>"
            f"<td class='num'>{g2(sci)}</td></tr>"
        )
    return (
        '<div class="table-wrap"><table><tr><th>Candidate</th><th>Verdict</th><th>Branch</th>'
        "<th>AUC</th><th>AUC vs champion</th><th>Segment gap</th><th>Failed rules</th>"
        "<th>SCI (gCO2e)</th></tr>" + "".join(rows) + "</table></div>"
    )


def carbon_by_verdict(decisions: list[dict[str, Any]]) -> dict[str, Any]:
    """SCI totals by verdict, and the promotion retrains the rejected candidates avoided.

    A promotion retrains the model once on the default branch, in the same train_evaluate
    job a candidate already ran. So a rejected candidate's avoided cost is estimated as its
    own measured SCI. Promotion jobs after the retrain (registry, Release, Pages) are small
    and not measured, so the estimate is a lower bound on what promotion would have cost.
    """
    totals: dict[str, float] = {}
    counts: dict[str, int] = {}
    for d in decisions:
        grams = (d.get("sci") or {}).get("sci_gco2e_per_candidate")
        if grams is None:
            continue
        totals[d["verdict"]] = totals.get(d["verdict"], 0.0) + grams
        counts[d["verdict"]] = counts.get(d["verdict"], 0) + 1
    rejected = [v for v in ("NO_GO", "NEEDS_REVIEW") if v in totals]
    avoided = sum(totals[v] for v in rejected)
    return {
        "gco2e_by_verdict": {k: float(f"{v:.2g}") for k, v in sorted(totals.items())},
        "candidates_by_verdict": dict(sorted(counts.items())),
        "rejected_candidates": sum(counts[v] for v in rejected),
        "avoided_promotion_retrain_gco2e": float(f"{avoided:.2g}") if rejected else None,
    }


def _carbon_section(data: dict[str, Any]) -> str:
    measured = [d for d in data["decisions"] if (d.get("sci") or {}).get("sci_gco2e_per_candidate")]
    values = [d["sci"]["sci_gco2e_per_candidate"] for d in measured]
    labels = [d["candidate_version"].split("-rc.")[-1] for d in measured]
    total = sum(values)
    cumulative = data.get("champion_cumulative_sci") or {}
    champ = data.get("champion") or {}
    to_reach = (
        f"Carbon to reach champion {e(champ.get('version'))}: {g2(cumulative['gco2e'])} gCO2e "
        f"over {e(cumulative.get('candidates'))} candidate(s) on "
        f"<code>{e(cumulative.get('branch'))}</code>. "
        if cumulative.get("gco2e") is not None
        else ""
    )
    split = carbon_by_verdict(data["decisions"])
    rows = "".join(
        f"<tr><td>{badge(verdict)}</td><td class='num'>{split['candidates_by_verdict'][verdict]}"
        f"</td><td class='num'>{g2(grams)}</td></tr>"
        for verdict, grams in split["gco2e_by_verdict"].items()
    )
    avoided = split["avoided_promotion_retrain_gco2e"]
    avoided_text = (
        f"<p>The gate stopped {split['rejected_candidates']} candidate(s) before promotion. "
        f"Each would have needed a promotion retrain on the default branch: an estimated "
        f"{g2(avoided)} gCO2e avoided (each candidate's own measured SCI, since promotion "
        "reruns the same job; promotion's smaller jobs are not counted).</p>"
        if avoided is not None
        else ""
    )
    credits = data.get("flow_credits_per_run")
    flow_cost = (
        f"The Duo flow's runs are not measured in energy; one run cost about {e(credits)} GitLab "
        "Credits (from the Credits dashboard)."
        if credits is not None
        else "The Duo flow's own runs are not measured."
    )
    return (
        f"<p>Software Carbon Intensity per evaluated candidate (gCO2e), by pipeline. {to_reach}"
        f"All {len(values)} candidate(s) together: {g2(total) if values else 'n/a'} gCO2e. "
        f"Method and assumptions: docs/SCI.md. {flow_cost}</p>"
        + bar_chart(values, labels, "gCO2e")
        + (
            '<div class="table-wrap"><table><tr><th>Verdict</th><th>Candidates</th>'
            f"<th>SCI total (gCO2e)</th></tr>{rows}</table></div>"
            if rows
            else ""
        )
        + avoided_text
    )


def _drift_section(data: dict[str, Any]) -> str:
    history = data["drift_history"]
    if not history:
        return (
            '<p class="muted">No drift checks yet. The nightly schedule adds one point per day.</p>'
        )
    labels = [h["date"] for h in history]
    chart = line_chart(
        labels,
        {
            "AUC on the drift batch": [h["drift"]["auc"] for h in history],
            "AUC on the holdout": [h["holdout_auc"] for h in history],
        },
        "AUC",
    )
    last = history[-1]
    rows = "".join(
        f"<tr><td>{e(name)}</td><td class='num'>{f4(value)}</td></tr>"
        for name, value in sorted(last["psi"].items(), key=lambda kv: -kv[1])
    )
    status = "alert" if last["alert"] else "within limits"
    return (
        f"<p>Champion {e(last['champion_version'])} scored nightly on a freshly drawn drift batch. "
        f"Latest ({e(last['date'])}): AUC {f4(last['drift']['auc'])} against "
        f"{f4(last['holdout_auc'])} on the holdout, largest PSI {f4(last['max_psi'])}: "
        f"<b>{e(status)}</b>.</p>"
        + chart
        + '<div class="table-wrap"><table><tr><th>Feature</th><th>PSI (latest)</th></tr>'
        + rows
        + "</table></div>"
    )


def render(data: dict[str, Any]) -> str:
    """Return the HTML text of the dashboard's index.html, built from the dict load returns."""
    url = data.get("project_url")
    source = f'<a href="{e(url)}">{e(url)}</a>' if url else "the ModelGate repository"
    commit = f", commit {e(str(data['commit'])[:12])}" if data.get("commit") else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ModelGate dashboard</title><style>{CSS}</style></head>
<body><main>
<h1>ModelGate</h1>
<p class="lede">Every model gets a merge request. The champion in production, every candidate
whose record reached the default branch, what they cost in carbon, and how the champion holds
up on drifted data.</p>
<h2>Champion</h2>{_champion_section(data)}
<h2>Candidates</h2>{_history_section(data)}
<h2>Carbon</h2>{_carbon_section(data)}
<h2>Drift</h2>{_drift_section(data)}
<footer>Built from {source}{commit}. Synthetic data only. Data as JSON:
<a href="data.json">data.json</a>.</footer>
</main></body></html>
"""


def main(paths: Paths, http: Http = urllib_http) -> dict[str, Any]:
    """Build the site into public/ and return the data it was built from.

    Loads the data, then deletes any existing <root>/public, copies models/cards and
    models/decisions into it when they exist (leaving out .gitkeep files), writes an HTML page
    next to each copied card's Markdown, then writes index.html and data.json.
    """
    data = load(paths, http)
    out = paths.root / "public"
    if out.exists():
        shutil.rmtree(out)
    for sub in ("cards", "decisions"):
        source = paths.models / sub
        if source.is_dir():
            shutil.copytree(source, out / sub, ignore=shutil.ignore_patterns(".gitkeep"))
    for card_md in sorted((out / "cards").glob("*.md")) if (out / "cards").is_dir() else []:
        page = card_page(card_md.stem, card_md.read_text(encoding="utf-8"))
        card_md.with_suffix(".html").write_text(page, encoding="utf-8", newline="\n")
    (out / "index.html").parent.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(render(data), encoding="utf-8", newline="\n")
    write_json(out / "data.json", data)
    logger.info(
        "site: %d candidate(s), champion %s, %d drift point(s) -> public/",
        len(data["decisions"]),
        (data["champion"] or {}).get("version"),
        len(data["drift_history"]),
    )
    return data
