# Demo site with deliberate bugs

A three-page static site (`index.html`, `pricing.html`, `about.html`) for a fictional
company. Every bug below is intentional; `tests/test_demo_site.py` asserts that
`ui-review examples/site` finds each one, on the right viewports, and nothing else on the
clean parts.

| Page | Bug | Check | Severity |
| --- | --- | --- | --- |
| `/` | `img/hero.png` was never committed | `broken-image` | major |
| `/` | nav link to `blog/`, which doesn't exist | `broken-link` | major |
| `/` | `app.js` reads `#promo-banner`, which is missing | `page-error` | major |
| `/` | `app.js` fetches `api/stats.json` (404) | `failed-request` | major |
| `/` | informative badge image without `alt` | `a11y` (image-alt) | critical |
| `/pricing.html` | fixed 3 × 240 px plan grid with no scroll container | `overflow` (mobile only) | major |
| `/pricing.html` | "NEW PLANS" ribbon positioned over the heading | `overlap` | major |
| `/pricing.html` | 92 px button with `overflow: hidden` | `clipped-text` | minor |
| `/pricing.html` | 18 × 18 px icon buttons | `tap-target` (touch viewports) | major |
| `/pricing.html` | red ribbon on navy fails contrast | `a11y` (color-contrast, dark only) | major |
| `/about.html` | quote box keeps a light background in dark mode | `a11y` (color-contrast, dark only) | major |

Things the sandbox must stop (reported as `info`, and the test server asserts they never
arrive): `app.js` calls `navigator.sendBeacon` (a POST) and `form.requestSubmit()`, and
`index.html` loads a tracking pixel from another host.
