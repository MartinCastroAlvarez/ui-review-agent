# Architecture

```
ui_review/
  config.py      Config (defaults + UI_REVIEW_* env), viewports, loopback checks, target resolution
  scope.py       URL normalisation, same-origin + allowlist scope, crawlability, slugs
  server.py      loopback static server for a local build (GET/HEAD, path-guarded)
  browser.py     BrowserSession: Playwright Chromium, one sandboxed context per view, event capture
  js/            guard.js (form/popup guard), read_dom.js, dom_checks.js (geometry checks)
  vendor/        axe-core 4.14 (MPL-2.0), injected for accessibility audits
  tools.py       Environment: crawl frontier, findings, the agent's tools
  agent.py       Agent (ReAct loop), ScriptedPolicy (sweep), OllamaPolicy (exploration)
  visual.py      pixel diff with tolerance, dHash, diff images, baselines
  vision.py      VLM rubric, image encoding, schema-validated parsing
  ollama.py      tiny httpx client: /api/tags, /api/chat (tools, images, format)
  findings.py    Severity, Finding, Findings (merged by fingerprint across views)
  review.py      review(): serve -> sandbox -> sweep -> explore -> attach screenshots
  report.py      report.html, report.json, junit.xml, trace.jsonl
  cli.py         ui-review command, exit codes 0 / 1 (findings) / 2 (error)
```

## Data flow

1. **Target.** A URL is used as is. A directory (or a file in it) is served by
   `StaticServer` on `127.0.0.1:<random>`, so a build is reviewed with real HTTP
   semantics and a real origin.
2. **Scope.** The start URL's origin, plus `--allow-host` entries. `index.html` and the
   directory are one page.
3. **Browser.** One context per (viewport, scheme): `mobile` 390×844 touch, `tablet`
   768×1024 touch, `desktop` 1280×800, each `light` and `dark`, reduced motion, UTC,
   `en-US`. Every context routes requests through the guard, mocks WebSockets, blocks
   service workers and downloads, injects `guard.js`, closes pop-ups, dismisses dialogs,
   and records `console.error`, uncaught exceptions, failed and blocked requests.
4. **Sweep (ReAct, scripted).** The `Agent` asks a policy for a `Decision(thought, tool,
   args)`, runs it through `Environment.call`, records a `TraceStep`, and feeds the
   result back as an `Observation`. `ScriptedPolicy` pops the next page from the
   breadth-first frontier and, for every view, plans: `goto` → `scroll` bottom → `scroll`
   top → `read_dom` (queues new links) → `run_checks` → `axe_audit` → `screenshot` →
   `compare_baseline` (with `--baseline`) → `critique` (with a vision model). Links that
   failed the link check are never crawled.
5. **Explore (ReAct, LLM, optional).** With `--explore N` and a pulled tool-calling
   model, `OllamaPolicy` drives the same tools: it gets a briefing (pages, findings so far)
   and each tool result, wrapped as untrusted, and returns one tool call per turn until
   `finish` or the step budget.
6. **Findings.** Each check emits `Finding`s with evidence (selectors, sizes, ratios).
   Findings with the same fingerprint (check, page, title, key, selectors) merge, so a
   bug seen in six views is one finding listing six views. Screenshots are attached
   after the sweep (checks run before the shot).
7. **Reports.** HTML grouped by severity with screenshot and diff thumbnails and a page
   gallery; JSON; JUnit (one suite per page, one case per check, failures at or above
   `--fail-on`); the full trace as JSONL.

## Deterministic checks

| Check | How |
| --- | --- |
| `http-status`, `load` | main document status ≥ 400, navigation timeout or error |
| `page-error`, `console-error` | Playwright `pageerror` / `console` events (network noise filtered) |
| `failed-request` | same-origin responses ≥ 400 and network failures (sandbox aborts excluded) |
| `blocked-request` | the guard's own aborts, reported as `info` |
| `broken-image` | `img.complete && naturalWidth === 0`, in-scope sources only |
| `broken-link` | `GET` without redirects for in-scope links, cached, at most `max_links` |
| `overflow` | `scrollWidth > clientWidth`; offenders are the outermost elements past the edge outside any scroll container |
| `overlap` | line boxes (`Range.getClientRects`) of unrelated text elements intersecting by more than 25 % of the smaller box |
| `clipped-text` | `overflow: hidden/clip` with `scrollWidth/Height` larger than the box, unless `text-overflow: ellipsis` or line-clamp |
| `tap-target` | touch viewports: interactive elements under 24×24 px (WCAG 2.5.8), inline links in sentences exempt |
| `a11y` | axe-core with `wcag2a`, `wcag2aa`, `wcag21a`, `wcag21aa`, `wcag22aa`, `best-practice`; impact maps to severity; runs in both schemes, so dark-mode contrast bugs show up |
| `visual-regression` | max per-channel difference > tolerance counted per pixel, sizes padded, ratio vs `max_diff_ratio`; `major` when the size or the 64-bit dHash (Hamming distance > 6) changes |
