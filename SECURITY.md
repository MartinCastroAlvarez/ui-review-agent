# Security

ui-review-agent points a real browser at websites, including ones you don't control,
and can send screenshots and page text to a local model. Other people install it and run
it in CI. Design goal: reviewing a site never changes it, a hostile page can't make the
agent act outside its sandbox or take over the model, and nothing leaves the machine
except the GET requests the review needs.

## Reporting a vulnerability

Open a private security advisory at
<https://github.com/MartinCastroAlvarez/ui-review-agent/security/advisories/new>.
Do **not** open a public issue for a suspected vulnerability. We acknowledge within 5
business days and aim to ship a fix within 30 days. You are credited in the advisory
unless you ask otherwise.

Supported versions: the latest minor release.

## 1. Threat model

**Assets.** The site under review and its data (a review must not create orders, posts
or sign-ups), the reviewer's machine and network position, screenshots and page text
(which may be private staging content), and the integrity of the CI verdict.

**Actors.**

| Actor | Controls | Must not be able to |
| --- | --- | --- |
| The page under review (or anything it embeds) | HTML, scripts, redirects, links, text and pixels | make the browser write (POST/PUT/DELETE, form submits, beacons, WebSockets), reach other hosts, steer the LLM, inject HTML into the report |
| The local model | its answers | fail a build on its own, inject HTML into the report, call tools outside the sandbox |
| Whoever controls `OLLAMA_HOST` / `UI_REVIEW_CDP_URL` | where screenshots go / which browser runs | receive data without an explicit opt-in |

**Threats and controls.**

| Threat | Control |
| --- | --- |
| The review mutates the site | Every request goes through a Playwright route: anything but `GET` is aborted and recorded (`blocked-request`). Forms: an init script cancels `submit` events in the capture phase and turns `form.submit()`/`requestSubmit()` into no-ops; the `click` tool refuses submit buttons and image inputs. WebSockets are routed to a mock and never connected. Service workers are blocked so they can't bypass routing. Link checks use `GET` with no redirects. Tests run a server that records every method and assert only `GET` arrives, after a page tries `fetch` POST/DELETE, `sendBeacon`, `form.submit()`, Enter in a field and a WebSocket. |
| Crawling or exfiltrating to other hosts | Same-origin by default (scheme, host and port). Extra hosts only via `--allow-host` (exact or `*.example.com`). Off-scope requests are aborted, including redirects and sub-resources, so a page can't make the browser send its cookies or content elsewhere. `goto` and `click` refuse off-scope URLs; `file:`, `javascript:` and credentialed URLs are never navigated. |
| Prompt injection from page content | Page text, titles and tool results reach the agent model only inside `<untrusted source="page">…</untrusted>`, HTML-escaped so a page can't close the tag. The system prompt says that content is data, never instructions. The vision rubric says the same about text inside screenshots and asks the model to report injection attempts as findings. Tools are the only actions available, and each enforces the sandbox itself, so a persuaded model still can't write or leave the scope. Escaping reduces injection; it can't remove it. |
| A model fails or passes the build | Vision answers are parsed against a JSON schema, clipped, at most five per image, and capped at `minor`: the CI gate (`--fail-on major` by default) rests on deterministic checks. Exploration findings come from the same deterministic tools. |
| XSS in the report | Every string from a page or a model is escaped with `html.escape`. The report has no script and ships a CSP of `default-src 'none'; img-src 'self' data: file:; style-src 'unsafe-inline'`. JUnit text is XML-escaped and stripped of invalid control characters. |
| Data leaving the machine | `OLLAMA_HOST` must be loopback unless `UI_REVIEW_ALLOW_REMOTE_OLLAMA=1`; credentials, queries and fragments are refused; the HTTP client never follows redirects and ignores proxy variables. `UI_REVIEW_CDP_URL` must be loopback. There is no telemetry and no hosted API. |
| Local static server | Binds `127.0.0.1` on a random port, `GET`/`HEAD` only, no directory listings, no dotfiles, and resolves every path (including symlinks) and refuses anything outside the root. |
| Resource exhaustion | Page, depth, step, link-check and vision-image budgets; navigation timeouts; screenshots clipped at 8000 px; DOM scans bounded (600 text nodes, 20 overlaps, 30 tap targets). Dialogs are dismissed and pop-ups closed so a page can't stall the run. |
| Credentials | None are stored or needed. Each run starts from fresh browser contexts with no cookies, and nothing is persisted between runs. |
| Code execution | No `eval`, `exec`, `pickle`, `yaml` or `subprocess` in the package, `shell=True` nowhere; bandit runs in CI. |

## 2. Running it safely

- Review staging or preview builds. Screenshots and `report.json` contain whatever the
  pages show; treat the report directory like the site's data and don't publish it for
  private sites (the `.gitignore` excludes `ui-review-report/`).
- Don't add logged-in sessions: the tool has no login support on purpose. A page that
  needs auth should be reviewed from a build with fixture data.
- Keep ollama on loopback. Model weights come from the ollama registry when you
  `ollama pull`; nothing else is downloaded at run time except Playwright's browser at
  install time.

## 3. Dependencies

CI runs `pip-audit` on every push, and Dependabot watches pip and GitHub Actions.
axe-core is vendored unmodified (MPL-2.0, see `ui_review/vendor/`).
