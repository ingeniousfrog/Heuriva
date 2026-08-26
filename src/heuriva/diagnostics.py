from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from heuriva.config import AppConfig, config_dir, default_home
from heuriva.storage.sqlite import SQLiteStore


@dataclass(frozen=True)
class DiagnosticsReport:
    config_path: Path
    endpoint: str
    model: str
    sqlite_schema: str
    llm_timeout_line: str
    search_timeout_line: str
    quality_line: str
    stale_running_count: int
    oldest_stale_task_id: str | None

    def lines(self) -> tuple[str, ...]:
        lines = [
            f"Config: {self.config_path}",
            f"Endpoint: {self.endpoint}",
            f"Model: {self.model}",
            self.llm_timeout_line,
            self.search_timeout_line,
            self.quality_line,
            f"SQLite schema: {self.sqlite_schema}",
            f"Stale running tasks: {self.stale_running_count}",
        ]
        if self.oldest_stale_task_id is not None:
            lines.append(f"Oldest stale task: {self.oldest_stale_task_id}")
        return tuple(lines)


def collect_diagnostics(config: AppConfig, *, home: Path | None = None) -> DiagnosticsReport:
    root_home = home or default_home()
    stale = SQLiteStore.stale_running_summary(
        config.storage.sqlite_path,
        max_age_seconds=config.runtime.max_task_seconds,
    )
    stale_count = stale["count"]
    oldest_stale_task_id = stale["oldest_task_id"]
    assert isinstance(stale_count, int)
    assert oldest_stale_task_id is None or isinstance(oldest_stale_task_id, str)
    return DiagnosticsReport(
        config_path=config_dir(root_home) / "config.yaml",
        endpoint=config.llm.base_url,
        model=config.llm.model,
        sqlite_schema=SQLiteStore.schema_status(config.storage.sqlite_path),
        llm_timeout_line=(
            "LLM timeouts: "
            f"connect={config.llm.connect_timeout_seconds:g}s "
            f"read={config.llm.read_timeout_seconds:g}s "
            f"retries={config.llm.max_retries}"
        ),
        search_timeout_line=(
            "Search timeout: "
            f"{config.tools.search.timeout_seconds:g}s "
            f"max_results={config.tools.search.max_results} "
            f"enabled={str(config.tools.search.enabled).lower()}"
        ),
        quality_line=(
            "Quality modes: "
            f"relevance={config.quality.evidence_relevance_mode.value} "
            f"completion={config.quality.completion_check_mode.value} "
            f"search_budget={config.quality.max_search_steps} "
            f"completion_repairs={config.quality.max_completion_repairs}"
        ),
        stale_running_count=stale_count,
        oldest_stale_task_id=oldest_stale_task_id,
    )
