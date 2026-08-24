from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from heuriva.clients.model import ModelClient, ModelClientError
from heuriva.config import DEFAULT_BASE_URL, AppConfig, load_config, setup_config
from heuriva.redaction import redact_text


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
