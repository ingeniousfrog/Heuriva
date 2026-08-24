# Heuriva

**Last Updated:** 2026-08-24

Heuriva is a Python CLI cognitive runtime for language models. v0.1 focuses on
one local experiment: can a frozen-weight model produce a visible,
serializable, inspectable task-solving trajectory when the runtime keeps
explicit state and asks a controller to choose only the next cognitive
operation?

## Current Status

Implemented in this repository:

- Python package with `heuriva` CLI entry point
- `heuriva setup`, `heuriva doctor`, `heuriva run`, interactive `heuriva`, and
  `heuriva show`
- Local config under `~/.heuriva/`
- OpenAI-compatible non-streaming `/v1/chat/completions` client
- v0.1 operators: `ANALYZE`, `SEARCH`, `ANSWER`
- LLM controller with structured JSON validation and one repair attempt
- Deterministic executor router that keeps operator selection separate from
  executor selection
- LLM and search executors
- Immutable Pydantic v2 schemas for state, decision, observation, events, and
  trajectory records
- SQLite trajectory store with schema versioning, foreign keys, unique step
  constraints, and atomic step commits
- Concise and detailed trace rendering
- Automated fake model/search tests for core runtime paths

Not implemented in v0.1:

- Learning policies, policy lifecycle, replay, benchmark runner, evaluation
  tables, vector database, dashboard, MCP, shell/filesystem/Python executors,
  multi-agent workflows, URL crawling, daemon mode, task resume, or concurrent
  queues

Live Cursor endpoint and real web search smoke tests are opt-in and were not
run as part of the automated test suite.

## Install For Development

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

The project is declared in `pyproject.toml` and requires Python 3.11 or newer.
SQLite uses Python's standard library `sqlite3`.

## Quick Start

Create local config:

```bash
heuriva setup
```

Run diagnostics:

```bash
heuriva doctor
heuriva doctor --probe
```

Run a task:

```bash
heuriva run --trace "Analyze whether this project should become a product"
```

Inspect a stored trajectory:

```bash
heuriva show --trace <task_id>
heuriva show --json <task_id>
```

Start the simple REPL:

```bash
heuriva --trace
```

## Configuration

`heuriva setup` creates:

```text
~/.heuriva/
├── config.yaml
├── .env
└── memory.db   # created when storage is first opened
```

Default model config:

```yaml
llm:
  base_url: http://localhost:8765/v1
  model: auto
  api_key_env: HEURIVA_API_KEY
```

Supported environment overrides:

- `HEURIVA_LLM_BASE_URL`
- `HEURIVA_LLM_MODEL`
- `HEURIVA_API_KEY`
- `HEURIVA_DB_PATH`

API keys are read from environment variables and are not written to YAML,
SQLite, or trace output. The SQLite database is a local plaintext trajectory
store, not encrypted memory.

Search is enabled by default. Search queries are sent to the configured
third-party search provider, and search snippets are treated as untrusted
external data.

## Runtime Shape

For each task, Heuriva creates an initial immutable `CognitiveState`, then loops
until it reaches `done`, `failed`, `max_steps_reached`, or `interrupted`.

Each committed operator step stores:

- state before the step
- validated decision
- executor observation
- state after the step
- trajectory step row

The controller chooses an operator only. The runtime-owned `ExecutorRouter`
maps:

```text
ANALYZE -> llm
SEARCH  -> search
ANSWER  -> llm
```

When only one step remains, the runtime exposes only `ANSWER` to force a final
attempt instead of allowing an endless analysis/search loop.

## Verification

Automated checks used for this implementation:

```bash
.venv/bin/ruff format --check .
.venv/bin/ruff check .
.venv/bin/mypy src tests
.venv/bin/pytest
```

The fake test suite covers schema immutability, config precedence, redaction,
OpenAI-compatible client response handling, controller malformed JSON repair,
router separation, state patch application, SQLite rollback, CLI setup/doctor,
and dynamic runtime paths including both `ANALYZE -> SEARCH -> ANSWER` and
`ANALYZE -> ANSWER`.

Live verification should be recorded separately:

```bash
HEURIVA_RUN_LIVE_LLM_TESTS=1 heuriva doctor --probe
HEURIVA_RUN_LIVE_SEARCH_TESTS=1 heuriva run --trace "..."
```

A successful small `doctor --probe` confirms only the minimal protocol path. It
does not prove a full multi-step product run or search quality.
