from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True)
class ModelChatResult:
    content: str
    metadata: dict[str, Any]


class ModelClientError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        attempt_count: int = 1,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.attempt_count = attempt_count

    def with_attempt_count(self, attempt_count: int) -> ModelClientError:
        return ModelClientError(
            self.code,
            self.message,
            retryable=self.retryable,
            attempt_count=attempt_count,
        )


class ModelClient:
    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None = None,
        connect_timeout_seconds: float = 5.0,
        read_timeout_seconds: float = 180.0,
        max_retries: int = 0,
        retry_backoff_seconds: float = 0.1,
        sleep: Callable[[float], None] = time.sleep,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.max_retries = max(0, max_retries)
        self.retry_backoff_seconds = max(0.0, retry_backoff_seconds)
        self._sleep = sleep
        self._own_client = http_client is None
        self._aborted = False
        self._connect_timeout_seconds = connect_timeout_seconds
        self._read_timeout_seconds = read_timeout_seconds
        timeout = httpx.Timeout(
            timeout=read_timeout_seconds,
            connect=connect_timeout_seconds,
            read=read_timeout_seconds,
        )
        self.http_client = http_client or httpx.Client(timeout=timeout)

    def abort(self) -> None:
        """Abort in-flight HTTP (Session Interrupt). Subsequent chat raises KeyboardInterrupt."""
        self._aborted = True
        try:
            self.http_client.close()
        except Exception:
            return

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        deadline_monotonic: float | None = None,
    ) -> ModelChatResult:
        self._raise_if_aborted()
        for attempt in range(1, self.max_retries + 2):
            self._raise_if_aborted()
            if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
                raise ModelClientError(
                    "timeout",
                    "task deadline reached before model request",
                    retryable=True,
                    attempt_count=attempt,
                )
            try:
                result = self._chat_once(messages)
            except KeyboardInterrupt:
                raise
            except ModelClientError as exc:
                self._raise_if_aborted()
                error = exc.with_attempt_count(attempt)
                if not error.retryable or attempt > self.max_retries:
                    raise error from exc
                self._sleep(_retry_delay(self.retry_backoff_seconds, attempt))
                continue
            return ModelChatResult(
                content=result.content,
                metadata={**result.metadata, "attempt_count": attempt},
            )
        raise ModelClientError(
            "request_error",
            "model request retries exhausted",
            retryable=True,
            attempt_count=self.max_retries + 1,
        )

    def _raise_if_aborted(self) -> None:
        if self._aborted:
            raise KeyboardInterrupt

    def _chat_once(self, messages: list[dict[str, str]]) -> ModelChatResult:
        """POST chat/completions; poll abort so Interrupt is not stuck on long reads."""
        self._raise_if_aborted()
        url = f"{self.base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        box: dict[str, Any] = {}

        def worker() -> None:
            try:
                box["response"] = self.http_client.post(
                    url,
                    headers=headers,
                    json={"model": self.model, "messages": messages},
                )
            except BaseException as exc:  # noqa: BLE001 - ferry to caller thread
                box["error"] = exc

        thread = threading.Thread(target=worker, name="heuriva-model-chat", daemon=True)
        thread.start()
        while thread.is_alive():
            if self._aborted:
                try:
                    self.http_client.close()
                except Exception:
                    pass
                thread.join(timeout=0.5)
                raise KeyboardInterrupt
            thread.join(timeout=0.05)

        if "error" in box:
            exc = box["error"]
            if isinstance(exc, KeyboardInterrupt):
                raise KeyboardInterrupt
            self._raise_if_aborted()
            if isinstance(exc, httpx.TimeoutException):
                raise ModelClientError(
                    "timeout", "model request timed out", retryable=True
                ) from exc
            if isinstance(exc, httpx.ConnectError):
                raise ModelClientError(
                    "connection_error", "could not connect to model endpoint"
                ) from exc
            if isinstance(exc, httpx.RequestError):
                raise ModelClientError("request_error", str(exc), retryable=True) from exc
            raise exc

        response = box["response"]
        self._raise_if_aborted()
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
            try:
                self.http_client.close()
            except Exception:
                return

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


def _retry_delay(base_seconds: float, attempt: int) -> float:
    return float(min(base_seconds * (2 ** max(0, attempt - 1)), 2.0))
