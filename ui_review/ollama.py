"""A minimal ollama HTTP client: ``/api/tags`` and ``/api/chat`` (tools, images, JSON format).

The client never follows redirects and ignores proxy environment variables, so screenshots
and page text only ever reach ``OLLAMA_HOST`` (loopback unless the operator opts in).
"""

from __future__ import annotations

from typing import Any

import httpx


class OllamaError(RuntimeError):
    """ollama is unreachable or answered with an error."""


class OllamaClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout_s: float = 300.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url
        self._http = httpx.Client(
            base_url=base_url,
            timeout=httpx.Timeout(timeout_s, connect=3.0),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    def models(self) -> list[str]:
        try:
            response = self._http.get("/api/tags")
        except httpx.HTTPError as exc:
            raise OllamaError(f"ollama is not reachable at {self.base_url}") from exc
        if response.status_code != 200:
            raise OllamaError(f"/api/tags: HTTP {response.status_code}")
        data = response.json()
        models = data.get("models", []) if isinstance(data, dict) else []
        return [str(m.get("name", "")) for m in models if isinstance(m, dict)]

    def has_model(self, model: str) -> bool:
        names = self.models()
        wanted = model if ":" in model else f"{model}:latest"
        return model in names or wanted in names

    def chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        format: dict[str, Any] | None = None,
        temperature: float = 0.0,
    ) -> dict[str, Any]:
        """One non-streamed chat turn. Returns the assistant ``message`` object."""
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature},
        }
        if tools:
            payload["tools"] = tools
        if format is not None:
            payload["format"] = format
        try:
            response = self._http.post("/api/chat", json=payload)
        except httpx.HTTPError as exc:
            raise OllamaError(f"/api/chat: {exc.__class__.__name__}") from exc
        if response.status_code != 200:
            raise OllamaError(f"/api/chat: HTTP {response.status_code} {_error(response)}")
        data = response.json()
        message = data.get("message") if isinstance(data, dict) else None
        if not isinstance(message, dict):
            raise OllamaError("/api/chat: response has no message")
        return message


def _error(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return ""
    return str(data.get("error", ""))[:200] if isinstance(data, dict) else ""
