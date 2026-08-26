from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

DEFAULT_BASE_URL = "http://localhost:8765/v1"
DEFAULT_MODEL = "auto"
CONFIG_DIR_NAME = ".heuriva"


class LLMConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    api_key_env: str = "HEURIVA_API_KEY"
    api_key_required: bool = False
    connect_timeout_seconds: float = Field(default=5.0, ge=0.1, le=60.0)
    read_timeout_seconds: float = Field(default=180.0, ge=1.0, le=600.0)
    max_retries: int = Field(default=1, ge=0, le=5)

    @field_validator("base_url")
    @classmethod
    def _valid_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("base_url must be a valid http or https URL")
        if parsed.username or parsed.password:
            raise ValueError("base_url must not contain credentials")
        return value.rstrip("/")

    @field_validator("model", "api_key_env")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("config value must not be empty")
        return stripped


class RuntimeConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    max_steps: int = Field(default=20, ge=1, le=100)
    max_task_seconds: int = Field(default=600, ge=1, le=7200)
    controller_repair_attempts: int = Field(default=1, ge=0, le=3)
    max_consecutive_failures: int = Field(default=3, ge=1, le=20)
    max_same_operator_streak: int = Field(default=3, ge=1, le=20)
    max_no_progress_steps: int = Field(default=2, ge=1, le=20)
    answer_reserve_steps: int = Field(default=2, ge=1, le=20)


class QualityMode(StrEnum):
    OFF = "off"
    OBSERVE = "observe"
    ENFORCE = "enforce"


class QualityConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence_relevance_mode: QualityMode = QualityMode.OBSERVE
    completion_check_mode: QualityMode = QualityMode.OBSERVE
    max_search_steps: int = Field(default=3, ge=0, le=20)
    max_no_relevant_search_steps: int = Field(default=1, ge=0, le=20)
    max_completion_repairs: int = Field(default=1, ge=0, le=10)


class StorageConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    sqlite_path: str = "~/.heuriva/memory.db"

    @field_validator("sqlite_path")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("sqlite_path must not be empty")
        return stripped


class SearchToolConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = True
    max_results: int = Field(default=5, ge=1, le=20)
    timeout_seconds: float = Field(default=15.0, ge=1.0, le=60.0)


class ToolsConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    search: SearchToolConfig = Field(default_factory=SearchToolConfig)


class AppConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    llm: LLMConfig = Field(default_factory=LLMConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    quality: QualityConfig = Field(default_factory=QualityConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)

    def redacted_snapshot(self) -> dict[str, Any]:
        return {
            "llm": {
                "base_url": self.llm.base_url,
                "model": self.llm.model,
                "api_key_env": self.llm.api_key_env,
                "api_key_required": self.llm.api_key_required,
                "connect_timeout_seconds": self.llm.connect_timeout_seconds,
                "read_timeout_seconds": self.llm.read_timeout_seconds,
                "max_retries": self.llm.max_retries,
            },
            "runtime": self.runtime.model_dump(mode="json"),
            "quality": self.quality.model_dump(mode="json"),
            "storage": {"sqlite_path": self.storage.sqlite_path},
            "tools": self.tools.model_dump(mode="json"),
        }


@dataclass(frozen=True)
class SetupResult:
    config_dir: Path
    config_path: Path
    env_path: Path
    created_config: bool
    created_env: bool


def config_dir(home: Path | None = None) -> Path:
    return (home or Path.home()) / CONFIG_DIR_NAME


def default_config_text() -> str:
    return "\n".join(
        [
            "llm:",
            f"  base_url: {DEFAULT_BASE_URL}",
            f"  model: {DEFAULT_MODEL}",
            "  api_key_env: HEURIVA_API_KEY",
            "  api_key_required: false",
            "  connect_timeout_seconds: 5",
            "  read_timeout_seconds: 180",
            "  max_retries: 1",
            "",
            "runtime:",
            "  max_steps: 20",
            "  max_task_seconds: 600",
            "  controller_repair_attempts: 1",
            "  max_consecutive_failures: 3",
            "  max_same_operator_streak: 3",
            "  max_no_progress_steps: 2",
            "  answer_reserve_steps: 2",
            "",
            "quality:",
            "  evidence_relevance_mode: observe",
            "  completion_check_mode: observe",
            "  max_search_steps: 3",
            "  max_no_relevant_search_steps: 1",
            "  max_completion_repairs: 1",
            "",
            "storage:",
            "  sqlite_path: ~/.heuriva/memory.db",
            "",
            "tools:",
            "  search:",
            "    enabled: true",
            "    max_results: 5",
            "    timeout_seconds: 15",
            "",
        ]
    )


def default_env_text() -> str:
    return "\n".join(
        [
            "# Optional local API key. Leave empty for local endpoints that do not require auth.",
            "HEURIVA_API_KEY=",
            "",
        ]
    )


def setup_config(*, home: Path | None = None, force: bool = False) -> SetupResult:
    root = config_dir(home)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    config_path = root / "config.yaml"
    env_path = root / ".env"
    created_config = _write_if_missing(config_path, default_config_text(), force=force, mode=0o600)
    created_env = _write_if_missing(env_path, default_env_text(), force=force, mode=0o600)
    return SetupResult(
        config_dir=root,
        config_path=config_path,
        env_path=env_path,
        created_config=created_config,
        created_env=created_env,
    )


def load_config(
    *,
    home: Path | None = None,
    cli_overrides: dict[str, Any] | None = None,
) -> AppConfig:
    root_home = home or Path.home()
    raw: dict[str, Any] = {}
    path = config_dir(root_home) / "config.yaml"
    if path.exists():
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            raise ValueError(f"config file must contain a mapping: {path}")
        raw = _deep_merge(raw, loaded)
    env_values = _read_env_file(config_dir(root_home) / ".env")
    env_values.update(os.environ)
    raw = _deep_merge(raw, _env_to_config(env_values))
    raw = _deep_merge(raw, cli_overrides or {})
    raw = _expand_storage_path(raw, root_home)
    return AppConfig.model_validate(raw)


def api_key_for(config: AppConfig) -> str | None:
    value = os.environ.get(config.llm.api_key_env)
    return value or None


def llm_settings_public(*, home: Path | None = None) -> dict[str, str]:
    config = load_config(home=home)
    return {"base_url": config.llm.base_url, "model": config.llm.model}


def update_llm_settings(
    *,
    base_url: str | None = None,
    model: str | None = None,
    home: Path | None = None,
) -> AppConfig:
    """Update ~/.heuriva/config.yaml llm section (Session UI settings)."""
    root_home = home or Path.home()
    config_path = config_dir(root_home) / "config.yaml"
    if not config_path.exists():
        setup_config(home=root_home)
    loaded = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"config file must contain a mapping: {config_path}")
    llm = dict(loaded.get("llm") or {})
    if base_url is not None:
        llm["base_url"] = base_url.strip().rstrip("/")
    if model is not None:
        llm["model"] = model.strip()
    loaded["llm"] = llm
    expanded = _expand_storage_path(loaded, root_home)
    config = AppConfig.model_validate(expanded)
    config_path.write_text(
        yaml.safe_dump(loaded, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    config_path.chmod(0o600)
    return config


def _write_if_missing(path: Path, content: str, *, force: bool, mode: int) -> bool:
    if path.exists() and not force:
        return False
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(content, encoding="utf-8")
    temp_path.chmod(mode)
    temp_path.replace(path)
    return True


def _deep_merge(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _env_to_config(env: dict[str, str]) -> dict[str, Any]:
    raw: dict[str, Any] = {}
    if env.get("HEURIVA_LLM_BASE_URL"):
        raw = _deep_merge(raw, {"llm": {"base_url": env["HEURIVA_LLM_BASE_URL"]}})
    if env.get("HEURIVA_LLM_MODEL"):
        raw = _deep_merge(raw, {"llm": {"model": env["HEURIVA_LLM_MODEL"]}})
    if env.get("HEURIVA_DB_PATH"):
        raw = _deep_merge(raw, {"storage": {"sqlite_path": env["HEURIVA_DB_PATH"]}})
    return raw


def _expand_storage_path(raw: dict[str, Any], home: Path) -> dict[str, Any]:
    storage = dict(raw.get("storage", {}))
    path = str(storage.get("sqlite_path", "~/.heuriva/memory.db"))
    if path.startswith("~/"):
        path = str(home / path[2:])
    storage["sqlite_path"] = str(Path(path).expanduser())
    return _deep_merge(raw, {"storage": storage})
