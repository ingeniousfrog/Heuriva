from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True)
class ModelChatResult:
    content: str
    metadata: dict[str, Any]


class ModelClientError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class ModelClient:
    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None = None,
        connect_timeout_seconds: float = 5.0,
        read_timeout_seconds: float = 180.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self._own_client = http_client is None
        timeout = httpx.Timeout(
            timeout=read_timeout_seconds,
            connect=connect_timeout_seconds,
            read=read_timeout_seconds,
        )
        self.http_client = http_client or httpx.Client(timeout=timeout)

    def chat(self, messages: list[dict[str, str]]) -> ModelChatResult:
        url = f"{self.base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            response = self.http_client.post(
                url,
                headers=headers,
                json={"model": self.model, "messages": messages},
            )
        except httpx.TimeoutException as exc:
            raise ModelClientError("timeout", "model request timed out", retryable=True) from exc
        except httpx.ConnectError as exc:
            raise ModelClientError(
                "connection_error", "could not connect to model endpoint"
            ) from exc
        except httpx.RequestError as exc:
            raise ModelClientError("request_error", str(exc), retryable=True) from exc
        if response.status_code >= 400:
            raise self._status_error(response)
        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise ModelClientError("non_json_response", "model response was not JSON") from exc
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ModelClientError("protocol_error", "model response had no choices")
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise ModelClientError(
                "empty_response", "model response content was empty", retryable=True
            )
        return ModelChatResult(
            content=content,
            metadata={
                "model": payload.get("model", self.model),
                "usage": payload.get("usage", {}),
            },
        )

    def models_probe(self) -> tuple[bool, str]:
        try:
            response = self.http_client.get(f"{self.base_url}/models")
        except httpx.TimeoutException:
            return False, "timeout"
        except httpx.ConnectError:
            return False, "connection refused"
        except httpx.RequestError as exc:
            return False, exc.__class__.__name__
        if response.status_code == 200:
            return True, "ok"
        return False, f"HTTP {response.status_code}"

    def close(self) -> None:
        if self._own_client:
            self.http_client.close()

    @staticmethod
    def _status_error(response: httpx.Response) -> ModelClientError:
        if response.status_code in {401, 403}:
            return ModelClientError("auth_failed", "model endpoint rejected authentication")
        if response.status_code == 429:
            return ModelClientError(
                "rate_limited", "model endpoint rate limited request", retryable=True
            )
        if response.status_code >= 500:
            return ModelClientError(
                "provider_error", f"model endpoint HTTP {response.status_code}", retryable=True
            )
        return ModelClientError("http_error", f"model endpoint HTTP {response.status_code}")
