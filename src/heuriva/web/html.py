"""Minimal HTML rendering for the local trajectory browser."""

from __future__ import annotations

import html
import json
from typing import Any

from heuriva.web.queries import TaskDetail, TaskListItem


def _esc(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _page(title: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>{_esc(title)}</title>
  <style>
    :root {{
      --bg: #f4f6f8;
      --fg: #1b1f24;
      --muted: #59636e;
      --line: #d0d7de;
      --card: #ffffff;
      --accent: #0550ae;
      --warn: #9a6700;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font: 15px/1.5 ui-sans-serif, system-ui, -apple-system, sans-serif;
      color: var(--fg);
      background: var(--bg);
    }}
    header {{
      padding: 1rem 1.25rem;
      border-bottom: 1px solid var(--line);
      background: var(--card);
    }}
    header h1 {{
      margin: 0;
      font-size: 1.15rem;
      font-weight: 650;
    }}
    header p {{
      margin: 0.35rem 0 0;
      color: var(--muted);
      font-size: 0.9rem;
    }}
    main {{
      max-width: 960px;
      margin: 0 auto;
      padding: 1.25rem;
    }}
    a {{ color: var(--accent); text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .banner {{
      margin: 0 0 1rem;
      padding: 0.75rem 0.9rem;
      border: 1px solid #ffe8a3;
      background: #fff8c5;
      color: var(--warn);
      font-size: 0.9rem;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      background: var(--card);
      border: 1px solid var(--line);
    }}
    th, td {{
      padding: 0.55rem 0.7rem;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
    }}
    th {{
      font-size: 0.8rem;
      text-transform: uppercase;
      letter-spacing: 0.03em;
      color: var(--muted);
      background: #fafbfc;
    }}
    .mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.85rem; }}
    .muted {{ color: var(--muted); }}
    .section {{
      margin: 1.25rem 0;
      padding: 1rem;
      background: var(--card);
      border: 1px solid var(--line);
    }}
    .section h2 {{
      margin: 0 0 0.75rem;
      font-size: 1rem;
    }}
    .kv {{
      display: grid;
      grid-template-columns: 11rem 1fr;
      gap: 0.35rem 0.75rem;
      margin: 0;
    }}
    .kv dt {{ color: var(--muted); }}
    .kv dd {{ margin: 0; word-break: break-word; }}
    pre {{
      margin: 0;
      padding: 0.75rem;
      overflow: auto;
      background: #f6f8fa;
      border: 1px solid var(--line);
      font-size: 0.85rem;
      white-space: pre-wrap;
    }}
    .empty {{ color: var(--muted); font-style: italic; }}
    nav.crumbs {{ margin-bottom: 1rem; font-size: 0.9rem; }}
  </style>
</head>
<body>
  <header>
    <h1>Heuriva Trajectory Browser</h1>
    <p>Local read-only inspector — not a remote dashboard. No model calls from this UI.</p>
  </header>
  <main>
    {body}
  </main>
</body>
</html>
"""


def render_task_list(tasks: tuple[TaskListItem, ...], *, db_path: str) -> str:
    rows = []
    for task in tasks:
        rows.append(
            "<tr>"
            f'<td class="mono"><a href="/tasks/{_esc(task.task_id)}">'
            f"{_esc(task.task_id[:8])}…</a></td>"
            f"<td>{_esc(task.status)}</td>"
            f"<td>{task.step_count}</td>"
            f"<td>{_esc(task.goal_summary)}</td>"
            f'<td class="muted mono">{_esc(task.updated_at)}</td>'
            "</tr>"
        )
    body_rows = (
        "".join(rows)
        if rows
        else '<tr><td colspan="5" class="empty">No tasks in this database.</td></tr>'
    )
    body = f"""
    <p class="banner">Read-only view of <span class="mono">{_esc(db_path)}</span>.
    Opening pages does not rewrite trajectory steps or call the model.</p>
    <div class="section">
      <h2>Tasks</h2>
      <table>
        <thead>
          <tr><th>Task</th><th>Status</th><th>Steps</th><th>Goal</th><th>Updated</th></tr>
        </thead>
        <tbody>{body_rows}</tbody>
      </table>
    </div>
    """
    return _page("Heuriva tasks", body)


def render_task_detail(detail: TaskDetail, *, db_path: str) -> str:
    contract_block = (
        f"<pre>{_esc(json.dumps(detail.task_contract, ensure_ascii=False, indent=2))}</pre>"
        if detail.task_contract
        else '<p class="empty">No task_contract on stored states.</p>'
    )
    assessment_block = (
        f"<pre>{_esc(json.dumps(detail.completion_assessment, ensure_ascii=False, indent=2))}</pre>"
        if detail.completion_assessment
        else '<p class="empty">No completion_assessment stored.</p>'
    )
    step_rows = []
    for step in detail.steps:
        step_rows.append(
            "<tr>"
            f"<td>{step.step_index}</td>"
            f'<td class="mono">{_esc(step.operator)}</td>'
            f"<td>{_esc(step.objective)}</td>"
            f"<td>{_esc(step.observation_summary)}</td>"
            f"<td>{_esc(step.observation_status)}</td>"
            "</tr>"
        )
    steps_html = (
        "".join(step_rows) if step_rows else '<tr><td colspan="5" class="empty">No steps.</td></tr>'
    )
    eval_rows = []
    for run in detail.eval_runs:
        eval_rows.append(
            "<tr>"
            f'<td class="mono">{_esc(run.eval_run_id)}</td>'
            f"<td>{_esc(run.disagreement_bucket)}</td>"
            f"<td>{_esc(run.deterministic_verdict)} / {_esc(run.judge_verdict)}</td>"
            f"<td>{_esc(run.model)}</td>"
            f'<td class="muted mono">{_esc(run.created_at)}</td>'
            "</tr>"
        )
    eval_html = (
        "".join(eval_rows)
        if eval_rows
        else '<tr><td colspan="5" class="empty">No eval_runs for this task.</td></tr>'
    )
    failed = ", ".join(detail.failed_criteria) if detail.failed_criteria else "—"
    body = f"""
    <nav class="crumbs"><a href="/">← Tasks</a></nav>
    <p class="banner">{_esc(detail.disclaimer)}</p>
    <div class="section">
      <h2>Task</h2>
      <dl class="kv">
        <dt>task_id</dt><dd class="mono">{_esc(detail.task_id)}</dd>
        <dt>status</dt><dd>{_esc(detail.status)}</dd>
        <dt>db</dt><dd class="mono">{_esc(db_path)}</dd>
        <dt>goal</dt><dd>{_esc(detail.goal)}</dd>
        <dt>final_answer</dt><dd>{_esc(detail.final_answer or "—")}</dd>
        <dt>termination</dt><dd>{_esc(detail.termination_reason or "—")}</dd>
        <dt>citation</dt><dd>{_esc(detail.citation_validation)}</dd>
        <dt>completion</dt><dd>{_esc(detail.completion_verdict)}</dd>
        <dt>failed_criteria</dt><dd>{_esc(failed)}</dd>
      </dl>
    </div>
    <div class="section">
      <h2>Task contract</h2>
      {contract_block}
    </div>
    <div class="section">
      <h2>Completion assessment</h2>
      {assessment_block}
    </div>
    <div class="section">
      <h2>Steps</h2>
      <table>
        <thead>
          <tr><th>#</th><th>Operator</th><th>Objective</th><th>Observation</th><th>Status</th></tr>
        </thead>
        <tbody>{steps_html}</tbody>
      </table>
    </div>
    <div class="section">
      <h2>Eval runs (read-only)</h2>
      <table>
        <thead>
          <tr>
            <th>eval_run_id</th>
            <th>Disagreement</th>
            <th>Det / Judge</th>
            <th>Model</th>
            <th>Created</th>
          </tr>
        </thead>
        <tbody>{eval_html}</tbody>
      </table>
    </div>
    """
    return _page(f"Task {detail.task_id[:8]}", body)


def render_not_found(message: str) -> str:
    body = f"""
    <nav class="crumbs"><a href="/">← Tasks</a></nav>
    <div class="section">
      <h2>Not found</h2>
      <p>{_esc(message)}</p>
    </div>
    """
    return _page("Not found", body)


def render_error(message: str) -> str:
    body = f"""
    <div class="section">
      <h2>Error</h2>
      <p>{_esc(message)}</p>
    </div>
    """
    return _page("Error", body)


def wants_json(headers: Any, query: dict[str, list[str]]) -> bool:
    if query.get("format", [""])[0].lower() == "json":
        return True
    accept = str(getattr(headers, "get", lambda _k, _d="": "")("Accept", "") or "")
    return "application/json" in accept and "text/html" not in accept
