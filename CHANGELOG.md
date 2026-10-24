# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-10-24

### Added
- `ui-review <url-or-dir>`: reviews a deployed site or a local static build (served on
  loopback), crawling same-origin pages breadth-first within page and depth budgets.
- Headless Chromium through Playwright, one context per viewport (mobile 390 px, tablet
  768 px, desktop 1280 px) and color scheme (light, dark), with reduced motion.
- ReAct loop (observe -> think -> act) over the tools `goto`, `click`, `scroll`,
  `read_dom`, `run_checks`, `axe_audit`, `screenshot`, `compare_baseline`, `critique`,
  `finish`, with a deterministic scripted sweep and an optional ollama tool-calling
  exploration phase. Every step is written to `trace.jsonl`.
- Deterministic checks: HTTP errors, uncaught exceptions, `console.error`, failed
  same-origin requests, broken images and links, horizontal overflow, overlapping and
  clipped text from DOM geometry, tap targets under 24 px on touch viewports, and
  axe-core 4.14 (WCAG 2.2 A/AA, contrast) in both color schemes.
- Visual regression: tolerant pixel diff plus a 64-bit perceptual hash against stored
  baselines, with highlighted diff images and `--update-baseline`.
- Optional vision pass by a local VLM on ollama (`qwen2.5vl:7b` by default) against a QA
  rubric; skipped cleanly when ollama or the model is missing.
- Reports: static HTML (findings by severity with screenshots and diffs, page gallery),
  JSON, JUnit XML, and exit code 1 when findings reach `--fail-on`.
- Sandbox: non-GET requests, off-scope hosts and WebSockets blocked at the browser,
  form submission cancelled, pop-ups closed, dialogs dismissed, service workers and
  downloads off, loopback-only ollama and CDP.
- `examples/site/` demo with eleven deliberate bugs and an example GitHub Actions workflow.
