from __future__ import annotations

import json
import sys
from collections.abc import Callable
from functools import partial
from typing import Annotated

import typer

from heuriva import __version__
from heuriva.clients.model import ModelClient
from heuriva.clients.search import SearchClient
from heuriva.config import api_key_for, load_config, setup_config
from heuriva.controller.llm_controller import LLMController
from heuriva.core.operator import Operator
from heuriva.core.task_contract import SearchPolicy
from heuriva.diagnostics import collect_diagnostics
from heuriva.evaluation import evaluate_trajectory, render_suite_report, run_eval_suite
from heuriva.executors.llm import LLMExecutor
from heuriva.executors.search import SearchExecutor
from heuriva.runtime.engine import Executor, RuntimeEngine, RuntimeInterrupted, RuntimeProgress
from heuriva.storage.sqlite import SQLiteStore
from heuriva.trace import render_saved_trajectory

app = typer.Typer(add_completion=False, invoke_without_command=True)
DOCTOR_CONNECT_TIMEOUT_SECONDS = 1.0
DOCTOR_READ_TIMEOUT_SECONDS = 2.0
MAX_DOCTOR_PROBE_TIMEOUT_SECONDS = 600.0


@app.callback(invoke_without_command=True)
def root(
    ctx: typer.Context,
    trace: Annotated[bool, typer.Option("--trace", help="Show detailed per-step trace.")] = False,
    version: Annotated[bool, typer.Option("--version", help="Show version and exit.")] = False,
) -> None:
    if version:
        typer.echo(f"Heuriva {__version__}")
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        _repl(trace=trace)


@app.command()
def setup(
    force: Annotated[
        bool, typer.Option("--force", help="Overwrite existing local config.")
    ] = False,
) -> None:
    result = setup_config(force=force)
    if result.created_config:
        typer.echo(f"Created {result.config_path}", err=True)
    else:
        typer.echo(f"Kept existing {result.config_path}", err=True)
    if result.created_env:
        typer.echo(f"Created {result.env_path}", err=True)
    else:
        typer.echo(f"Kept existing {result.env_path}", err=True)
    typer.echo(
        "Default endpoint is Cursor-compatible local auto: http://localhost:8765/v1",
        err=True,
    )
    typer.echo(
        "Model requests may leave this machine; search sends queries to a "
        "third-party search provider.",
        err=True,
    )


@app.command()
def doctor(
    probe: Annotated[bool, typer.Option("--probe", help="Send a minimal chat completion.")] = False,
    probe_timeout: Annotated[
        float | None,
        typer.Option(
            "--probe-timeout",
            min=0.1,
            max=MAX_DOCTOR_PROBE_TIMEOUT_SECONDS,
            help="Override the doctor probe read timeout in seconds.",
        ),
    ] = None,
) -> None:
    try:
        config = load_config()
    except Exception as exc:
        typer.echo(f"Configuration error: {exc}", err=True)
        raise typer.Exit(2) from exc
    typer.echo(f"Version: {__version__}", err=True)
    for line in collect_diagnostics(config).lines():
        typer.echo(line, err=True)
    read_timeout_seconds = probe_timeout or min(
        config.llm.read_timeout_seconds, DOCTOR_READ_TIMEOUT_SECONDS
    )
    if probe or probe_timeout is not None:
        typer.echo(f"Probe timeout: {read_timeout_seconds:g}s", err=True)
    model_client = ModelClient(
        base_url=config.llm.base_url,
        model=config.llm.model,
        api_key=api_key_for(config),
        connect_timeout_seconds=min(
            config.llm.connect_timeout_seconds, DOCTOR_CONNECT_TIMEOUT_SECONDS
        ),
        read_timeout_seconds=read_timeout_seconds,
        max_retries=config.llm.max_retries,
    )
    try:
        ok, message = model_client.models_probe()
        level = "ok" if ok else "warning"
        typer.echo(f"Models endpoint: {level} ({message})", err=True)
        if probe:
            response = model_client.chat([{"role": "user", "content": "Reply with ok."}])
            typer.echo(f"Chat probe: ok ({len(response.content)} chars)", err=True)
    except Exception as exc:
        typer.echo(f"Model probe warning: {exc.__class__.__name__}: {exc}", err=True)
    finally:
        model_client.close()


