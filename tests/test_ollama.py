from __future__ import annotations

import httpx
import pytest

from tests.conftest import fake_ollama
from ui_review.ollama import OllamaClient
from ui_review.ollama import OllamaError


def client(transport: httpx.BaseTransport) -> OllamaClient:
    return OllamaClient("http://127.0.0.1:11434", transport=transport)


def test_models_and_has_model() -> None:
    c = client(
        fake_ollama(
            lambda p, b: {"models": [{"name": "llava:latest"}, {"name": "qwen3:8b"}, "junk"]}
        )
    )
    assert c.models() == ["llava:latest", "qwen3:8b"]
    assert c.has_model("llava") and c.has_model("qwen3:8b") and not c.has_model("gemma3")
    c.close()


def test_chat_sends_tools_and_format() -> None:
    calls: list[tuple[str, dict[str, object]]] = []
    c = client(fake_ollama(lambda p, b: {"message": {"role": "assistant", "content": "hi"}}, calls))
    msg = c.chat(
        "m",
        [{"role": "user", "content": "x"}],
        tools=[{"type": "function"}],
        format={"type": "object"},
    )
    assert msg["content"] == "hi"
    path, body = calls[0]
    assert path == "/api/chat"
    assert body["stream"] is False and body["tools"] and body["format"] == {"type": "object"}
    assert body["options"] == {"temperature": 0.0}


def test_errors_are_wrapped() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(OllamaError, match="not reachable"):
        client(httpx.MockTransport(boom)).models()
    with pytest.raises(OllamaError, match="ConnectError"):
        client(httpx.MockTransport(boom)).chat("m", [])
    bad = httpx.MockTransport(lambda r: httpx.Response(500, json={"error": "model not found"}))
    with pytest.raises(OllamaError, match="model not found"):
        client(bad).chat("m", [])
    with pytest.raises(OllamaError, match="HTTP 500"):
        client(bad).models()
    text = httpx.MockTransport(lambda r: httpx.Response(404, text="nope"))
    with pytest.raises(OllamaError, match="HTTP 404"):
        client(text).chat("m", [])
    with pytest.raises(OllamaError, match="no message"):
        client(fake_ollama(lambda p, b: {"done": True})).chat("m", [])


def test_never_follows_redirects() -> None:
    hops: list[str] = []

    def redirect(request: httpx.Request) -> httpx.Response:
        hops.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://evil.example/api/chat"})

    with pytest.raises(OllamaError, match="HTTP 302"):
        client(httpx.MockTransport(redirect)).chat("m", [])
    assert hops == ["http://127.0.0.1:11434/api/chat"]
