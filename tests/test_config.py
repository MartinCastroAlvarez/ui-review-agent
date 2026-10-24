from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from ui_review.config import Config
from ui_review.config import ConfigError
from ui_review.config import check_loopback_url
from ui_review.config import is_loopback_host
from ui_review.config import resolve_target


def test_defaults_are_local_and_safe() -> None:
    cfg = Config.from_env({})
    assert cfg.ollama_host == "http://127.0.0.1:11434"
    assert cfg.vision == "auto"
    assert cfg.explore_steps == 0
    assert len(cfg.views()) == 6
    assert cfg.chromium_args == ("--disable-dev-shm-usage",)


def test_from_env_reads_overrides() -> None:
    cfg = Config.from_env(
        {
            "OLLAMA_HOST": "http://localhost:1234",
            "UI_REVIEW_VISION": "OFF",
            "UI_REVIEW_VISION_MODEL": "gemma3:4b",
            "UI_REVIEW_AGENT_MODEL": "llama3.2",
            "UI_REVIEW_EXPLORE_STEPS": "7",
            "UI_REVIEW_MAX_PAGES": "3",
            "UI_REVIEW_CHROMIUM_PATH": "/opt/chrome",
            "UI_REVIEW_CDP_URL": "http://127.0.0.1:9222",
            "UI_REVIEW_CHROMIUM_ARGS": "--a --b",
        }
    )
    assert (cfg.vision, cfg.vision_model, cfg.agent_model) == ("off", "gemma3:4b", "llama3.2")
    assert (cfg.explore_steps, cfg.max_pages) == (7, 3)
    assert cfg.chromium_path == "/opt/chrome" and cfg.cdp_url == "http://127.0.0.1:9222"
    assert cfg.chromium_args == ("--a", "--b")


@pytest.mark.parametrize(
    "env",
    [
        {"UI_REVIEW_VISION": "maybe"},
        {"UI_REVIEW_MAX_PAGES": "lots"},
        {"UI_REVIEW_MAX_PAGES": "0"},
        {"OLLAMA_HOST": "http://10.0.0.5:11434"},
        {"OLLAMA_HOST": "http://u:p@localhost:11434"},
        {"OLLAMA_HOST": "ftp://localhost"},
        {"UI_REVIEW_CDP_URL": "http://192.168.1.2:9222"},
    ],
)
def test_bad_env_rejected(env: dict[str, str]) -> None:
    with pytest.raises(ConfigError):
        Config.from_env(env)


def test_remote_ollama_needs_opt_in() -> None:
    env = {"OLLAMA_HOST": "http://gpu-box:11434", "UI_REVIEW_ALLOW_REMOTE_OLLAMA": "1"}
    assert Config.from_env(env).ollama_host == "http://gpu-box:11434"


@pytest.mark.parametrize(
    "changes",
    [
        {"viewports": ("watch",)},
        {"viewports": ()},
        {"schemes": ("sepia",)},
        {"max_depth": -1},
        {"pixel_tolerance": 300},
        {"max_diff_ratio": 2.0},
        {"vision": "sometimes"},
        {"allow_hosts": ("https://cdn.example.com",)},
        {"allow_hosts": ("cdn.example.com:443",)},
        {"allow_hosts": ("*.",)},
    ],
)
def test_validation(changes: dict[str, object]) -> None:
    with pytest.raises(ConfigError):
        replace(Config(), **changes).validated()


def test_allow_hosts_normalised() -> None:
    cfg = replace(Config(), allow_hosts=(" CDN.Example.com ", "", "*.fonts.test")).validated()
    assert cfg.allow_hosts == ("cdn.example.com", "*.fonts.test")


def test_loopback_helpers() -> None:
    assert is_loopback_host("localhost") and is_loopback_host("[::1]")
    assert is_loopback_host("127.0.0.2") and not is_loopback_host("example.com")
    check_loopback_url("ws://127.0.0.1:9222/devtools", what="x")
    with pytest.raises(ConfigError):
        check_loopback_url("ws://u:p@127.0.0.1:9222", what="x")
    with pytest.raises(ConfigError):
        check_loopback_url("file:///x", what="x")


def test_resolve_target(tmp_path: Path) -> None:
    assert resolve_target("https://example.com/a") == ("https://example.com/a", None)
    assert resolve_target(str(tmp_path)) == ("/", tmp_path.resolve())
    page = tmp_path / "page.html"
    page.write_text("<p>x</p>")
    assert resolve_target(str(page)) == ("/page.html", tmp_path.resolve())
    for bad in ("https://", "https://u:p@example.com", "ftp://example.com", str(tmp_path / "no")):
        with pytest.raises(ConfigError):
            resolve_target(bad)
