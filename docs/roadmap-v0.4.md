# Heuriva v0.4 Roadmap

**Last Updated:** 2026-08-25
**v0.3 Status:** accepted

## v0.3 Acceptance Position

v0.3 is accepted for the scoped CLI runtime quality loop:

- Automated verification passes: `65 passed, 2 skipped`, 87% coverage.
- Local build passes for the `0.3.0` sdist and wheel.
- Live acceptance records cover protocol readiness, required Web evidence,
  citation validation, read-only `heuriva eval`, and observed completion verdicts.
- Forced fake-integration tests cover runtime branches that a cooperative live
  model did not naturally trigger: forbidden-search guard and enforce-mode
  completion blocking/repair.

This acceptance does not mean model answers are objectively correct. It means
the v0.3 runtime surfaces task contracts, evidence accounting, citations,
completion verdicts, and provenance in a reproducible way.

## Current Structure

Heuriva is currently a small Python CLI runtime with three operators:
`ANALYZE`, `SEARCH`, and `ANSWER`.

| Area | Main Files | Responsibility |
| --- | --- | --- |
| CLI | `src/heuriva/cli.py` | Typer commands: setup, doctor, run, show, eval |
| Config | `src/heuriva/config.py` | YAML/env loading, defaults, redacted snapshots |
| Clients | `src/heuriva/clients/model.py`, `src/heuriva/clients/search.py` | OpenAI-compatible chat client and Web search client |
| Controller | `src/heuriva/controller/llm_controller.py` | Model-driven operator selection with structured JSON repair |
| Core Schemas | `src/heuriva/core/*` | Immutable state, decisions, observations, events, task contracts, evaluation models |
| Runtime | `src/heuriva/runtime/*` | Loop orchestration, guards, validation, state updates, deltas |
| Executors | `src/heuriva/executors/*` | LLM execution for ANALYZE/ANSWER and search execution for SEARCH |
| Storage | `src/heuriva/storage/sqlite.py` | SQLite trajectory persistence and schema versioning |
| Evaluation | `src/heuriva/evaluation.py` | Read-only stored trajectory summary for `heuriva eval` |
| Trace | `src/heuriva/trace.py` | Human-readable trajectory rendering |
| Tests | `tests/*` | Unit, fake-integration, live opt-in, and v0.3 quality tests |

The main flow is:

```text
CLI/config
  -> RuntimeEngine
  -> CognitiveState + TaskContract
  -> LLMController selects operator
  -> ExecutorRouter resolves executor
  -> executor returns OperationResult
  -> citation/search/completion validation
  -> StateUpdater creates next immutable state
  -> SQLiteStore commits trajectory step
  -> show/eval read saved trajectory
```

## Remaining Gaps

The important missing pieces are evaluation infrastructure, not another
operator.

- No durable known-good/known-bad corpus for task completion and relevance.
- No first-class `eval-suite` runner across saved, fake, and live cases.
- No cross-task evaluation table or trend report.
- `heuriva eval --judge` is reserved only; fresh model judging is not
  implemented.
- Completion and relevance assessment remain deterministic and shallow by
  design; they are not semantic judges.
- `completion_check_mode=enforce` should remain opt-in until corpus evidence
  shows acceptable false-positive and false-negative rates.
- Live acceptance is currently manually recorded in ignored local checklist
  files.
- No replay, resume, policy lifecycle, vector database, dashboard, MCP,
  shell/filesystem/Python executors, multi-agent workflow, URL crawling, daemon,
  or queue system.

## Recommended v0.4 Scope

v0.4 should make evaluation reproducible across cases before expanding runtime
capability.

### 1. Eval Corpus

Add a versioned corpus format for known-good and known-bad tasks.

Acceptance:

- Corpus entries identify prompt, task contract, evidence level, expected
  signals, and expected verdicts.
- Cases distinguish synthetic, fake-integration, stored-live, and fresh-live
  evidence.
- Corpus fixtures never imply that fake or model-judged results are product
  proof.

### 2. Eval Suite Runner

Add a CLI command that runs or summarizes the corpus without mutating unrelated
state.

Acceptance:

- `heuriva eval-suite` can run deterministic and fake-integration cases locally.
- Stored-live cases can be summarized from saved task IDs.
- Fresh-live cases are opt-in and clearly labeled.
- Output supports both human-readable and JSON formats.

### 3. Evidence and Completion Reports

Turn v0.3 signals into aggregate reports.

Acceptance:

- Reports include pass/fail/insufficient evidence, raw/accepted/rejected counts,
  search guard counts, citation validation, completion verdicts, parse warnings,
  and evidence level.
- Reports separate deterministic, stored model assessment, fake integration, and
  fresh live evidence.
- Reports never call a model unless an explicit future judging mode is requested.

### 4. Acceptance Harness

Move fragile manual checklist steps into repeatable commands where possible.

Acceptance:

- Forced runtime branches such as forbidden-search guard and enforce repair have
  stable tests or harness cases.
- Live endpoint probes remain opt-in.
- Machine-specific task IDs stay out of committed public docs.

### 5. Promotion Rules

Define when quality modes may move from observe to enforce.

Acceptance:

- Promotion requires corpus evidence, not intuition.
- False positives and false negatives are recorded separately.
- `VERIFY` remains deferred unless the corpus shows repeated cases where
  citations pass but task completion still fails.

## Not Recommended for v0.4

- Do not add a standalone `VERIFY` operator yet.
- Do not add vector memory, dashboard, MCP, multi-agent workflows, crawling, or
  shell/filesystem/Python executors before the evaluation layer is stable.
- Do not claim semantic correctness from deterministic completion checks.
- Do not turn `eval --judge` on by default.

## v0.4 Success Criteria

- A contributor can run one command to reproduce the deterministic/fake
  evaluation set.
- Stored live cases can be summarized without replaying tasks.
- Fresh live cases are opt-in and clearly labeled as live evidence.
- v0.3 quality signals can be compared across tasks.
- The project has enough evidence to decide whether any quality mode can safely
  become stricter.
