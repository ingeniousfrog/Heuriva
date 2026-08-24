from __future__ import annotations

import os

import pytest

from heuriva.clients.model import ModelClient, ModelClientError
from heuriva.config import api_key_for, load_config


def test_live_llm_protocol_smoke() -> None:
    if os.environ.get("HEURIVA_RUN_LIVE_LLM_TESTS") != "1":
        pytest.skip("set HEURIVA_RUN_LIVE_LLM_TESTS=1 to run live LLM smoke tests")
    config = load_config()
    client = ModelClient(
        base_url=config.llm.base_url,
        model=config.llm.model,
        api_key=api_key_for(config),
        connect_timeout_seconds=config.llm.connect_timeout_seconds,
        read_timeout_seconds=config.llm.read_timeout_seconds,
        max_retries=config.llm.max_retries,
    )
    try:
        response = client.chat([{"role": "user", "content": "Reply with ok."}])
    except ModelClientError as exc:
        raise AssertionError(f"live LLM endpoint failed with {exc.code}: {exc.message}") from exc
    finally:
        client.close()
    assert response.content.strip()
    assert response.metadata["attempt_count"] >= 1
