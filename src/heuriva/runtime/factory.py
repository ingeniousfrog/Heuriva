"""Shared RuntimeEngine construction for CLI and Session UI."""

from __future__ import annotations

from heuriva.clients.model import ModelClient
from heuriva.clients.search import SearchClient
from heuriva.config import AppConfig, api_key_for, load_config
from heuriva.controller.llm_controller import LLMController
from heuriva.core.operator import Operator
from heuriva.executors.llm import LLMExecutor
from heuriva.executors.search import SearchExecutor
from heuriva.runtime.engine import Executor, RuntimeEngine
from heuriva.storage.sqlite import SQLiteStore


def build_engine(
    *,
    config: AppConfig | None = None,
    store: SQLiteStore | None = None,
) -> RuntimeEngine:
    """Build a RuntimeEngine with the default LLM/search wiring."""
    resolved = config if config is not None else load_config()
    resolved_store = store if store is not None else SQLiteStore(resolved.storage.sqlite_path)
    model_client = ModelClient(
        base_url=resolved.llm.base_url,
        model=resolved.llm.model,
        api_key=api_key_for(resolved),
        connect_timeout_seconds=resolved.llm.connect_timeout_seconds,
        read_timeout_seconds=resolved.llm.read_timeout_seconds,
        max_retries=resolved.llm.max_retries,
    )
    controller = LLMController(
        model_client=model_client,
        repair_attempts=resolved.runtime.controller_repair_attempts,
    )
    executors: dict[Operator, Executor] = {
        Operator.ANALYZE: LLMExecutor(model_client=model_client),
        Operator.ANSWER: LLMExecutor(model_client=model_client),
    }
    if resolved.tools.search.enabled:
        executors[Operator.SEARCH] = SearchExecutor(
            search_client=SearchClient(
                max_results=resolved.tools.search.max_results,
                timeout_seconds=resolved.tools.search.timeout_seconds,
            ),
            quality_config=resolved.quality,
        )
    return RuntimeEngine(
        config=resolved,
        store=resolved_store,
        controller=controller,
        executors=executors,
    )
