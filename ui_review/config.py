"""Review settings: defaults, ``UI_REVIEW_*`` environment variables and validation."""

from __future__ import annotations

import ipaddress
import os
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import replace
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from ui_review.findings import Severity

Scheme = Literal["light", "dark"]
VisionMode = Literal["auto", "on", "off"]


class ConfigError(ValueError):
    """A setting is invalid."""


@dataclass(frozen=True)
class Viewport:
    name: str
    width: int
    height: int
    mobile: bool = False


VIEWPORTS: dict[str, Viewport] = {
    "mobile": Viewport("mobile", 390, 844, mobile=True),
    "tablet": Viewport("tablet", 768, 1024, mobile=True),
    "desktop": Viewport("desktop", 1280, 800),
}
SCHEMES: tuple[Scheme, ...] = ("light", "dark")


@dataclass(frozen=True)
class Config:
    """Everything a review run needs. Build it with :meth:`from_env` and ``replace``."""

    target: str = ""
    out_dir: Path = Path("ui-review-report")
    viewports: tuple[str, ...] = ("mobile", "tablet", "desktop")
    schemes: tuple[Scheme, ...] = SCHEMES
    max_pages: int = 10
    max_depth: int = 3
    max_steps: int = 600
    allow_hosts: tuple[str, ...] = ()
    # Visual regression.
    baseline_dir: Path | None = None
    update_baseline: bool = False
    pixel_tolerance: int = 16
    max_diff_ratio: float = 0.001
    hash_threshold: int = 6
    # Deterministic checks.
    axe: bool = True
    check_links: bool = True
    max_links: int = 50
    min_tap_target: int = 24
    max_screenshot_height: int = 8000
    nav_timeout_ms: int = 15_000
    # Local models (ollama). Vision is a critique of screenshots; explore is an LLM-driven
    # ReAct phase after the deterministic sweep.
    ollama_host: str = "http://127.0.0.1:11434"
    allow_remote_ollama: bool = False
    vision: VisionMode = "auto"
    vision_model: str = "qwen2.5vl:7b"
    max_vision_images: int = 24
    explore_steps: int = 0
    agent_model: str = "qwen3:8b"
    # Browser.
    chromium_path: str | None = None
    cdp_url: str | None = None
    chromium_args: tuple[str, ...] = ("--disable-dev-shm-usage",)
    # CI gate.
    fail_on: Severity = Severity.MAJOR

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Config:
        e = os.environ if env is None else env
        base = cls()
        cfg = replace(
            base,
            ollama_host=e.get("OLLAMA_HOST", base.ollama_host) or base.ollama_host,
            allow_remote_ollama=_bool(e.get("UI_REVIEW_ALLOW_REMOTE_OLLAMA"), False),
            vision=_vision(e.get("UI_REVIEW_VISION", base.vision)),
            vision_model=e.get("UI_REVIEW_VISION_MODEL", base.vision_model),
            agent_model=e.get("UI_REVIEW_AGENT_MODEL", base.agent_model),
            explore_steps=_int(e.get("UI_REVIEW_EXPLORE_STEPS"), base.explore_steps),
            max_pages=_int(e.get("UI_REVIEW_MAX_PAGES"), base.max_pages),
            chromium_path=e.get("UI_REVIEW_CHROMIUM_PATH") or None,
            cdp_url=e.get("UI_REVIEW_CDP_URL") or None,
            chromium_args=tuple(a for a in e.get("UI_REVIEW_CHROMIUM_ARGS", "").split() if a)
            or base.chromium_args,
        )
        return cfg.validated()

    def validated(self) -> Config:
        """Return ``self`` normalised, or raise :class:`ConfigError`."""
        unknown = [v for v in self.viewports if v not in VIEWPORTS]
        if unknown or not self.viewports:
            raise ConfigError(f"viewports must be a subset of {sorted(VIEWPORTS)}: {unknown}")
        bad = [s for s in self.schemes if s not in SCHEMES]
        if bad or not self.schemes:
            raise ConfigError(f"schemes must be a subset of {list(SCHEMES)}: {bad}")
        for name in ("max_pages", "max_depth", "max_steps", "max_links", "min_tap_target"):
            if getattr(self, name) < 0:
                raise ConfigError(f"{name} must be >= 0")
        if self.max_pages < 1:
            raise ConfigError("max_pages must be >= 1")
        if not 0 <= self.pixel_tolerance <= 255:
            raise ConfigError("pixel_tolerance must be between 0 and 255")
        if not 0 <= self.max_diff_ratio <= 1:
            raise ConfigError("max_diff_ratio must be between 0 and 1")
        if self.vision not in ("auto", "on", "off"):
            raise ConfigError("vision must be auto, on or off")
        check_ollama_host(self.ollama_host, allow_remote=self.allow_remote_ollama)
        if self.cdp_url:
            check_loopback_url(self.cdp_url, what="UI_REVIEW_CDP_URL")
        hosts = tuple(h.strip().lower() for h in self.allow_hosts if h.strip())
        for host in hosts:
            if "/" in host or ":" in host.lstrip("*.") or not host.lstrip("*."):
                raise ConfigError(f"allow host must be a bare host name: {host!r}")
        return replace(self, allow_hosts=hosts)

    def views(self) -> list[tuple[Viewport, Scheme]]:
        return [(VIEWPORTS[v], s) for v in self.viewports for s in self.schemes]


def is_loopback_host(host: str) -> bool:
    host = host.strip("[]").lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def check_loopback_url(url: str, *, what: str) -> None:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https", "ws", "wss") or not parts.hostname:
        raise ConfigError(f"{what} must be an http(s) or ws(s) URL: {url!r}")
    if parts.username or parts.password:
        raise ConfigError(f"{what} must not contain credentials")
    if not is_loopback_host(parts.hostname):
        raise ConfigError(f"{what} must be a loopback address: {url!r}")


def check_ollama_host(url: str, *, allow_remote: bool) -> None:
    """Screenshots and page text go to ollama: keep it on this machine unless opted in."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ConfigError(f"OLLAMA_HOST must be an http(s) URL: {url!r}")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ConfigError("OLLAMA_HOST must not contain credentials, a query or a fragment")
    if not allow_remote and not is_loopback_host(parts.hostname):
        raise ConfigError(
            f"OLLAMA_HOST {url!r} is not a loopback address; set "
            "UI_REVIEW_ALLOW_REMOTE_OLLAMA=1 to send screenshots there"
        )


def resolve_target(target: str) -> tuple[str, Path | None]:
    """Return ``(start_url, static_root)``. A directory or file is served locally."""
    parts = urlsplit(target)
    if parts.scheme in ("http", "https"):
        if not parts.hostname:
            raise ConfigError(f"not a valid URL: {target!r}")
        if parts.username or parts.password:
            raise ConfigError("the start URL must not contain credentials")
        return target, None
    if parts.scheme and len(parts.scheme) > 1:
        raise ConfigError(f"only http(s) URLs or a local directory are supported: {target!r}")
    path = Path(target).expanduser().resolve()
    if path.is_dir():
        return "/", path
    if path.is_file():
        return "/" + path.name, path.parent
    raise ConfigError(f"no such directory or URL: {target!r}")


def _bool(value: str | None, default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _int(value: str | None, default: int) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ConfigError(f"expected an integer, got {value!r}") from exc


def _vision(value: str) -> VisionMode:
    v = value.strip().lower()
    if v == "auto":
        return "auto"
    if v == "on":
        return "on"
    if v == "off":
        return "off"
    raise ConfigError(f"UI_REVIEW_VISION must be auto, on or off, not {value!r}")