@app.command()
def run(
    task: Annotated[list[str], typer.Argument(help="Task text.")],
    trace: Annotated[bool, typer.Option("--trace", help="Show detailed trace.")] = False,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
    progress_output: Annotated[
        bool, typer.Option("--progress/--no-progress", help="Show live progress on stderr.")
    ] = True,
    criterion: Annotated[
        list[str] | None,
        typer.Option("--criterion", help="Stable task-level completion criterion."),
    ] = None,
    search_policy: Annotated[
        SearchPolicy,
        typer.Option(
            "--search-policy",
            case_sensitive=False,
            help="Task-level search policy: auto, required, or forbidden.",
        ),
    ] = SearchPolicy.AUTO,
) -> None:
    text = " ".join(task).strip()
    if not text:
        typer.echo("Task must not be empty.", err=True)
        raise typer.Exit(2)
    try:
        engine = _build_engine()
        progress: Callable[[RuntimeProgress], None] | None = None
        if progress_output:
            progress = partial(_emit_progress, json_output=json_output)
        result = engine.run(
            text,
            trace=trace,
            progress=progress,
            criteria=tuple(criterion or ()),
            search_policy=search_policy,
        )
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    except RuntimeInterrupted as exc:
        typer.echo(
            "Interrupted. "
            f"task_id={exc.task_id}; "
            f"use `heuriva show --trace {exc.task_id}` to inspect saved trajectory",
            err=True,
        )
        raise typer.Exit(130) from exc
    except KeyboardInterrupt as exc:
        typer.echo("Interrupted.", err=True)
        raise typer.Exit(130) from exc
    except Exception as exc:
        typer.echo(f"Runtime failed: {exc}", err=True)
        raise typer.Exit(3) from exc
    if json_output:
        typer.echo(
            json.dumps(
                {
                    "task_id": result.task_id,
                    "status": result.status,
                    "final_answer": result.final_answer,
                },
                ensure_ascii=False,
            )
        )
    else:
        for line in result.trace_lines:
            typer.echo(line, err=True)
        if result.final_answer:
            typer.echo(result.final_answer)
        else:
            typer.echo(f"Task {result.task_id} ended with status {result.status}", err=True)
    if result.status != "done":
        raise typer.Exit(3)


def _emit_progress(event: RuntimeProgress, *, json_output: bool) -> None:
    message = event.message
    if json_output and event.stage == "task_started" and "stdout" not in message:
        message = f"{message}; final JSON will be printed on stdout"
    operator = f" {event.operator}" if event.operator else ""
    typer.echo(
        f"[{event.task_id[:8]} step {event.step_index} +{event.elapsed_seconds:.1f}s]"
        f" {event.stage}{operator}: {message}",
        err=True,
    )


