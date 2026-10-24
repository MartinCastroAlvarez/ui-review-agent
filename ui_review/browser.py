"""Headless Chromium through Playwright, sandboxed for review.

Every browser context routes all requests through :meth:`BrowserSession._guard`:
non-GET requests and requests to hosts outside the :class:`~ui_review.scope.Scope` are
aborted and recorded. WebSockets are never connected, service workers are blocked,
downloads are refused, dialogs are dismissed and pop-ups closed. An init script cancels
form submission. Nothing is persisted: each run starts from empty contexts.
"""

from __future__ import annotations

import time
from contextlib import suppress
from dataclasses import dataclass
from dataclasses import field
from importlib import resources
from types import TracebackType

from playwright.sync_api import Browser
from playwright.sync_api import BrowserContext
from playwright.sync_api import ConsoleMessage
from playwright.sync_api import Dialog
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page
from playwright.sync_api import Playwright
from playwright.sync_api import Request
from playwright.sync_api import Response
from playwright.sync_api import Route
from playwright.sync_api import WebSocketRoute
from playwright.sync_api import sync_playwright

from ui_review.config import Config
from ui_review.config import Scheme
from ui_review.config import Viewport
from ui_review.scope import Scope

_BLOCKED_ERROR = "blockedbyclient"


def asset(name: str) -> str:
    return resources.files("ui_review").joinpath(name).read_text(encoding="utf-8")


@dataclass(frozen=True)
class Blocked:
    method: str
    url: str
    reason: str


@dataclass
class Events:
    """What happened in one view since the last :meth:`View.drain`."""

    console_errors: list[str] = field(default_factory=list)
    page_errors: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)  # (url, reason)
    blocked: list[Blocked] = field(default_factory=list)


@dataclass
class View:
    viewport: Viewport
    scheme: Scheme
    context: BrowserContext
    page: Page
    events: Events = field(default_factory=Events)
    url: str | None = None
    status: int | None = None
    screenshot: str | None = None
    last_activity: float = 0.0

    def settle(self, timeout_ms: int = 3000, quiet_ms: int = 250) -> None:
        """Wait until the network has been quiet for ``quiet_ms`` (at most ``timeout_ms``).

        Quiet means no request started, answered, finished or failed; a request whose
        body is never read doesn't hold the wait open.
        """
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            if (time.monotonic() - self.last_activity) * 1000 >= quiet_ms:
                return
            self.page.wait_for_timeout(50)

    @property
    def name(self) -> str:
        return f"{self.viewport.name}-{self.scheme}"

    def drain(self) -> Events:
        events, self.events = self.events, Events()
        return events


