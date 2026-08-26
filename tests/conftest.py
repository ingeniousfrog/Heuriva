"""Shared pytest environment defaults for CI-friendly CLI help tests."""

from __future__ import annotations

import os

# Typer 0.27+ Rich help truncates badly in CI non-TTY / narrow COLUMNS.
os.environ["COLUMNS"] = "200"
os.environ["NO_COLOR"] = "1"
os.environ["FORCE_COLOR"] = "0"
os.environ["TERM"] = "dumb"
