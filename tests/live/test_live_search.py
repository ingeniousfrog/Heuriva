from __future__ import annotations

import os

import pytest

from heuriva.clients.search import SearchClient
from heuriva.config import load_config


def test_live_search_protocol_smoke() -> None:
    if os.environ.get("HEURIVA_RUN_LIVE_SEARCH_TESTS") != "1":
        pytest.skip("set HEURIVA_RUN_LIVE_SEARCH_TESTS=1 to run live search smoke tests")
    config = load_config()
    results, error = SearchClient(
        max_results=config.tools.search.max_results,
        timeout_seconds=config.tools.search.timeout_seconds,
    ).search("OpenAI Codex")
    if error is not None:
        pytest.fail(f"live search failed with {error.code}: {error.message}")
    assert results
    assert results[0].url.startswith(("http://", "https://"))
