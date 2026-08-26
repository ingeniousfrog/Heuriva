"""Progress log and status helpers for Session UI."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProgressLogEntry:
    ts: float
    stage: str
    message: str
    step_index: int | None = None
    operator: str | None = None
    task_id: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def display_storage_path(path: str | Path) -> str:
    """Show ~/.heuriva/memory.db instead of /Users/.../.heuriva/memory.db."""
    default = Path("~/.heuriva/memory.db").expanduser()
    try:
        resolved = Path(path).expanduser().resolve()
        if resolved == default.resolve():
            return "~/.heuriva/memory.db"
        home = Path.home().resolve()
        rel = resolved.relative_to(home)
        return f"~/{rel.as_posix()}"
    except (ValueError, OSError):
        text = str(path)
        home_str = str(Path.home())
        if text.startswith(home_str):
            return "~" + text[len(home_str) :]
        return text