class BrowserSession:
    """Owns Playwright, one browser and one context per (viewport, scheme)."""

    def __init__(self, config: Config, scope: Scope) -> None:
        self.config = config
        self.scope = scope
        self.blocked: list[Blocked] = []
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._views: dict[str, View] = {}
        self._guard_js = asset("js/guard.js")

    # -- lifecycle ---------------------------------------------------------------------
    def start(self) -> BrowserSession:
        self._pw = sync_playwright().start()
        try:
            if self.config.cdp_url:
                self._browser = self._pw.chromium.connect_over_cdp(self.config.cdp_url)
            else:
                self._browser = self._pw.chromium.launch(
                    headless=True,
                    executable_path=self.config.chromium_path,
                    args=list(self.config.chromium_args),
                )
        except Exception:
            self._pw.stop()
            self._pw = None
            raise
        return self

    def close(self) -> None:
        for view in self._views.values():
            with suppress(PlaywrightError):
                view.context.close()
        self._views.clear()
        if self._browser is not None:
            with suppress(PlaywrightError):
                self._browser.close()
            self._browser = None
        if self._pw is not None:
            self._pw.stop()
            self._pw = None

    def __enter__(self) -> BrowserSession:
        return self.start()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    @property
    def version(self) -> str:
        return self._browser.version if self._browser else ""

    # -- views -------------------------------------------------------------------------
    def view(self, viewport: Viewport, scheme: Scheme) -> View:
        key = f"{viewport.name}-{scheme}"
        if key in self._views:
            return self._views[key]
        if self._browser is None:
            raise RuntimeError("BrowserSession is not started")
        context = self._browser.new_context(
            viewport={"width": viewport.width, "height": viewport.height},
            device_scale_factor=1,
            is_mobile=viewport.mobile,
            has_touch=viewport.mobile,
            color_scheme=scheme,
            reduced_motion="reduce",
            service_workers="block",
            accept_downloads=False,
            java_script_enabled=True,
            locale="en-US",
            timezone_id="UTC",
        )
        context.set_default_timeout(self.config.nav_timeout_ms)
        page = context.new_page()
        view = View(viewport=viewport, scheme=scheme, context=context, page=page)
        context.route("**/*", lambda route, request: self._guard(view, route, request))
        context.route_web_socket("**/*", lambda ws: self._block_socket(view, ws))
        context.add_init_script(script=self._guard_js)
        context.on("page", lambda p: self._close_popup(page, p))
        page.on("console", lambda msg: self._console(view, msg))
        page.on("pageerror", lambda err: view.events.page_errors.append(_short(str(err))))
        page.on("request", lambda _req: _touch(view))
        page.on("requestfinished", lambda _req: _touch(view))
        page.on("requestfailed", lambda req: self._failed(view, req))
        page.on("response", lambda resp: self._response(view, resp))
        page.on("dialog", _dismiss)
        self._views[key] = view
        return view

    # -- sandbox -----------------------------------------------------------------------
    def _guard(self, view: View, route: Route, request: Request) -> None:
        reason = None
        if request.method != "GET":
            reason = f"{request.method} requests are never sent"
        elif not self.scope.allows(request.url):
            reason = "host is outside the review scope"
        if reason is None:
            route.continue_()
            return
        blocked = Blocked(request.method, _short(request.url, 300), reason)
        self.blocked.append(blocked)
        view.events.blocked.append(blocked)
        route.abort(_BLOCKED_ERROR)

    def _block_socket(self, view: View, ws: WebSocketRoute) -> None:
        blocked = Blocked("WEBSOCKET", _short(ws.url, 300), "WebSockets are never connected")
        self.blocked.append(blocked)
        view.events.blocked.append(blocked)
        # Not calling ws.connect_to_server() is what blocks it: the page talks to a mock
        # that never answers. (Closing it from inside the handler deadlocks Playwright.)

    @staticmethod
    def _close_popup(main: Page, page: Page) -> None:
        if page is not main:
            page.close()

    def _console(self, view: View, msg: ConsoleMessage) -> None:
        if msg.type != "error":
            return
        text = msg.text
        # Network failures already surface as failed/blocked requests.
        if "Failed to load resource" in text or "ERR_BLOCKED_BY_CLIENT" in text:
            return
        view.events.console_errors.append(_short(text))

    def _failed(self, view: View, request: Request) -> None:
        _touch(view)
        failure = request.failure or "failed"
        if "ERR_BLOCKED_BY_CLIENT" in failure or "ERR_ABORTED" in failure:
            return
        if self.scope.is_same_origin(request.url):
            view.events.failed.append((_short(request.url, 300), failure))

    def _response(self, view: View, response: Response) -> None:
        _touch(view)
        main_document = (
            response.request.resource_type == "document" and response.frame == view.page.main_frame
        )
        if response.status >= 400 and self.scope.is_same_origin(response.url) and not main_document:
            view.events.failed.append((_short(response.url, 300), f"HTTP {response.status}"))


def _touch(view: View) -> None:
    view.last_activity = time.monotonic()


def _dismiss(dialog: Dialog) -> None:
    with suppress(PlaywrightError):
        dialog.dismiss()


def _short(text: str, limit: int = 500) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"
