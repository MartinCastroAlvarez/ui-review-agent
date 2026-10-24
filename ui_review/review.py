"""Run a whole review: serve, sandbox the browser, sweep, explore, critique."""

from __future__ import annotations

import time
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass
from dataclasses import field
from dataclasses import replace

import httpx

from ui_review import __version__
from ui_review.agent import Agent
from ui_review.agent import OllamaPolicy
from ui_review.agent import ScriptedPolicy
from ui_review.agent import TraceStep
from ui_review.browser import Blocked
from ui_review.browser import BrowserSession
from ui_review.config import Config
from ui_review.config import resolve_target
from ui_review.findings import Findings
from ui_review.ollama import OllamaClient
from ui_review.scope import Scope
from ui_review.server import StaticServer
from ui_review.tools import Environment
from ui_review.tools import PageRecord
from ui_review.vision import VisionCritic
from ui_review.vision import availability


class ReviewError(RuntimeError):
    """The review could not run (browser missing, vision required but unavailable)."""


@dataclass
class ReviewResult:
    config: Config
    start_url: str
    findings: Findings
    pages: list[PageRecord]
    trace: list[TraceStep]
    blocked: list[Blocked]
    browser: str
    notes: list[str] = field(default_factory=list)
    vision_model: str | None = None
    agent_model: str | None = None
    started_at: float = 0.0
    duration_s: float = 0.0
    version: str = __version__

    @property
    def failed(self) -> bool:
        return bool(self.findings.at_least(self.config.fail_on))


def review(
    config: Config,
    *,
    ollama_transport: httpx.BaseTransport | None = None,
    log: Callable[[str], None] | None = None,
) -> ReviewResult:
    """Review ``config.target`` and return the result. Reports are written separately."""
    say = log or (lambda _msg: None)
    started = time.time()
    t0 = time.monotonic()
    start_url, root = resolve_target(config.target)
    notes: list[str] = []
    with ExitStack() as stack:
        if root is not None:
            server = stack.enter_context(StaticServer(root))
            start_url = server.url + start_url
            say(f"serving {root} at {server.url}")
        scope = Scope(start_url, config.allow_hosts)
        config.out_dir.mkdir(parents=True, exist_ok=True)

        client = OllamaClient(config.ollama_host, transport=ollama_transport)
        stack.callback(client.close)
        vision: VisionCritic | None = None
        if config.vision != "off":
            ok, why = availability(client, config.vision_model)
            if ok:
                vision = VisionCritic(
                    client, config.vision_model, max_images=config.max_vision_images
                )
                say(f"vision pass on: {config.vision_model}")
            elif config.vision == "on":
                raise ReviewError(f"vision pass required but unavailable: {why}")
            else:
                notes.append(f"Vision pass skipped: {why}.")
        explore = 0
        if config.explore_steps > 0:
            ok, why = availability(client, config.agent_model)
            if ok:
                explore = config.explore_steps
            else:
                notes.append(f"Exploration skipped: {why}.")

        try:
            session = stack.enter_context(BrowserSession(config, scope))
        except Exception as exc:
            raise ReviewError(
                "could not start Chromium. Run `playwright install --with-deps --only-shell "
                f"chromium`, or set UI_REVIEW_CHROMIUM_PATH / UI_REVIEW_CDP_URL. ({exc})"
            ) from exc
        env = Environment(config, scope, session, vision=vision)
        trace: list[TraceStep] = []
        say(f"sweeping {start_url} ({len(config.views())} views, up to {config.max_pages} pages)")
        steps = Agent(
            env, ScriptedPolicy(env), max_steps=config.max_steps, phase="sweep", trace=trace
        ).run()
        if steps >= config.max_steps:
            notes.append(f"The sweep stopped at the step budget ({config.max_steps}).")
        if explore:
            say(f"exploring with {config.agent_model} for up to {explore} steps")
            policy = OllamaPolicy(client, config.agent_model, env)
            Agent(env, policy, max_steps=explore, phase="explore", trace=trace).run()
        _attach_screenshots(env)
        if env.frontier:
            notes.append(
                f"{len(env.frontier)} more same-site pages were found but not reviewed "
                f"(--max-pages {config.max_pages})."
            )
        if vision is not None and vision.errors:
            notes.append(f"Vision model errors: {len(vision.errors)} (first: {vision.errors[0]}).")
        return ReviewResult(
            config=replace(config),
            start_url=start_url,
            findings=env.findings,
            pages=list(env.pages.values()),
            trace=trace,
            blocked=list(session.blocked),
            browser=session.version,
            notes=notes,
            vision_model=config.vision_model if vision else None,
            agent_model=config.agent_model if explore else None,
            started_at=started,
            duration_s=round(time.monotonic() - t0, 2),
        )


def _attach_screenshots(env: Environment) -> None:
    """Checks run before the screenshot: give each finding its page's shot in its view."""
    for finding in env.findings:
        record = env.pages.get(finding.url)
        if finding.screenshot or record is None or not record.shots:
            continue
        for view in finding.views:
            if view in record.shots:
                finding.screenshot = record.shots[view]
                break
        else:
            finding.screenshot = next(iter(record.shots.values()))