@app.command()
def show(
    task_id: Annotated[str, typer.Argument(help="Task ID to inspect.")],
    trace: Annotated[bool, typer.Option("--trace", help="Show observations and events.")] = False,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    try:
        config = load_config()
        data = SQLiteStore(config.storage.sqlite_path).get_trajectory(task_id)
    except KeyError as exc:
        typer.echo(f"Task not found: {task_id}", err=True)
        raise typer.Exit(4) from exc
    except Exception as exc:
        typer.echo(f"Could not read task: {exc}", err=True)
        raise typer.Exit(3) from exc
    if json_output:
        typer.echo(json.dumps(data, ensure_ascii=False))
    else:
        typer.echo(render_saved_trajectory(data, trace=trace))


@app.command(name="eval")
def eval_task(
    task_id: Annotated[str, typer.Argument(help="Task ID to evaluate.")],
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
    judge: Annotated[
        bool,
        typer.Option(
            "--judge",
            help="Reserved for fresh model judging; not used by the default read-only eval.",
        ),
    ] = False,
) -> None:
    if judge:
        typer.echo(
            "--judge is reserved for future fresh model assessment; default eval is read-only.",
            err=True,
        )
    try:
        config = load_config()
        data = SQLiteStore(config.storage.sqlite_path).get_trajectory(task_id)
    except KeyError as exc:
        typer.echo(f"Task not found: {task_id}", err=True)
        raise typer.Exit(4) from exc
    except Exception as exc:
        typer.echo(f"Could not read task: {exc}", err=True)
        raise typer.Exit(3) from exc
    report = evaluate_trajectory(data)
    if json_output:
        typer.echo(json.dumps(report.to_dict(), ensure_ascii=False))
        return
    typer.echo(f"task_id: {report.task_id}")
    typer.echo(f"status: {report.status}")
    typer.echo(f"evidence_level: {report.evidence_level}")
    typer.echo(f"search_steps: {report.search_steps}")
    typer.echo(f"search_guards: {report.search_guard_count}")
    typer.echo(f"raw_candidates: {report.raw_candidate_count}")
    typer.echo(f"accepted_evidence: {report.accepted_evidence_count}")
    typer.echo(f"rejected_candidates: {report.rejected_candidate_count}")
    typer.echo(f"citation_validation: {report.citation_validation}")
    typer.echo(f"completion_verdict: {report.completion_verdict}")
    if report.failed_criteria:
        typer.echo(f"failed_criteria: {', '.join(report.failed_criteria)}")


@app.command(name="eval-suite")
def eval_suite(
    corpus: Annotated[
        str | None,
        typer.Option("--corpus", help="Path to a versioned eval corpus YAML file."),
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
    include_fresh_live: Annotated[
        bool,
        typer.Option(
            "--include-fresh-live",
            help=(
                "Opt into fresh_live corpus cases. "
                "Also enabled by HEURIVA_EVAL_SUITE_FRESH_LIVE=1."
            ),
        ),
    ] = False,
    db_path: Annotated[
        str | None,
        typer.Option(
            "--db",
            help="SQLite path for stored_live summaries. Defaults to configured storage.",
        ),
    ] = None,
) -> None:
    try:
        sqlite_path = db_path
        if sqlite_path is None:
            sqlite_path = str(load_config().storage.sqlite_path)
        report = run_eval_suite(
            corpus_path=corpus,
            sqlite_path=sqlite_path,
            # None lets HEURIVA_EVAL_SUITE_FRESH_LIVE enable opt-in when the
            # CLI flag is absent; True forces opt-in when the flag is present.
            include_fresh_live=True if include_fresh_live else None,
        )
    except Exception as exc:
        typer.echo(f"Eval suite failed: {exc}", err=True)
        raise typer.Exit(3) from exc
    if json_output:
        typer.echo(json.dumps(report.to_dict(), ensure_ascii=False))
    else:
        typer.echo(render_suite_report(report))
    failed = report.totals_by_status.get("fail", 0)
    if failed:
        raise typer.Exit(1)


def _build_engine() -> RuntimeEngine:
    config = load_config()
    store = SQLiteStore(config.storage.sqlite_path)
    model_client = ModelClient(
        base_url=config.llm.base_url,
        model=config.llm.model,
        api_key=api_key_for(config),
        connect_timeout_seconds=config.llm.connect_timeout_seconds,
        read_timeout_seconds=config.llm.read_timeout_seconds,
        max_retries=config.llm.max_retries,
    )
    controller = LLMController(
        model_client=model_client,
        repair_attempts=config.runtime.controller_repair_attempts,
    )
    executors: dict[Operator, Executor] = {
        Operator.ANALYZE: LLMExecutor(model_client=model_client),
        Operator.ANSWER: LLMExecutor(model_client=model_client),
    }
    if config.tools.search.enabled:
        executors[Operator.SEARCH] = SearchExecutor(
            search_client=SearchClient(
                max_results=config.tools.search.max_results,
                timeout_seconds=config.tools.search.timeout_seconds,
            ),
            quality_config=config.quality,
        )
    return RuntimeEngine(config=config, store=store, controller=controller, executors=executors)


def _repl(*, trace: bool) -> None:
    typer.echo("Heuriva interactive mode. Type :quit or press Ctrl-D to exit.", err=True)
    while True:
        try:
            line = input("heuriva> ")
        except EOFError:
            typer.echo("", err=True)
            return
        if line.strip() in {":quit", ":exit"}:
            return
        if not line.strip():
            continue
        sys.argv = [sys.argv[0], "run", line]
        try:
            engine = _build_engine()
            result = engine.run(line, trace=trace)
            for trace_line in result.trace_lines:
                typer.echo(trace_line, err=True)
            if result.final_answer:
                typer.echo(result.final_answer)
            else:
                typer.echo(f"Task {result.task_id} ended with status {result.status}", err=True)
        except Exception as exc:
            typer.echo(f"Task failed: {exc}", err=True)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
