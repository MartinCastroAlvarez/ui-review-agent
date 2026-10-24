"""The agent's tools and the environment they act on.

Tools: ``goto``, ``click``, ``scroll``, ``read_dom``, ``run_checks``, ``axe_audit``,
``screenshot``, ``compare_baseline``, ``critique`` and ``finish``. Every tool returns a
:class:`ToolResult` whose ``summary`` is what a policy observes. Page-derived text in a
summary is wrapped in ``<untrusted>`` so an LLM policy can tell data from instructions.
Tools never raise: browser errors become ``ok=False`` results.
"""

from __future__ import annotations

import html
import json
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import FloatRect
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from ui_review import visual
from ui_review.browser import BrowserSession
from ui_review.browser import Events
from ui_review.browser import View
from ui_review.browser import asset
from ui_review.config import VIEWPORTS
from ui_review.config import Config
from ui_review.config import Scheme
from ui_review.findings import Finding
from ui_review.findings import Findings
from ui_review.findings import Severity
from ui_review.scope import Scope
from ui_review.scope import normalize
from ui_review.scope import slug
from ui_review.vision import VisionCritic

AXE_IMPACT = {
    "critical": Severity.CRITICAL,
    "serious": Severity.MAJOR,
    "moderate": Severity.MINOR,
    "minor": Severity.INFO,
}
AXE_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa", "best-practice"]


def untrusted(text: str, limit: int = 2000) -> str:
    """Delimit page-derived text for a model: escaped, clipped, clearly labelled."""
    body = html.escape(text[:limit], quote=False)
    return f'<untrusted source="page">{body}</untrusted>'


