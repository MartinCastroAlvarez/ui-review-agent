# Contributing to ui-review-agent

Thanks for helping. The package is small on purpose: one sandboxed browser, a handful of
tools, deterministic checks, an optional local model, and reports.

## TL;DR

```bash
poetry install
poetry run playwright install --with-deps --only-shell chromium
UI_REVIEW_REQUIRE_BROWSER=1 poetry run pytest      # real Chromium, fake ollama
poetry run ruff check ui_review tests && poetry run ruff format --check ui_review tests
poetry run mypy ui_review && poetry run bandit -c pyproject.toml -q -r ui_review
poetry run pip-audit
poetry run ui-review examples/site                 # exits 1: the demo has bugs
```

No Playwright build for your platform? Point `UI_REVIEW_CHROMIUM_PATH` at any recent
Chromium or headless shell, or attach to one with `UI_REVIEW_CDP_URL` (loopback only).

## Adding a check

1. Measure it in `ui_review/js/dom_checks.js` (pure reads, bounded loops) or in Python
   in `Environment.run_checks`.
2. Register its id and label in `CHECKS` (`ui_review/findings.py`) and pick a severity.
3. Add a deliberate bug to `examples/site/`, document it in `examples/site/README.md`,
   and assert it in `tests/test_demo_site.py`, plus a no-false-positive assertion.

## Adding a tool

Add a `ToolSpec` and a method on `Environment` returning `ToolResult`. Wrap anything that
came from the page with `untrusted()`. If the tool can cause side effects, refuse them
and test the refusal in `tests/test_sandbox.py`.

## Pull requests

Branch from `main` (`feat/…`, `fix/…`, `docs/…`), keep commits small, update
`CHANGELOG.md` and the README. CI must be green on Python 3.11 to 3.13.
