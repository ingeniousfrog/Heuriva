from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from heuriva.clients.model import ModelClient, ModelClientError
from heuriva.clients.search import SearchClient
from heuriva.config import DEFAULT_BASE_URL, AppConfig, default_home, load_config, setup_config
from heuriva.redaction import redact_text


def test_default_home_prefers_home_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    isolated = tmp_path / "isolated-home"
    isolated.mkdir()
    monkeypatch.setenv("HOME", str(isolated))
    monkeypatch.delenv("HEURIVA_HOME", raising=False)
    assert default_home() == isolated


def test_setup_creates_config_without_overwriting(tmp_path: Path) -> None:
    home = tmp_path / "home"
    first = setup_config(home=home)
    config_path = home / ".heuriva" / "config.yaml"
    config_path.write_text("custom: true\n", encoding="utf-8")

    second = setup_config(home=home)

    assert first.created_config is True
    assert second.created_config is False
    assert config_path.read_text(encoding="utf-8") == "custom: true\n"


def test_load_config_precedence_and_redacted_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    setup_config(home=home, force=True)
    config_path = home / ".heuriva" / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "llm:",
                "  base_url: http://config.test/v1",
                "  model: config-model",
                "runtime:",
                "  max_steps: 7",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HEURIVA_LLM_MODEL", "env-model")
    monkeypatch.setenv("HEURIVA_API_KEY", "sk-test-secret")

    loaded = load_config(home=home)

    assert loaded.llm.base_url == "http://config.test/v1"
    assert loaded.llm.model == "env-model"
    assert loaded.runtime.max_steps == 7
    assert "sk-test-secret" not in str(loaded.redacted_snapshot())


def test_invalid_base_url_is_rejected() -> None:
    with pytest.raises(ValueError):
        AppConfig.model_validate({"llm": {"base_url": "http://user:pass@example.com/v1"}})


def test_model_client_joins_chat_completion_url_once() -> None:
    captured_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_urls.append(str(request.url))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "hello"}}], "usage": {"total_tokens": 3}},
        )

    client = ModelClient(
        base_url=DEFAULT_BASE_URL,
        model="auto",
        api_key="sk-test-secret",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    result = client.chat([{"role": "user", "content": "hi"}])

    assert result.content == "hello"
    assert captured_urls == ["http://localhost:8765/v1/chat/completions"]
    assert "sk-test-secret" not in redact_text(str(result.metadata), secrets=("sk-test-secret",))


def test_model_client_maps_error_status() -> None:
    client = ModelClient(
        base_url=DEFAULT_BASE_URL,
        model="auto",
        http_client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(401, json={"error": "bad"})
            )
        ),
    )

    with pytest.raises(ModelClientError) as exc:
        client.chat([{"role": "user", "content": "hi"}])

    assert exc.value.code == "auth_failed"


def test_model_client_rejects_empty_choices() -> None:
    client = ModelClient(
        base_url=DEFAULT_BASE_URL,
        model="auto",
        http_client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(200, json={"choices": []})
            )
        ),
    )

    with pytest.raises(ModelClientError) as exc:
        client.chat([{"role": "user", "content": "hi"}])

    assert exc.value.code == "protocol_error"


def test_runtime_config_exposes_v02_loop_guard_defaults(tmp_path: Path) -> None:
    config = AppConfig()

    assert config.runtime.max_same_operator_streak == 3
    assert config.runtime.max_no_progress_steps == 2
    assert config.runtime.answer_reserve_steps == 2
    assert "answer_reserve_steps: 2" in setup_config(home=tmp_path / "home").config_path.read_text(
        encoding="utf-8"
    )


def test_model_client_retries_retryable_errors_then_records_attempt_count() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, json={"error": "slow down"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    client = ModelClient(
        base_url=DEFAULT_BASE_URL,
        model="auto",
        max_retries=1,
        retry_backoff_seconds=0.0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    result = client.chat([{"role": "user", "content": "hi"}])

    assert calls == 2
    assert result.metadata["attempt_count"] == 2
    assert result.content == "ok"


def test_model_client_does_not_retry_non_retryable_errors() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(401, json={"error": "bad key"})

    client = ModelClient(
        base_url=DEFAULT_BASE_URL,
        model="auto",
        max_retries=3,
        retry_backoff_seconds=0.0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(ModelClientError) as exc:
        client.chat([{"role": "user", "content": "hi"}])

    assert calls == 1
    assert exc.value.code == "auth_failed"
    assert exc.value.attempt_count == 1


def test_model_client_reports_attempt_count_when_retries_exhaust() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500, json={"error": "temporary"})

    client = ModelClient(
        base_url=DEFAULT_BASE_URL,
        model="auto",
        max_retries=2,
        retry_backoff_seconds=0.0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(ModelClientError) as exc:
        client.chat([{"role": "user", "content": "hi"}])

    assert calls == 3
    assert exc.value.code == "provider_error"
    assert exc.value.attempt_count == 3


def test_search_client_classifies_timeout_separately() -> None:
    class TimeoutProvider:
        def text(self, query: str, *, max_results: int) -> list[dict[str, object]]:
            del query, max_results
            raise TimeoutError("provider timed out")

    results, error = SearchClient(
        max_results=3,
        timeout_seconds=0.1,
        provider=TimeoutProvider(),
    ).search("heuriva")

    assert results == ()
    assert error is not None
    assert error.code == "search_timeout"
    assert error.retryable is True
