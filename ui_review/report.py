"""Reports: a static HTML page, JSON, JUnit XML for CI, and the agent trace as JSONL.

Every string that came from a page or a model is escaped with :func:`html.escape`. The
HTML report has no script at all and a CSP that forbids one.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from datetime import UTC
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ui_review.findings import CHECKS
from ui_review.findings import Finding
from ui_review.findings import Severity
from ui_review.review import ReviewResult

_CSP = "default-src 'none'; img-src 'self' data: file:; style-src 'unsafe-inline'"
_XML_INVALID = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")


def to_dict(result: ReviewResult) -> dict[str, Any]:
    cfg = result.config
    return {
        "tool": "ui-review-agent",
        "version": result.version,
        "start_url": result.start_url,
        "started_at": datetime.fromtimestamp(result.started_at, UTC).isoformat(),
        "duration_s": result.duration_s,
        "browser": result.browser,
        "views": [f"{v.name}-{s}" for v, s in cfg.views()],
        "fail_on": cfg.fail_on.value,
        "failed": result.failed,
        "counts": result.findings.counts(),
        "vision_model": result.vision_model,
        "agent_model": result.agent_model,
        "notes": result.notes,
        "pages": [asdict(p) for p in result.pages],
        "findings": [f.to_dict() for f in result.findings],
        "blocked": [asdict(b) for b in result.blocked],
    }


def write_reports(result: ReviewResult, out_dir: Path | None = None) -> dict[str, Path]:
    out = out_dir or result.config.out_dir
    out.mkdir(parents=True, exist_ok=True)
    paths = {
        "html": out / "report.html",
        "json": out / "report.json",
        "junit": out / "junit.xml",
        "trace": out / "trace.jsonl",
    }
    paths["json"].write_text(json.dumps(to_dict(result), indent=2, ensure_ascii=False) + "\n")
    paths["junit"].write_text(junit(result))
    paths["html"].write_text(render_html(result))
    with paths["trace"].open("w", encoding="utf-8") as fh:
        for step in result.trace:
            fh.write(json.dumps(asdict(step), ensure_ascii=False, default=str) + "\n")
    return paths


# -- JUnit ------------------------------------------------------------------------------
def _x(text: object) -> str:
    return escape(_XML_INVALID.sub("", str(text)), quote=True)


def junit(result: ReviewResult) -> str:
    """One testsuite per page, one testcase per check; findings >= fail_on are failures."""
    fail_on = result.config.fail_on
    suites = []
    total = failures = 0
    for page in result.pages:
        cases = []
        page_fail = 0
        for check, label in CHECKS.items():
            found = [f for f in result.findings if f.url == page.url and f.check == check]
            failing = [f for f in found if f.severity.at_least(fail_on)]
            body = ""
            if failing:
                page_fail += 1
                message = f"{len(failing)} finding(s): " + "; ".join(f.title for f in failing[:5])
                text = "\n\n".join(_finding_text(f) for f in failing)
                body += f'<failure message="{_x(message)}" type="{_x(check)}">{_x(text)}</failure>'
            advisory = [f for f in found if not f.severity.at_least(fail_on)]
            if advisory:
                out = "\n".join(_finding_text(f) for f in advisory)
                body += f"<system-out>{_x(out)}</system-out>"
            cases.append(
                f'    <testcase classname="{_x(_page_name(page.url))}" name="{_x(check)}: '
                f'{_x(label)}">{body}</testcase>'
            )
        total += len(cases)
        failures += page_fail
        suites.append(
            f'  <testsuite name="{_x(page.url)}" tests="{len(cases)}" failures="{page_fail}" '
            f'errors="0" skipped="0">\n' + "\n".join(cases) + "\n  </testsuite>"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<testsuites name="ui-review" tests="{total}" failures="{failures}" '
        f'time="{result.duration_s}">\n' + "\n".join(suites) + "\n</testsuites>\n"
    )


def _finding_text(f: Finding) -> str:
    views = ", ".join(f.views)
    return f"[{f.severity.value}] {f.title} ({views})\n{f.detail}"


def _page_name(url: str) -> str:
    parts = urlsplit(url)
    return (parts.path or "/") + (f"?{parts.query}" if parts.query else "")


# -- HTML -------------------------------------------------------------------------------
_CSS = """
:root{--bg:#f6f7fb;--card:#fff;--ink:#141826;--muted:#5b6275;--line:#e3e6ef;
--accent:#4f46e5;--critical:#b91c1c;--major:#c2410c;--minor:#a16207;--info:#475569;
color-scheme:light dark}
@media (prefers-color-scheme:dark){:root{--bg:#0b1020;--card:#141a2e;--ink:#e7e9f3;
--muted:#9aa3bd;--line:#252d48;--accent:#8b85ff;--critical:#f87171;--major:#fb923c;
--minor:#facc15;--info:#94a3b8}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:1180px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:26px;margin:0 0 4px}h2{font-size:19px;margin:32px 0 12px}
a{color:var(--accent)}code{font:13px ui-monospace,SFMono-Regular,Menlo,monospace;
overflow-wrap:anywhere}.muted{color:var(--muted)}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin:16px 0}
.chip{border:1px solid var(--line);background:var(--card);border-radius:999px;padding:4px 12px;
font-size:13px}.chip b{font-size:15px}
.sev{display:inline-block;border-radius:6px;padding:1px 8px;font-size:12px;font-weight:700;
text-transform:uppercase;color:var(--bg)}
.sev-critical{background:var(--critical)}.sev-major{background:var(--major)}
.sev-minor{background:var(--minor)}.sev-info{background:var(--info)}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;
margin:10px 0;display:grid;grid-template-columns:1fr auto;gap:12px}
.card h3{font-size:15px;margin:4px 0;overflow-wrap:anywhere}
.card p{margin:6px 0;white-space:pre-wrap;overflow-wrap:anywhere;font-size:14px}
.shots{display:flex;gap:8px;align-items:flex-start}
.shots figure{margin:0;text-align:center;font-size:11px;color:var(--muted)}
.shots img{width:120px;height:150px;object-fit:cover;object-position:top;border-radius:6px;
border:1px solid var(--line);display:block}
.gallery{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px}
.gallery figure{margin:0;font-size:12px;color:var(--muted)}
.gallery img{width:100%;height:220px;object-fit:cover;object-position:top;border-radius:8px;
border:1px solid var(--line);display:block}
details>summary{cursor:pointer;font-weight:600;font-size:17px;margin:18px 0 6px}
.notes{border-left:3px solid var(--accent);padding:4px 12px;background:var(--card)}
table{border-collapse:collapse;width:100%;font-size:13px}
td,th{border-bottom:1px solid var(--line);padding:6px;text-align:left;vertical-align:top}
@media (max-width:640px){.card{grid-template-columns:1fr}.shots img{width:96px;height:120px}}
"""


def _img(path: str | None, caption: str) -> str:
    if not path:
        return ""
    src = escape(path, quote=True)
    return (
        f'<figure><a href="{src}"><img loading="lazy" src="{src}" alt="{escape(caption)}"></a>'
        f"{escape(caption)}</figure>"
    )


def _card(f: Finding) -> str:
    views = " ".join(f'<span class="chip">{escape(v)}</span>' for v in f.views)
    selectors = "".join(f"<li><code>{escape(s)}</code></li>" for s in f.selectors[:5])
    shots = _img(f.screenshot, "screenshot") + _img(f.diff, "diff")
    return (
        '<article class="card"><div>'
        f'<span class="sev sev-{f.severity.value}">{f.severity.value}</span> '
        f'<span class="muted">{escape(CHECKS[f.check])}</span>'
        f"<h3>{escape(f.title)}</h3>"
        f'<div class="muted"><code>{escape(_page_name(f.url))}</code> · {views}</div>'
        + (f"<p>{escape(f.detail)}</p>" if f.detail else "")
        + (f"<ul>{selectors}</ul>" if selectors else "")
        + (
            f'<div class="muted">source: {escape(f.source)}</div>'
            if f.source != "deterministic"
            else ""
        )
        + f'</div><div class="shots">{shots}</div></article>'
    )


def render_html(result: ReviewResult) -> str:
    counts = result.findings.counts()
    status = "FAILED" if result.failed else "PASSED"
    chips = "".join(
        f'<span class="chip"><span class="sev sev-{s.value}">{s.value}</span> '
        f"<b>{counts[s.value]}</b></span>"
        for s in Severity
    )
    meta = (
        f'<span class="chip">gate <b>{status}</b> (fail on ≥ {result.config.fail_on.value})</span>'
        f'<span class="chip"><b>{len(result.pages)}</b> pages</span>'
        f'<span class="chip"><b>{len(result.config.views())}</b> views</span>'
        f'<span class="chip"><b>{len(result.trace)}</b> agent steps</span>'
        f'<span class="chip"><b>{result.duration_s}</b> s</span>'
        f'<span class="chip">Chromium {escape(result.browser)}</span>'
        f'<span class="chip">vision: {escape(result.vision_model or "off")}</span>'
    )
    notes = "".join(f"<li>{escape(n)}</li>" for n in result.notes)
    sections = []
    for sev in Severity:
        items = [f for f in result.findings if f.severity == sev]
        if not items:
            continue
        cards = "".join(_card(f) for f in items)
        sections.append(
            f"<details {'open' if sev != Severity.INFO else ''}><summary>"
            f'<span class="sev sev-{sev.value}">{sev.value}</span> {len(items)} findings'
            f"</summary>{cards}</details>"
        )
    pages = []
    for page in result.pages:
        figs = "".join(_img(path, view) for view, path in page.shots.items())
        pages.append(
            f"<h3>{escape(page.title or _page_name(page.url))} "
            f'<span class="muted"><code>{escape(_page_name(page.url))}</code> · HTTP '
            f"{escape(str(page.status))}</span></h3>"
            f'<div class="gallery">{figs}</div>'
        )
    started = datetime.fromtimestamp(result.started_at, UTC).strftime("%Y-%m-%d %H:%M UTC")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="{_CSP}">
<meta name="referrer" content="no-referrer">
<title>UI review report</title><style>{_CSS}</style></head>
<body><main>
<h1>UI review: {escape(result.start_url)}</h1>
<div class="muted">ui-review-agent {escape(result.version)} · {started}</div>
<div class="chips">{chips}</div><div class="chips">{meta}</div>
{f'<ul class="notes">{notes}</ul>' if notes else ""}
<h2>Findings</h2>
{"".join(sections) or "<p>No findings.</p>"}
<h2>Pages</h2>
{"".join(pages)}
<p class="muted">Machine-readable: <a href="report.json">report.json</a> ·
<a href="junit.xml">junit.xml</a> · <a href="trace.jsonl">trace.jsonl</a></p>
</main></body></html>
"""
