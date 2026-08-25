# Heuriva

**Last Updated:** 2026-08-25

[中文文档](README-CN.md)

Heuriva is a Python CLI cognitive runtime for language models. v0.2 keeps the
small three-operator runtime from v0.1 and makes each task run easier to
explain, recover, and verify: the runtime tracks material state progress, guards
low-progress loops, validates evidence citations in final answers, and records
richer diagnostics in the local trajectory store.

v0.2.1 is a small polish release: CLI version visibility is available through
`heuriva --version` and `heuriva doctor`, and controller drafts now normalize a
single string `success_criteria` into a one-item list before validation.

## Current Status

Implemented in this repository:

- Python package with `heuriva` CLI entry point
- `heuriva setup`, `heuriva doctor`, `heuriva run`, interactive `heuriva`, and
  `heuriva show`
- Version visibility through `heuriva --version` and `heuriva doctor`
- Local config under `~/.heuriva/`
- OpenAI-compatible non-streaming `/v1/chat/completions` client
- v0.1 operators: `ANALYZE`, `SEARCH`, `ANSWER`
- LLM controller with structured JSON validation, `success_criteria`
  normalization, and one repair attempt
- Deterministic executor router that keeps operator selection separate from
  executor selection
- LLM and search executors
- Immutable Pydantic v2 schemas for state, decision, observation, events, and
  trajectory records
- SQLite trajectory store with schema versioning, foreign keys, unique step
  constraints, and atomic step commits
- Runtime-owned progress policy with same-operator, no-material-progress, and
  answer-reserve guards
- State delta rendering for concise trace, `show --trace`, and `show --json`
- Evidence-aware ANSWER prompt plus deterministic `[S1]` citation validation
  against saved state evidence
- Retryable model HTTP failures controlled by `llm.max_retries`, with
  `attempt_count` metadata
- Search timeout classification, stale running task diagnostics, and opt-in live
  smoke tests
- Automated fake model/search tests for core v0.1 and v0.2 runtime paths

Not implemented:

- Learning policies, policy lifecycle, replay, benchmark runner, evaluation
  tables, vector database, dashboard, MCP, shell/filesystem/Python executors,
  multi-agent workflows, URL crawling, daemon mode, task resume, or concurrent
  queues
- A separate `VERIFY` operator. v0.2 still uses only `ANALYZE`, `SEARCH`, and
  `ANSWER`.

Live Cursor-compatible endpoint and real web search smoke tests are opt-in and
are skipped by the default automated test suite.

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
heuriva --version
heuriva setup
```

Run diagnostics:

```bash
heuriva doctor
heuriva doctor --probe
heuriva doctor --probe --probe-timeout 30
```

Run a task:

```bash
heuriva run --trace "Analyze whether this project should become a product"
heuriva run --json "Analyze whether this project should become a product"
```

Long-running tasks stream live progress to stderr. When `--json` is used,
stdout stays reserved for the final machine-readable JSON payload. Use
`--no-progress` to suppress live status output.

Failed tasks exit non-zero and include a `heuriva show --trace <task_id>`
recovery command when a trajectory exists. Ctrl+C exits `130`; after the
runtime has created the task, stderr includes the full `task_id` and matching
`show --trace` command. Model endpoint failures keep a classified cause such as
`connection_error` or `timeout` in progress and saved runtime events.

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

runtime:
  max_steps: 20
  max_task_seconds: 600
  controller_repair_attempts: 1
  max_consecutive_failures: 3
  max_same_operator_streak: 3
  max_no_progress_steps: 2
  answer_reserve_steps: 2

tools:
  search:
    enabled: true
    max_results: 5
    timeout_seconds: 15
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

Before each controller decision, the runtime applies a deterministic progress
policy. It can narrow the available operators when repeated steps stop changing
material state, when the same operator repeats too long, or when the task is
inside the answer reserve. Guard interventions are written as runtime events
and shown in progress output.

Material progress is limited to structured state changes such as new evidence,
known items backed by evidence, resolved unknowns, new failure classification,
or a validated final answer. Bookkeeping-only changes, repeated content, and
confidence-only changes do not count.

If `SEARCH` has saved evidence, a successful `ANSWER` must cite at least one
known label such as `[S1]`. Unknown labels or missing required citations produce
an `answer_validation_error` observation instead of `done`, leaving the
trajectory readable and allowing a later ANSWER attempt within the remaining
budget.

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
controller `success_criteria` normalization, router separation, state patch
application, SQLite rollback, CLI setup/doctor, live run progress on stderr
without polluting JSON stdout, loop guard behavior, state delta rendering,
citation validation and repair, model retry accounting, search timeout
classification, stale task diagnostics, and dynamic runtime paths
including `ANALYZE -> SEARCH -> ANSWER`, `ANALYZE -> ANSWER`, and
`SEARCH -> ANSWER(validation error) -> ANSWER`.

Live verification should be recorded separately:

```bash
heuriva doctor --probe --probe-timeout 30
HEURIVA_RUN_LIVE_LLM_TESTS=1 .venv/bin/pytest tests/live/test_live_llm.py
HEURIVA_RUN_LIVE_SEARCH_TESTS=1 .venv/bin/pytest tests/live/test_live_search.py
```

A successful small `doctor --probe` confirms only the minimal protocol path. It
does not prove a full multi-step product run or search quality. Use
`--probe-timeout` when a local or Cursor-compatible model needs more than the
default quick probe timeout to return its first token.

The pytest live smoke files are opt-in and remain skipped by default.
