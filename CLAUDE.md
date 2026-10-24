# CLAUDE.md

Contract between this repository and any AI agent contributing to it.

## Required reading

1. This file. 2. [`README.md`](README.md). 3. [`ARCHITECTURE.md`](ARCHITECTURE.md).
4. [`SECURITY.md`](SECURITY.md).

## Mission

`ui-review-agent` reviews a web UI the way a QA engineer would and reports what is
broken, with evidence, so a CI job can fail on it.

## The rules

1. **The review never changes the site.** Only GET requests leave the browser; forms are
   never submitted; nothing outside the scope is loaded. Keep `BrowserSession._guard`,
   `js/guard.js` and the `click` refusals, and give every new tool a hostile test.
2. **Page content is untrusted.** It reaches models only inside `<untrusted>` tags,
   escaped; reports escape everything and contain no script. Model output is parsed
   against a schema and vision findings stay advisory (at most `minor`).
3. **Deterministic first.** A new check goes into `run_checks`/`dom_checks.js` before
   anyone reaches for a model. A finding needs a selector or other evidence.
4. **Offline tests.** Real headless Chromium against local servers; ollama is always
   `httpx.MockTransport`. Never the network, never a real model.
5. **The demo site is the spec.** A new check gets a deliberate bug in `examples/site/`,
   a row in its README, and an assertion in `tests/test_demo_site.py`, including a
   negative one on the clean parts.
6. Every behaviour change gets a test, plus README and CHANGELOG entries.
7. Never add `# noqa`, `# nosec` or `# type: ignore` to the package, skip a test, or
   loosen lint/type/coverage config. Fix the code.

## Done means

`poetry run pytest` (coverage >= 85%, browser tests not skipped), `ruff check`,
`ruff format --check`, `mypy` (strict), `bandit`, `pip-audit` all green;
`ui-review examples/site` exits 1 with every bug listed; docs and CHANGELOG updated.

## Git

Small commits, imperative subjects, never force-push `main`, never commit secrets or
review output (`ui-review-report/`, screenshots of private pages).