@dataclass
class ToolResult:
    ok: bool
    summary: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class PageRecord:
    url: str
    depth: int
    title: str = ""
    status: int | None = None
    shots: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]

    def schema(self) -> dict[str, Any]:
        """The tool as an ollama/OpenAI-style function definition."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def _obj(props: dict[str, Any] | None = None, required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": props or {}, "required": required or []}


TOOL_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        "goto",
        "Open a same-site page in a viewport (mobile|tablet|desktop) and color scheme "
        "(light|dark). Collects console errors and failed requests.",
        _obj(
            {
                "url": {"type": "string", "description": "absolute or relative URL"},
                "viewport": {"type": "string", "enum": list(VIEWPORTS)},
                "scheme": {"type": "string", "enum": ["light", "dark"]},
            },
            ["url"],
        ),
    ),
    ToolSpec(
        "click",
        "Click one element by CSS selector (menus, tabs, toggles). Form submission is refused.",
        _obj({"selector": {"type": "string"}}, ["selector"]),
    ),
    ToolSpec(
        "scroll",
        "Scroll the page: 'top', 'bottom' (step by step, loads lazy content) or a pixel offset.",
        _obj({"to": {"type": "string"}}, ["to"]),
    ),
    ToolSpec(
        "read_dom",
        "Summarise the current page: title, headings, links, buttons, visible text.",
        _obj(),
    ),
    ToolSpec(
        "run_checks",
        "Deterministic checks on the current page: horizontal overflow, broken images and "
        "links, overlapping and clipped text, tap targets on touch viewports.",
        _obj(),
    ),
    ToolSpec("axe_audit", "Run axe-core accessibility rules (WCAG 2.2 AA, contrast).", _obj()),
    ToolSpec("screenshot", "Take a full-page screenshot of the current view.", _obj()),
    ToolSpec(
        "compare_baseline",
        "Compare the latest screenshot with the stored baseline (pixel diff + perceptual hash).",
        _obj(),
    ),
    ToolSpec("critique", "Ask the local vision model to critique the latest screenshot.", _obj()),
    ToolSpec(
        "finish",
        "Stop: the review is complete.",
        _obj({"reason": {"type": "string"}}, ["reason"]),
    ),
)


class Environment:
    """Browser views, crawl frontier, findings and artifacts for one review run."""

    def __init__(
        self,
        config: Config,
        scope: Scope,
        session: BrowserSession,
        *,
        vision: VisionCritic | None = None,
    ) -> None:
        self.config = config
        self.scope = scope
        self.session = session
        self.vision = vision
        self.findings = Findings()
        self.pages: dict[str, PageRecord] = {}
        self.frontier: deque[tuple[str, int]] = deque()
        self._queued: set[str] = set()
        self.link_status: dict[str, str] = {}
        self.current: View | None = None
        self.current_depth = 0
        self.out_dir = config.out_dir
        self.shots_dir = self.out_dir / "screenshots"
        self.diffs_dir = self.out_dir / "diffs"
        self.shots_dir.mkdir(parents=True, exist_ok=True)
        self.diffs_dir.mkdir(parents=True, exist_ok=True)
        for stale in [*self.shots_dir.glob("*.png"), *self.diffs_dir.glob("*.png")]:
            stale.unlink()  # images from a previous run in the same report directory
        self._dom_js = asset("js/dom_checks.js")
        self._read_js = asset("js/read_dom.js")
        self._axe_js: str | None = None
        self._tools: dict[str, Callable[..., ToolResult]] = {
            "goto": self.goto,
            "click": self.click,
            "scroll": self.scroll,
            "read_dom": self.read_dom,
            "run_checks": self.run_checks,
            "axe_audit": self.axe_audit,
            "screenshot": self.screenshot,
            "compare_baseline": self.compare_baseline,
            "critique": self.critique,
        }
        self.enqueue(scope.start_url, 0)

    # -- crawl bookkeeping -------------------------------------------------------------
    def enqueue(self, url: str, depth: int) -> bool:
        norm = normalize(url)
        if norm is None or norm in self._queued or not self.scope.crawlable(norm):
            return False
        if depth > self.config.max_depth:
            return False
        self._queued.add(norm)
        self.frontier.append((norm, depth))
        return True

    def claim_next(self) -> PageRecord | None:
        """Pop the next page to review, within the page budget."""
        while self.frontier and len(self.pages) < self.config.max_pages:
            url, depth = self.frontier.popleft()
            status = self.link_status.get(url, "200")
            if status.isdigit() and int(status) >= 400:
                continue  # already reported as a broken link
            if url not in self.pages:
                record = PageRecord(url, depth)
                self.pages[url] = record
                return record
        return None

    # -- dispatch ----------------------------------------------------------------------
    def call(self, name: str, args: dict[str, Any]) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(False, f"unknown tool {name!r}")
        try:
            return tool(**args)
        except TypeError as exc:
            return ToolResult(False, f"bad arguments for {name}: {exc}")
        except PlaywrightTimeout as exc:
            return ToolResult(False, f"{name} timed out: {_first_line(exc)}")
        except PlaywrightError as exc:
            return ToolResult(False, f"{name} failed: {_first_line(exc)}")

    def _view(self) -> View:
        if self.current is None:
            raise PlaywrightError("no page is open; call goto first")
        return self.current

    def _page_url(self, view: View) -> str:
        return view.url or self.scope.start_url

    def _rel(self, path: Path) -> str:
        return path.relative_to(self.out_dir).as_posix()

    def _short(self, url: str) -> str:
        return _path(url, keep_host=not self.scope.is_same_origin(url))

    def _add(self, view: View, check: str, severity: Severity, title: str, **kw: Any) -> None:
        self.findings.add(
            Finding(
                check=check,
                severity=severity,
                title=title,
                url=self._page_url(view),
                views=[view.name],
                screenshot=view.screenshot,
                **kw,
            )
        )

    def flush(self, view: View) -> Events:
        """Turn the events collected in ``view`` into findings."""
        events = view.drain()
        for message in events.page_errors:
            self._add(
                view,
                "page-error",
                Severity.MAJOR,
                f"Uncaught exception: {_first_line(message)}",
                detail=message,
            )
        for message in events.console_errors:
            self._add(
                view,
                "console-error",
                Severity.MINOR,
                f"console.error: {_first_line(message)}",
                detail=message,
            )
        for url, reason in events.failed:
            self._add(
                view,
                "failed-request",
                Severity.MAJOR,
                f"Request failed: {self._short(url)}",
                detail=f"{url} -> {reason}",
            )
        for blocked in events.blocked:
            self._add(
                view,
                "blocked-request",
                Severity.INFO,
                f"Blocked {blocked.method} {self._short(blocked.url)}",
                detail=f"{blocked.url}: {blocked.reason}",
            )
        return events

    # -- tools -------------------------------------------------------------------------
    def goto(self, url: str, viewport: str | None = None, scheme: str | None = None) -> ToolResult:
        base = self.current.url if self.current and self.current.url else self.scope.start_url
        target = normalize(url, base)
        if target is None or not self.scope.crawlable(target):
            return ToolResult(False, f"refused: {url!r} is outside the review scope")
        if target not in self.pages:
            if len(self.pages) >= self.config.max_pages:
                return ToolResult(
                    False, f"refused: the page budget ({self.config.max_pages}) is spent"
                )
            self.pages[target] = PageRecord(target, self.current_depth + 1)
        record = self.pages[target]
        vp_name = viewport or (
            self.current.viewport.name if self.current else self.config.viewports[0]
        )
        sc_name = scheme or (self.current.scheme if self.current else self.config.schemes[0])
        if vp_name not in VIEWPORTS or sc_name not in ("light", "dark"):
            return ToolResult(False, f"unknown viewport/scheme {vp_name}/{sc_name}")
        sc: Scheme = "dark" if sc_name == "dark" else "light"
        view = self.session.view(VIEWPORTS[vp_name], sc)
        view.drain()
        view.url, view.status, view.screenshot = target, None, None
        self.current = view
        self.current_depth = record.depth
        try:
            response = view.page.goto(target, wait_until="load")
        except (PlaywrightTimeout, PlaywrightError) as exc:
            self._add(view, "load", Severity.CRITICAL, "Page did not load", detail=_first_line(exc))
            self.flush(view)
            return ToolResult(False, f"{target} did not load at {view.name}: {_first_line(exc)}")
        self._settle(view)
        view.status = response.status if response is not None else None
        record.status = view.status
        if view.status is not None and view.status >= 400:
            self._add(
                view,
                "http-status",
                Severity.CRITICAL,
                f"HTTP {view.status}",
                detail=f"{target} answered {view.status}",
            )
        events = self.flush(view)
        title = view.page.title()
        record.title = record.title or title
        return ToolResult(
            True,
            f"Opened {target} at {view.name}: HTTP {view.status}; title {untrusted(title, 120)}; "
            f"{len(events.page_errors)} exceptions, {len(events.console_errors)} console errors, "
            f"{len(events.failed)} failed and {len(events.blocked)} blocked requests.",
            {"url": target, "view": view.name, "status": view.status},
        )

    def _settle(self, view: View) -> None:
        view.settle()

    def click(self, selector: str) -> ToolResult:
        view = self._view()
        page = view.page
        handle = page.query_selector(selector)
        if handle is None:
            return ToolResult(False, f"no element matches {selector!r}")
        kind: dict[str, Any] = handle.evaluate(
            """(el) => {
                const form = el.closest('form');
                const tag = el.tagName.toLowerCase();
                const type = (el.getAttribute('type') || '').toLowerCase();
                const submits = (tag === 'button' && form && (type === '' || type === 'submit'))
                    || (tag === 'input' && (type === 'submit' || type === 'image'));
                return {submits: !!submits, href: tag === 'a' ? el.href : null};
            }"""
        )
        if kind.get("submits"):
            return ToolResult(False, "refused: this element submits a form")
        href = kind.get("href")
        leaves = bool(href) and normalize(str(href)) is not None
        if leaves and not self.scope.crawlable(str(href)):
            return ToolResult(False, "refused: the link leaves the review scope")
        before = page.url
        handle.click(timeout=3000)
        self._settle(view)
        events = self.flush(view)
        after = page.url
        if after != before:
            norm = normalize(after)
            if norm is not None and self.scope.crawlable(norm):
                view.url = norm
                self.pages.setdefault(norm, PageRecord(norm, self.current_depth + 1))
        view.screenshot = None
        return ToolResult(
            True,
            f"Clicked {selector!r}; url is {after}; {len(events.page_errors)} exceptions, "
            f"{len(events.console_errors)} console errors, {len(events.blocked)} blocked requests.",
        )

    def scroll(self, to: str = "bottom") -> ToolResult:
        view = self._view()
        page = view.page
        if to == "top":
            page.evaluate("window.scrollTo(0, 0)")
        elif to == "bottom":
            for _ in range(30):
                at_end = page.evaluate(
                    "() => { const before = scrollY; scrollBy(0, innerHeight);"
                    " return scrollY === before; }"
                )
                page.wait_for_timeout(50)
                if at_end:
                    break
        else:
            try:
                offset = int(to)
            except ValueError:
                return ToolResult(
                    False, f"scroll: 'to' must be top, bottom or a number, not {to!r}"
                )
            page.evaluate("(y) => window.scrollTo(0, y)", offset)
        self._settle(view)
        self.flush(view)
        y = page.evaluate("scrollY")
        return ToolResult(True, f"Scrolled to {to}; scrollY is now {y}.")

    def read_dom(self) -> ToolResult:
        view = self._view()
        info: dict[str, Any] = view.page.evaluate(self._read_js, 1500)
        record = self.pages.get(self._page_url(view))
        if record is not None and info.get("title"):
            record.title = record.title or str(info["title"])
        queued = [
            str(link["href"])
            for link in info.get("links", [])
            if self.enqueue(str(link["href"]), self.current_depth + 1)
        ]
        unvisited = [u for u, _ in self.frontier][:15]
        lines = [
            f"Page {info.get('url')} ({info.get('width')}x{info.get('height')} px), "
            f"lang={info.get('lang')!r}, {len(info.get('links', []))} links, "
            f"{info.get('forms', 0)} forms.",
            f"Title: {untrusted(str(info.get('title', '')), 160)}",
            "Headings: " + untrusted("; ".join(info.get("headings", [])), 800),
            "Buttons: " + untrusted("; ".join(info.get("buttons", [])), 400),
            f"Newly queued pages: {queued[:10]}",
            f"Unvisited pages: {unvisited}",
            "Visible text: " + untrusted(str(info.get("text", "")), 1500),
        ]
        return ToolResult(True, "\n".join(lines), {"queued": queued, "title": info.get("title")})

    def run_checks(self) -> ToolResult:
        view = self._view()
        res: dict[str, Any] = view.page.evaluate(
            self._dom_js,
            {"mobile": view.viewport.mobile, "minTap": self.config.min_tap_target},
        )
        before = len(self.findings)
        overflow = res.get("overflow")
        if overflow:
            offenders = overflow.get("offenders", [])
            self._add(
                view,
                "overflow",
                Severity.MAJOR,
                "Page scrolls horizontally",
                detail=(
                    f"Document is {overflow['scrollWidth']} px wide in a {overflow['viewport']} px "
                    "viewport. Widest offenders: "
                    + ", ".join(f"{o['selector']} (right edge {o['right']} px)" for o in offenders)
                ),
                selectors=[o["selector"] for o in offenders],
                evidence={"scrollWidth": overflow["scrollWidth"], "viewport": overflow["viewport"]},
            )
        for img in res.get("images", []):
            if not self.scope.allows(img["src"]):
                continue  # blocked by the sandbox, reported as a blocked request
            self.findings.discard("failed-request", self._page_url(view), img["src"])
            self._add(
                view,
                "broken-image",
                Severity.MAJOR,
                f"Broken image: {self._short(img['src'])}",
                detail=f"{img['selector']} has no pixels after loading {img['src']}",
                selectors=[img["selector"]],
            )
        for item in res.get("overlaps", []):
            self._add(
                view,
                "overlap",
                Severity.MAJOR,
                "Text overlaps other text",
                detail=f"{item['a']} ({item['textA']!r}) overlaps {item['b']} ({item['textB']!r})",
                selectors=[item["a"], item["b"]],
            )
        for item in res.get("clipped", []):
            self._add(
                view,
                "clipped-text",
                Severity.MINOR,
                "Text is cut off by its container",
                detail=(
                    f"{item['selector']} ({item['text']!r}) needs {item['scroll'][0]}x"
                    f"{item['scroll'][1]} px but shows {item['box'][0]}x{item['box'][1]} px "
                    "with overflow hidden"
                ),
                selectors=[item["selector"]],
            )
        for item in res.get("taps", []):
            self._add(
                view,
                "tap-target",
                Severity.MAJOR,
                f"Tap target smaller than {self.config.min_tap_target} px",
                detail=(
                    f"{item['selector']} ({item['text']!r}) is {item['width']}x{item['height']} px "
                    f"on a touch viewport (WCAG 2.5.8 asks for {self.config.min_tap_target} px)"
                ),
                selectors=[item["selector"]],
                evidence={"width": item["width"], "height": item["height"]},
            )
        broken = self._check_links(view, res.get("links", [])) if self.config.check_links else 0
        self.flush(view)
        new = len(self.findings) - before
        return ToolResult(
            True,
            f"Checks on {view.name}: overflow={'yes' if overflow else 'no'}, "
            f"{len(res.get('images', []))} broken images, {len(res.get('overlaps', []))} overlaps, "
            f"{len(res.get('clipped', []))} clipped, {len(res.get('taps', []))} small tap targets, "
            f"{broken} broken links; {new} new findings.",
            res | {"broken_links": broken},
        )

    def _check_links(self, view: View, links: list[str]) -> int:
        broken = 0
        for href in links:
            url = normalize(href, self._page_url(view))
            if url is None or not self.scope.allows(url):
                continue
            if url not in self.link_status:
                if len(self.link_status) >= self.config.max_links:
                    continue
                self.link_status[url] = self._fetch_status(view, url)
            status = self.link_status[url]
            if not status.isdigit() or int(status) >= 400:
                broken += 1
                self._add(
                    view,
                    "broken-link",
                    Severity.MAJOR,
                    f"Broken link: {self._short(url)}",
                    detail=f"GET {url} -> {status}",
                    key=url,
                )
        return broken

    def _fetch_status(self, view: View, url: str) -> str:
        try:
            response = view.context.request.get(
                url, max_redirects=0, timeout=self.config.nav_timeout_ms
            )
        except PlaywrightError as exc:
            return _first_line(exc)[:120]
        status = response.status
        response.dispose()
        return str(status)

    def axe_audit(self) -> ToolResult:
        if not self.config.axe:
            return ToolResult(True, "axe audit is disabled.")
        view = self._view()
        page = view.page
        if not page.evaluate("typeof window.axe === 'object'"):
            if self._axe_js is None:
                source = asset("vendor/axe.min.js")
                # Shadow AMD/CommonJS globals so axe always registers on window.
                self._axe_js = (
                    "(() => { const define = undefined, module = undefined, exports = undefined;\n"
                    + source
                    + "\n})()"
                )
            page.evaluate(self._axe_js)
        violations: list[dict[str, Any]] = page.evaluate(
            """async (tags) => {
                const r = await axe.run(document, {runOnly: {type: 'tag', values: tags},
                                                   resultTypes: ['violations']});
                return r.violations.map(v => ({
                    id: v.id, impact: v.impact, help: v.help, description: v.description,
                    helpUrl: v.helpUrl, tags: v.tags,
                    nodes: v.nodes.slice(0, 8).map(n => ({
                        target: n.target.map(t => Array.isArray(t) ? t.join(' ') : t).join(' >>> '),
                        summary: (n.failureSummary || '').slice(0, 300)})),
                    count: v.nodes.length}));
            }""",
            AXE_TAGS,
        )
        for v in violations:
            severity = AXE_IMPACT.get(str(v.get("impact")), Severity.MINOR)
            nodes = v.get("nodes", [])
            detail = f"{v['description']} ({v['count']} elements). {v['helpUrl']}"
            if nodes and nodes[0].get("summary"):
                detail += "\n" + nodes[0]["summary"]
            self._add(
                view,
                "a11y",
                severity,
                f"{v['id']}: {v['help']}",
                detail=detail,
                selectors=[n["target"] for n in nodes],
                evidence={"rule": v["id"], "impact": v.get("impact"), "count": v["count"]},
                key=v["id"],
            )
        self.flush(view)
        ids = ", ".join(f"{v['id']}({v['count']})" for v in violations) or "none"
        return ToolResult(True, f"axe on {view.name}: {len(violations)} violations: {ids}.")

    def screenshot(self) -> ToolResult:
        view = self._view()
        url = self._page_url(view)
        name = f"{slug(url, self.scope.start_url)}__{view.name}"
        path = self.shots_dir / f"{name}.png"
        n = 1
        while path.exists():
            n += 1
            path = self.shots_dir / f"{name}-{n}.png"
        page = view.page
        height = int(page.evaluate("document.documentElement.scrollHeight"))
        clip: FloatRect | None = None
        if height > self.config.max_screenshot_height:
            clip = FloatRect(
                x=0, y=0, width=view.viewport.width, height=self.config.max_screenshot_height
            )
        page.screenshot(
            path=str(path), full_page=True, animations="disabled", caret="hide", clip=clip
        )
        view.screenshot = self._rel(path)
        record = self.pages.get(url)
        if record is not None:
            record.shots.setdefault(view.name, view.screenshot)
        return ToolResult(True, f"Saved {view.screenshot} ({height} px tall).", {"path": str(path)})

    def compare_baseline(self) -> ToolResult:
        view = self._view()
        if self.config.baseline_dir is None:
            return ToolResult(True, "No baseline directory configured; skipped.")
        if view.screenshot is None:
            return ToolResult(False, "take a screenshot first")
        current = self.out_dir / view.screenshot
        name = f"{slug(self._page_url(view), self.scope.start_url)}__{view.name}.png"
        baseline = self.config.baseline_dir / name
        if self.config.update_baseline or not baseline.exists():
            visual.update_baseline(current, baseline)
            return ToolResult(True, f"Baseline written: {baseline.name}.")
        diff_path = self.diffs_dir / name
        diff = visual.compare(
            baseline, current, tolerance=self.config.pixel_tolerance, diff_path=diff_path
        )
        summary = (
            f"{diff.ratio:.3%} of pixels changed, perceptual hash distance "
            f"{diff.hash_distance}/64, size {diff.baseline_size} -> {diff.current_size}"
        )
        if diff.ratio <= self.config.max_diff_ratio and not diff.size_changed:
            return ToolResult(True, f"Matches the baseline ({summary}).")
        major = diff.size_changed or diff.hash_distance > self.config.hash_threshold
        self._add(
            view,
            "visual-regression",
            Severity.MAJOR if major else Severity.MINOR,
            "Screenshot differs from the baseline",
            detail=summary + (f"; changed region {diff.bbox}" if diff.bbox else ""),
            diff=self._rel(diff_path) if diff_path.exists() else None,
            evidence={
                "ratio": round(diff.ratio, 6),
                "changed_pixels": diff.changed_pixels,
                "hash_distance": diff.hash_distance,
                "size_changed": diff.size_changed,
            },
            key=view.name,
        )
        return ToolResult(True, f"Differs from the baseline: {summary}.")

    def critique(self) -> ToolResult:
        view = self._view()
        if self.vision is None:
            return ToolResult(True, "Vision pass is off; skipped.")
        if view.screenshot is None:
            return ToolResult(False, "take a screenshot first")
        record = self.pages.get(self._page_url(view))
        items = self.vision.critique(
            self.out_dir / view.screenshot,
            url=self._page_url(view),
            view=view.name,
            title=record.title if record else "",
        )
        for item in items:
            self._add(
                view,
                "vision",
                Severity.MINOR if item.severity != Severity.INFO else Severity.INFO,
                item.title,
                detail=item.detail,
                source=f"vision:{self.vision.model}",
                evidence={"model_severity": item.severity.value},
                key=view.name,
            )
        return ToolResult(
            True,
            f"Vision model reported {len(items)} issues: "
            + untrusted("; ".join(i.title for i in items), 600),
        )


def _first_line(value: object) -> str:
    text = str(value).strip()
    return text.splitlines()[0][:300] if text else value.__class__.__name__


def _path(url: str, *, keep_host: bool = False) -> str:
    parts = urlsplit(url)
    if url.startswith("data:"):
        return url[:40]
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query[:40]
    return f"{parts.netloc}{path}" if keep_host else path


def dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, default=str)
