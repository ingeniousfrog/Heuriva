"""Session UI HTML — Perplexity-inspired layout with i18n and activity feed."""

from __future__ import annotations

import html
import json
from typing import Any

from heuriva import __version__
from heuriva.web.queries import TaskDetail, TaskListItem


def _esc(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _shell(title: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <link rel="icon" href="/static/favicon.png" type="image/png"/>
  <link rel="apple-touch-icon" href="/static/apple-touch-icon.png"/>
  <title>{_esc(title)}</title>
  <style>
{_CSS}
  </style>
</head>
<body>
  {body}
  <div id="session-toast" class="toast" aria-live="polite"></div>
  <script>
{_JS}
  </script>
</body>
</html>
"""


_CSS = """
:root {
  --bg: #f7f7f4;
  --surface: #ffffff;
  --ink: #1b1b18;
  --muted: #6b6b65;
  --line: #e4e4de;
  --accent: #127681;
  --accent-hover: #0f636d;
  --accent-soft: rgba(18, 118, 129, 0.1);
  --danger: #b42318;
  --ok: #067647;
  --shadow: 0 8px 32px rgba(27, 27, 24, 0.06);
  --radius: 16px;
  --font: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Hiragino Sans GB", sans-serif;
  --mono: "SF Mono", ui-monospace, Menlo, monospace;
}
* { box-sizing: border-box; }
html, body { margin: 0; min-height: 100%; }
body {
  font: 15px/1.5 var(--font);
  color: var(--ink);
  background: radial-gradient(1200px 600px at 50% -20%, #eef6f6 0%, var(--bg) 55%);
}
.page {
  max-width: 760px;
  margin: 0 auto;
  padding: 1.25rem 1rem 3rem;
}
.topbar {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  margin-bottom: 2rem;
}
.logo {
  display: flex;
  align-items: center;
  gap: 0.55rem;
  font-weight: 650;
  font-size: 1.05rem;
  letter-spacing: -0.02em;
  text-decoration: none;
  color: inherit;
}
.logo-mark {
  width: 28px;
  height: 28px;
  border-radius: 8px;
  object-fit: cover;
  display: block;
}
.topbar-spacer { flex: 1; }
.topbar-actions { display: flex; align-items: center; gap: 0.35rem; }
.lang-toggle {
  display: inline-flex;
  border: 1px solid var(--line);
  border-radius: 999px;
  overflow: hidden;
  background: var(--surface);
}
.lang-toggle button {
  border: 0;
  background: transparent;
  padding: 0.35rem 0.65rem;
  font: inherit;
  font-size: 0.82rem;
  color: var(--muted);
  cursor: pointer;
}
.lang-toggle button.active {
  background: var(--accent-soft);
  color: var(--accent);
  font-weight: 600;
}
.icon-btn {
  border: 1px solid var(--line);
  background: var(--surface);
  border-radius: 10px;
  width: 2.1rem;
  height: 2.1rem;
  cursor: pointer;
  color: var(--muted);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  line-height: 0;
}
.icon-btn:hover { border-color: #ccc; color: var(--ink); }
.icon-btn svg {
  display: block;
  width: 1rem;
  height: 1rem;
  flex-shrink: 0;
}
.hero { text-align: center; margin-bottom: 1.25rem; }
.hero h1 {
  margin: 0;
  font-size: clamp(1.6rem, 4vw, 2rem);
  font-weight: 650;
  letter-spacing: -0.03em;
}
.hero p {
  margin: 0.45rem 0 0;
  color: var(--muted);
  font-size: 0.95rem;
}
.chip {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  margin-top: 0.65rem;
  font-size: 0.78rem;
  color: var(--muted);
}
.chip::before {
  content: "";
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--accent);
}
.composer-card {
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  padding: 0.85rem;
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.composer-card textarea {
  width: 100%;
  flex: 1 1 auto;
  min-height: 5.5rem;
  border: 0;
  resize: vertical;
  font: inherit;
  font-size: 1.05rem;
  line-height: 1.45;
  outline: none;
  background: transparent;
  color: var(--ink);
  transition: background 0.2s ease, color 0.2s ease;
}
.composer-card.is-running {
  border-color: rgba(18, 118, 129, 0.35);
  box-shadow: 0 0 0 3px var(--accent-soft), var(--shadow);
}
.composer-card.is-running textarea {
  background: var(--accent-soft);
  color: var(--ink);
  border-radius: 10px;
  padding: 0.55rem 0.65rem;
  resize: none;
  cursor: default;
}
.composer-toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 0.65rem;
  align-items: end;
  margin-top: 0.65rem;
  padding-top: 0.65rem;
  border-top: 1px solid var(--line);
  flex-shrink: 0;
}
.field { flex: 1 1 8rem; min-width: 0; }
.field label {
  display: block;
  font-size: 0.72rem;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--muted);
  margin-bottom: 0.3rem;
}
.field input, .field select {
  width: 100%;
  border: 1px solid var(--line);
  border-radius: 10px;
  padding: 0.5rem 0.65rem;
  font: inherit;
  background: var(--bg);
}
button, .btn {
  border: 0;
  border-radius: 10px;
  padding: 0.55rem 1rem;
  font: 600 0.92rem/1 var(--font);
  cursor: pointer;
  background: var(--accent);
  color: #fff;
}
button:hover { background: var(--accent-hover); }
button:disabled { opacity: 0.5; cursor: not-allowed; }
button.secondary, .btn.secondary {
  background: var(--surface);
  color: var(--ink);
  border: 1px solid var(--line);
}
button.secondary:hover { background: var(--bg); }
button.danger { background: var(--danger); }
.layout-split {
  display: grid;
  grid-template-columns: 1fr;
  gap: 1rem;
  margin-top: 1rem;
  align-items: stretch;
  height: 22rem;
}
.layout-split > * {
  min-height: 0;
  height: 100%;
  overflow: hidden;
}
.layout-split > div {
  display: flex;
  flex-direction: column;
  min-height: 0;
}
.layout-split > div > .composer-card {
  flex: 1;
  min-height: 0;
}
@media (min-width: 900px) {
  .page { max-width: 980px; }
  .layout-split {
    grid-template-columns: 1fr 1fr;
    height: 24rem;
  }
}
.activity-panel {
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  height: 100%;
  min-height: 0;
  max-height: 100%;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.activity-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 0.5rem;
  padding: 0.65rem 0.85rem;
  border-bottom: 1px solid var(--line);
  font-size: 0.82rem;
  font-weight: 600;
  flex-shrink: 0;
}
.activity-head span.sub { color: var(--muted); font-weight: 400; }
.activity-actions {
  display: flex;
  align-items: center;
  gap: 0.35rem;
}
.activity-actions button {
  padding: 0.25rem 0.55rem;
  font-size: 0.75rem;
}
.activity-feed {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overscroll-behavior: contain;
  padding: 0.5rem 0.75rem;
  font-family: var(--mono);
  font-size: 0.78rem;
  line-height: 1.55;
}
.activity-line {
  padding: 0.2rem 0;
  color: var(--ink);
  animation: fade-in 0.25s ease;
}
.activity-line .ts { color: var(--muted); margin-right: 0.45rem; }
.activity-line .stage { color: var(--accent); margin-right: 0.35rem; }
.activity-empty {
  color: var(--muted);
  font-family: var(--font);
  font-size: 0.88rem;
  padding: 1rem 0.25rem;
}
.recent-drawer {
  margin-top: 1rem;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--surface);
  overflow: hidden;
}
.recent-drawer summary {
  list-style: none;
  cursor: pointer;
  padding: 0.75rem 0.9rem;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem 0.75rem;
  font-weight: 600;
  user-select: none;
}
.recent-drawer summary::-webkit-details-marker { display: none; }
.recent-drawer summary::before {
  content: "▸";
  color: var(--muted);
  transition: transform 0.15s ease;
}
.recent-drawer[open] summary::before { transform: rotate(90deg); }
.recent-drawer .db { font-family: var(--mono); font-size: 0.75rem; color: var(--muted); font-weight: 400; }
.recent-drawer .count { color: var(--muted); font-weight: 500; }
.recent-body { border-top: 1px solid var(--line); padding: 0.25rem 0; }
.task-list { list-style: none; margin: 0; padding: 0; }
.task-list li { border-bottom: 1px solid var(--line); }
.task-list li:last-child { border-bottom: 0; }
.task-list a {
  display: grid;
  grid-template-columns: 4.5rem 5.5rem 2.5rem 1fr;
  gap: 0.5rem;
  padding: 0.65rem 0.9rem;
  text-decoration: none;
  color: inherit;
  font-size: 0.88rem;
}
.task-list a:hover { background: var(--bg); }
.mono { font-family: var(--mono); font-size: 0.82rem; }
.muted { color: var(--muted); }
.status-pill { color: var(--accent); font-size: 0.8rem; }
.goal-cell { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.empty { color: var(--muted); font-style: italic; padding: 0.75rem 0.9rem; }
.banner {
  padding: 0.75rem 0.9rem;
  border-radius: var(--radius);
  background: #fff8e8;
  color: #8a5a12;
  font-size: 0.9rem;
  margin-bottom: 1rem;
}
.settings-modal {
  position: fixed;
  inset: 0;
  z-index: 40;
  display: none;
  align-items: flex-start;
  justify-content: center;
  padding: 4.5rem 1rem 1.5rem;
  background: rgba(27, 27, 24, 0.28);
}
.settings-modal.open { display: flex; }
.settings-dialog {
  width: min(26rem, 100%);
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  padding: 1rem 1.1rem 1.1rem;
}
.settings-dialog-head {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-bottom: 0.9rem;
}
.settings-dialog-head h2 {
  margin: 0;
  flex: 1;
  font-size: 1rem;
  font-weight: 650;
}
.settings-form .field { margin-bottom: 0.75rem; }
.settings-form label {
  display: block;
  font-size: 0.72rem;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--muted);
  margin-bottom: 0.3rem;
}
.settings-form input {
  width: 100%;
  border: 1px solid var(--line);
  border-radius: 10px;
  padding: 0.55rem 0.7rem;
  font: inherit;
  font-size: 0.9rem;
  background: var(--bg);
}
.settings-form input:focus {
  outline: none;
  border-color: rgba(18, 118, 129, 0.45);
  box-shadow: 0 0 0 3px var(--accent-soft);
}
.settings-form .hint {
  margin: 0.35rem 0 0;
  font-size: 0.78rem;
  color: var(--muted);
  line-height: 1.4;
}
.settings-actions {
  display: flex;
  justify-content: flex-end;
  gap: 0.5rem;
  margin-top: 1rem;
}
.about-modal {
  position: fixed;
  inset: 0;
  z-index: 40;
  display: none;
  align-items: flex-start;
  justify-content: center;
  padding: 4.5rem 1rem 1.5rem;
  background: rgba(27, 27, 24, 0.28);
}
.about-modal.open { display: flex; }
.about-dialog {
  width: min(28rem, 100%);
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  padding: 1rem 1.1rem 1.15rem;
}
.about-dialog-head {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-bottom: 0.75rem;
}
.about-dialog-head h2 {
  margin: 0;
  flex: 1;
  font-size: 1.05rem;
  font-weight: 650;
}
.about-body {
  font-size: 0.9rem;
  color: var(--ink);
  line-height: 1.55;
}
.about-body p { margin: 0 0 0.7rem; color: var(--muted); }
.about-body ul {
  margin: 0 0 0.85rem;
  padding-left: 1.15rem;
  color: var(--muted);
}
.about-body li { margin: 0.25rem 0; }
.about-meta {
  display: grid;
  gap: 0.45rem;
  margin: 0.25rem 0 0.85rem;
  padding: 0.7rem 0.8rem;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: var(--bg);
  font-size: 0.84rem;
}
.about-meta a { color: var(--accent); font-weight: 600; }
.about-meta .label { color: var(--muted); margin-right: 0.35rem; }
.about-meta code {
  font-family: var(--mono);
  font-size: 0.8rem;
  color: var(--ink);
}
.toast {
  position: fixed;
  right: 1rem;
  bottom: 1rem;
  max-width: min(24rem, calc(100vw - 2rem));
  padding: 0.75rem 1rem;
  background: var(--ink);
  color: #fff;
  border-radius: 10px;
  opacity: 0;
  transform: translateY(6px);
  pointer-events: none;
  transition: opacity 0.2s, transform 0.2s;
  z-index: 30;
  font-size: 0.88rem;
}
.toast.visible { opacity: 1; transform: translateY(0); }
.crumbs { margin-bottom: 1rem; font-size: 0.9rem; }
.crumbs a { color: var(--accent); text-decoration: none; }
.detail-hero h1 { margin: 0.35rem 0 0; font-size: 1.45rem; font-weight: 650; line-height: 1.25; }
.meta-row { display: flex; flex-wrap: wrap; gap: 0.5rem 0.85rem; margin-top: 0.65rem; color: var(--muted); font-size: 0.88rem; }
.actions { display: flex; flex-wrap: wrap; gap: 0.5rem; margin-top: 1rem; }
.panel, .answer-block {
  margin-top: 1rem;
  padding: 0.9rem 1rem;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius);
}
.panel h2, .answer-block h2 { margin: 0 0 0.6rem; font-size: 1rem; }
.answer-block .body { white-space: pre-wrap; word-break: break-word; }
.timeline { list-style: none; margin: 0; padding: 0; }
.timeline li { padding: 0.65rem 0; border-bottom: 1px solid var(--line); }
.timeline li:last-child { border-bottom: 0; }
.timeline .op { font-family: var(--mono); font-size: 0.78rem; color: var(--accent); }
.timeline .obs { color: var(--muted); font-size: 0.88rem; margin-top: 0.25rem; }
details.fold summary { cursor: pointer; color: var(--muted); font-size: 0.88rem; }
pre {
  margin: 0.5rem 0 0;
  padding: 0.65rem;
  background: var(--bg);
  border-radius: 8px;
  font-family: var(--mono);
  font-size: 0.78rem;
  white-space: pre-wrap;
  overflow: auto;
}
@keyframes fade-in {
  from { opacity: 0; transform: translateY(3px); }
  to { opacity: 1; transform: translateY(0); }
}
@media (max-width: 640px) {
  .task-list a {
    grid-template-columns: 4rem 1fr;
    grid-template-areas: "id status" "goal goal" "steps steps";
  }
  .task-list .col-id { grid-area: id; }
  .task-list .col-status { grid-area: status; justify-self: end; }
  .task-list .col-steps { grid-area: steps; }
  .task-list .col-goal { grid-area: goal; white-space: normal; }
}
"""

_JS = r"""
const HeurivaSession = (() => {
  const I18N = {
    en: {
      tagline: "Local cognitive workspace — ask, inspect trajectories, resume safely.",
      local_chip: "localhost session",
      new_task: "Ask Heuriva",
      goal_ph: "What should Heuriva solve?",
      criterion: "Criterion (optional)",
      criterion_ph: "e.g. mention tradeoffs",
      search: "Search",
      run: "Run",
      activity: "Activity",
      activity_hint: "live progress",
      activity_empty: "Progress lines appear here while a task runs.",
      clear_log: "Clear",
      interrupt: "Interrupt",
      interrupting: "Interrupting…",
      interrupt_sent: "Interrupt requested",
      interrupt_idle: "Nothing is running.",
      interrupted_ready: "Interrupted — edit or resume.",
      recent: "Recent tasks",
      no_tasks: "No tasks yet.",
      settings_title: "Settings",
      settings_base_url: "LLM base URL",
      settings_base_url_hint: "OpenAI-compatible endpoint. Saved to ~/.heuriva/config.yaml for the next run.",
      settings_model: "Model",
      save: "Save",
      saving: "Saving…",
      cancel: "Cancel",
      settings_saved: "Saved — applies to the next run.",
      settings_open: "Open settings",
      settings_close: "Close settings",
      about_title: "About Heuriva",
      about_open: "About",
      about_close: "Close about",
      about_blurb: "A local-first workspace for language-model tasks with explicit steps, inspectable SQLite trajectories, and safe resume.",
      about_guidelines_title: "Guidelines",
      about_g1: "Localhost-only Session UI — not a remote multi-tenant dashboard.",
      about_g2: "Each step picks one operator: ANALYZE, SEARCH, or ANSWER.",
      about_g3: "Trajectory history is append-only; resume never rewrites past steps.",
      about_g4: "No default VERIFY / semantic enforce; quality checks stay observe unless you opt in.",
      about_github: "GitHub",
      about_license: "License",
      about_port: "Session port",
      about_port_hint: "Default 8766 (desktop app + `heuriva serve`). Override with `heuriva serve --port`. LLM endpoint is separate (often :8765).",
      about_version: "Version",
      enter_goal: "Enter a goal first.",
      force_confirm: "Force resume a done task? History is never rewritten.",
      copy_ok: "Copied task id",
      back: "← Session",
      final_answer: "Final answer",
      steps: "Steps",
      copy_id: "Copy id",
      resume: "Resume",
      force_resume: "Force resume",
      readonly: "Read-only inspector. Run heuriva serve for the interactive Session UI.",
      opening_result: "Opening full trajectory…",
    },
    zh: {
      tagline: "本机认知工作台 — 提问、检视轨迹、安全续跑。",
      local_chip: "本机会话",
      new_task: "向 Heuriva 提问",
      goal_ph: "想让 Heuriva 解决什么问题？",
      criterion: "完成条件（可选）",
      criterion_ph: "例如：提到取舍",
      search: "搜索",
      run: "运行",
      activity: "运行日志",
      activity_hint: "实时进度",
      activity_empty: "任务进行中时，进度会在这里逐条刷出。",
      clear_log: "清空",
      interrupt: "中断",
      interrupting: "正在中断…",
      interrupt_sent: "已请求中断",
      interrupt_idle: "当前没有运行中的任务。",
      interrupted_ready: "已中断 — 可改写提问或续跑。",
      recent: "最近任务",
      no_tasks: "还没有任务。",
      settings_title: "设置",
      settings_base_url: "LLM Base URL",
      settings_base_url_hint: "OpenAI 兼容端点。保存到 ~/.heuriva/config.yaml，下一次运行生效。",
      settings_model: "模型",
      save: "保存",
      saving: "保存中…",
      cancel: "取消",
      settings_saved: "已保存 — 下一次运行生效。",
      settings_open: "打开设置",
      settings_close: "关闭设置",
      about_title: "关于 Heuriva",
      about_open: "关于",
      about_close: "关闭关于",
      about_blurb: "本机优先的语言模型工作台：步骤显式、轨迹可检视（SQLite）、中断后可安全续跑。",
      about_guidelines_title: "使用约定",
      about_g1: "Session UI 仅本机使用 — 不是远程多租户 dashboard。",
      about_g2: "每一步只选一个操作：ANALYZE / SEARCH / ANSWER。",
      about_g3: "轨迹只追加、不改写；续跑不会回头改历史。",
      about_g4: "默认不开 VERIFY / 语义 enforce；质量检查默认 observe，需显式开启。",
      about_github: "GitHub",
      about_license: "许可证",
      about_port: "会话端口",
      about_port_hint: "默认 8766（桌面端与 `heuriva serve`）。可用 `heuriva serve --port` 改掉。LLM 端点另算（常见 :8765）。",
      about_version: "版本",
      enter_goal: "请先输入任务目标。",
      force_confirm: "强制续跑已完成任务？历史不会被改写，只会追加步骤。",
      copy_ok: "已复制 task id",
      back: "← 会话",
      final_answer: "最终答案",
      steps: "步骤",
      copy_id: "复制 ID",
      resume: "续跑",
      force_resume: "强制续跑",
      readonly: "只读检视模式。请用 heuriva serve 启动可交互 Session UI。",
      opening_result: "正在打开完整轨迹…",
    },
  };

  let lang = localStorage.getItem("heuriva_lang") || ((navigator.language || "").startsWith("zh") ? "zh" : "en");
  let pollTimer = null;
  let lastBusy = false;
  let lastLogLen = 0;
  let redirectScheduled = false;
  let resumeTaskId = null;
  let resumeBaselineGoal = "";
  let interruptPending = false;

  function $(id) { return document.getElementById(id); }

  function pageName() {
    const el = document.querySelector("[data-page]");
    return el ? el.getAttribute("data-page") : "";
  }

  function currentGoal() {
    const goalEl = $("session-goal");
    return (goalEl && goalEl.value || "").trim();
  }

  function isResumeMode() {
    return Boolean(resumeTaskId) && currentGoal() === resumeBaselineGoal;
  }

  function syncSendButton(busy) {
    const send = $("session-send");
    if (!send) return;
    send.disabled = Boolean(busy);
    if (busy) {
      send.textContent = t("run");
      return;
    }
    send.textContent = isResumeMode() ? t("resume") : t("run");
  }

  function enterResumeMode(taskId, goal) {
    resumeTaskId = taskId || null;
    resumeBaselineGoal = (goal || "").trim();
    setComposerRunning(false);
    syncSendButton(false);
  }

  function clearResumeMode() {
    resumeTaskId = null;
    resumeBaselineGoal = "";
    syncSendButton(false);
  }

  function setComposerRunning(running) {
    const form = $("session-form");
    const goalEl = $("session-goal");
    const criterion = $("session-criterion");
    const search = $("session-search-policy");
    if (form) form.classList.toggle("is-running", Boolean(running));
    if (goalEl) goalEl.readOnly = Boolean(running);
    if (criterion) criterion.disabled = Boolean(running);
    if (search) search.disabled = Boolean(running);
  }

  function t(key) {
    return (I18N[lang] && I18N[lang][key]) || I18N.en[key] || key;
  }

  function applyI18n() {
    document.documentElement.lang = lang === "zh" ? "zh-Hans" : "en";
    document.querySelectorAll("[data-i18n]").forEach((el) => {
      const key = el.getAttribute("data-i18n");
      if (key) el.textContent = t(key);
    });
    document.querySelectorAll("[data-i18n-placeholder]").forEach((el) => {
      const key = el.getAttribute("data-i18n-placeholder");
      if (key) el.placeholder = t(key);
    });
    document.querySelectorAll("[data-i18n-aria]").forEach((el) => {
      const key = el.getAttribute("data-i18n-aria");
      if (key) {
        el.setAttribute("aria-label", t(key));
        if (el.hasAttribute("title")) el.setAttribute("title", t(key));
      }
    });
    document.querySelectorAll(".lang-toggle button").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.lang === lang);
    });
    const chip = document.querySelector(".chip[data-i18n='local_chip']");
    if (chip) {
      const port = location.port || "8766";
      chip.textContent = `${t("local_chip")} · :${port}`;
    }
    const aboutPort = $("about-port-value");
    if (aboutPort) {
      aboutPort.textContent = location.port || "8766";
    }
    syncSendButton(Boolean($("session-send") && $("session-send").disabled));
  }

  function setLang(next) {
    lang = next;
    localStorage.setItem("heuriva_lang", lang);
    applyI18n();
  }

  function toast(message) {
    const el = $("session-toast");
    if (!el) return;
    el.textContent = message;
    el.classList.add("visible");
    setTimeout(() => el.classList.remove("visible"), 3200);
  }

  async function api(path, options) {
    const res = await fetch(path, {
      headers: { Accept: "application/json", ...(options && options.headers || {}) },
      ...options,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const err = new Error((data && (data.message || data.error)) || res.statusText);
      err.status = res.status;
      err.data = data;
      throw err;
    }
    return data;
  }

  function formatTs(ts) {
    const d = new Date(ts * 1000);
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  }

  function renderLog(log, force) {
    const feed = $("activity-feed");
    if (!feed) return;
    if (!log || !log.length) {
      if (force) {
        feed.innerHTML = '<div class="activity-empty">' + t("activity_empty") + "</div>";
        lastLogLen = 0;
      }
      return;
    }
    if (force || lastLogLen === 0) {
      feed.innerHTML = "";
      lastLogLen = 0;
    }
    for (let i = lastLogLen; i < log.length; i++) {
      const line = log[i];
      const el = document.createElement("div");
      el.className = "activity-line";
      const parts = [];
      if (line.ts) parts.push('<span class="ts">' + formatTs(line.ts) + "</span>");
      if (line.stage) parts.push('<span class="stage">' + line.stage + "</span>");
      if (line.operator) parts.push("<span>" + line.operator + "</span>");
      if (line.step_index != null) parts.push("<span>step " + line.step_index + "</span>");
      parts.push("<span>" + (line.message || "") + "</span>");
      el.innerHTML = parts.join(" ");
      feed.appendChild(el);
    }
    lastLogLen = log.length;
    feed.scrollTop = feed.scrollHeight;
  }

  function clearLogView() {
    lastLogLen = 0;
    renderLog([], true);
  }

  async function pollOnce() {
    const snap = await api("/api/status");
    renderLog(snap.log || []);
    const busy = Boolean(snap.busy);
    setComposerRunning(busy);
    syncSendButton(busy);
    const interruptBtn = $("activity-interrupt");
    if (interruptBtn) interruptBtn.disabled = !busy;
    document.querySelectorAll("[data-resume-btn]").forEach((btn) => { btn.disabled = busy; });
    if (lastBusy && !busy && !redirectScheduled) {
      const interrupted =
        interruptPending ||
        snap.result_status === "interrupted" ||
        snap.error_code === "interrupted" ||
        snap.stage === "interrupted";
      interruptPending = false;
      if (interrupted && snap.task_id && pageName() === "home") {
        enterResumeMode(snap.task_id, currentGoal());
        toast(t("interrupted_ready"));
      } else if (snap.task_id && pageName() === "home") {
        redirectScheduled = true;
        clearResumeMode();
        if (snap.error) toast(snap.error);
        else toast(t("opening_result"));
        if (window.HeurivaSessionOnIdle) window.HeurivaSessionOnIdle(snap);
        else {
          setTimeout(() => {
            window.location.href = "/tasks/" + encodeURIComponent(snap.task_id);
          }, 700);
        }
      } else {
        if (snap.error) toast(snap.error);
        else if (snap.result_status) {
          toast((snap.task_id || "").slice(0, 8) + "… → " + snap.result_status);
        }
        if (window.HeurivaSessionOnIdle && snap.task_id) {
          window.HeurivaSessionOnIdle(snap);
        } else if (pageName() === "detail" && snap.task_id) {
          redirectScheduled = true;
          setTimeout(() => { window.location.reload(); }, 700);
        }
      }
    }
    lastBusy = busy;
    return snap;
  }

  function startPolling() {
    if (pollTimer) return;
    pollTimer = setInterval(() => { pollOnce().catch(() => {}); }, 800);
    pollOnce().catch(() => {});
  }

  async function submitRun(event) {
    event.preventDefault();
    const goalEl = $("session-goal");
    const goal = (goalEl && goalEl.value || "").trim();
    if (!goal) { toast(t("enter_goal")); return; }
    if (isResumeMode()) {
      await submitResume(resumeTaskId, false);
      return;
    }
    const criteria = [];
    const criterion = ($("session-criterion") && $("session-criterion").value || "").trim();
    if (criterion) criteria.push(criterion);
    try {
      clearLogView();
      lastBusy = true;
      redirectScheduled = false;
      clearResumeMode();
      interruptPending = false;
      setComposerRunning(true);
      syncSendButton(true);
      await api("/api/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          goal,
          criteria,
          search_policy: ($("session-search-policy") && $("session-search-policy").value) || "auto",
        }),
      });
      startPolling();
    } catch (err) {
      toast(err.message || "Run failed");
      setComposerRunning(false);
      syncSendButton(false);
    }
  }

  async function submitResume(taskId, force) {
    try {
      clearLogView();
      lastBusy = true;
      redirectScheduled = false;
      setComposerRunning(true);
      syncSendButton(true);
      await api("/api/resume", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ task_id: taskId, force: Boolean(force) }),
      });
      if (pageName() === "detail") {
        const heading = document.querySelector(".detail-hero h1");
        const goal = (heading && heading.textContent || "").trim();
        try {
          sessionStorage.setItem("heuriva_resume_goal", goal);
          sessionStorage.setItem("heuriva_resume_task", taskId);
        } catch (_) {}
        window.location.href = "/";
        return;
      }
      startPolling();
    } catch (err) {
      toast(err.message || "Resume failed");
      setComposerRunning(false);
      syncSendButton(false);
    }
  }

  async function submitInterrupt() {
    const btn = $("activity-interrupt");
    if (btn) {
      btn.disabled = true;
      btn.textContent = t("interrupting");
    }
    try {
      await api("/api/interrupt", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      interruptPending = true;
      toast(t("interrupt_sent"));
      startPolling();
    } catch (err) {
      interruptPending = false;
      toast(err.message || t("interrupt_idle"));
    } finally {
      if (btn) btn.textContent = t("interrupt");
    }
  }

  function copyText(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(() => toast(t("copy_ok")));
      return;
    }
    toast(text);
  }

  async function saveSettings(event) {
    event.preventDefault();
    const baseEl = $("settings-base-url");
    const modelEl = $("settings-model");
    const saveBtn = $("settings-save");
    const base_url = (baseEl && baseEl.value || "").trim();
    const model = (modelEl && modelEl.value || "").trim();
    if (!base_url) { toast(t("settings_base_url_required") || "Base URL required"); return; }
    if (!model) { toast(t("settings_model_required") || "Model required"); return; }
    const payload = { base_url, model };
    if (saveBtn) {
      saveBtn.disabled = true;
      saveBtn.textContent = t("saving");
    }
    try {
      try {
        await api("/api/settings", {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
      } catch (err) {
        if (err.status === 405 || err.status === 404) {
          await api("/api/settings", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
          });
        } else {
          throw err;
        }
      }
      toast(t("settings_saved"));
      closeSettings();
    } catch (err) {
      toast(err.message || "Save failed");
    } finally {
      if (saveBtn) {
        saveBtn.disabled = false;
        saveBtn.textContent = t("save");
      }
    }
  }

  function openSettings() {
    const modal = $("settings-modal");
    if (!modal) return;
    closeAbout();
    modal.classList.add("open");
    modal.setAttribute("aria-hidden", "false");
    $("settings-base-url")?.focus();
  }

  function closeSettings() {
    const modal = $("settings-modal");
    if (!modal) return;
    modal.classList.remove("open");
    modal.setAttribute("aria-hidden", "true");
  }

  function openAbout() {
    const modal = $("about-modal");
    if (!modal) return;
    closeSettings();
    modal.classList.add("open");
    modal.setAttribute("aria-hidden", "false");
  }

  function closeAbout() {
    const modal = $("about-modal");
    if (!modal) return;
    modal.classList.remove("open");
    modal.setAttribute("aria-hidden", "true");
  }

  function restoreResumeFromDetail() {
    if (pageName() !== "home") return;
    let goal = "";
    let taskId = "";
    try {
      goal = sessionStorage.getItem("heuriva_resume_goal") || "";
      taskId = sessionStorage.getItem("heuriva_resume_task") || "";
      sessionStorage.removeItem("heuriva_resume_goal");
      sessionStorage.removeItem("heuriva_resume_task");
    } catch (_) {}
    if (goal && $("session-goal")) $("session-goal").value = goal;
    if (taskId) {
      resumeTaskId = taskId;
      resumeBaselineGoal = goal;
    }
  }

  function bind() {
    applyI18n();
    restoreResumeFromDetail();
    document.querySelectorAll(".lang-toggle button").forEach((btn) => {
      btn.addEventListener("click", () => setLang(btn.dataset.lang));
    });
    $("settings-open")?.addEventListener("click", openSettings);
    $("settings-close")?.addEventListener("click", closeSettings);
    $("settings-cancel")?.addEventListener("click", closeSettings);
    $("settings-modal")?.addEventListener("click", (event) => {
      if (event.target === $("settings-modal")) closeSettings();
    });
    $("about-open")?.addEventListener("click", openAbout);
    $("about-close")?.addEventListener("click", closeAbout);
    $("about-modal")?.addEventListener("click", (event) => {
      if (event.target === $("about-modal")) closeAbout();
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        closeSettings();
        closeAbout();
      }
    });
    $("settings-form")?.addEventListener("submit", saveSettings);
    $("activity-clear")?.addEventListener("click", clearLogView);
    $("activity-interrupt")?.addEventListener("click", submitInterrupt);
    $("session-form")?.addEventListener("submit", submitRun);
    $("session-goal")?.addEventListener("input", () => {
      if (!($("session-send") && $("session-send").disabled)) syncSendButton(false);
    });
    document.querySelectorAll("[data-resume-btn]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const taskId = btn.getAttribute("data-task-id");
        const force = btn.getAttribute("data-force") === "1";
        if (force && !window.confirm(t("force_confirm"))) return;
        submitResume(taskId, force);
      });
    });
    document.querySelectorAll("[data-copy-id]").forEach((btn) => {
      btn.addEventListener("click", () => copyText(btn.getAttribute("data-copy-id") || ""));
    });
    const page = document.querySelector("[data-page]");
    if (page && page.dataset.sessionEnabled === "1") startPolling();
  }

  document.addEventListener("DOMContentLoaded", bind);
  return { pollOnce, startPolling, submitResume, toast, setLang };
})();
"""


_GEAR_SVG = """<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path fill="currentColor" d="M19.14 12.94c.04-.31.06-.63.06-.94s-.02-.63-.06-.94l2.03-1.58a.5.5 0 0 0 .12-.64l-1.92-3.32a.5.5 0 0 0-.6-.22l-2.39.96a7.2 7.2 0 0 0-1.63-.94l-.36-2.54A.5.5 0 0 0 13.9 2h-3.8a.5.5 0 0 0-.5.42l-.36 2.54c-.59.24-1.13.55-1.63.94l-2.39-.96a.5.5 0 0 0-.6.22L2.7 8.48a.5.5 0 0 0 .12.64l2.03 1.58c-.04.31-.06.63-.06.94s.02.63.06.94l-2.03 1.58a.5.5 0 0 0-.12.64l1.92 3.32c.14.24.43.34.68.24l2.39-.96c.5.39 1.04.7 1.63.94l.36 2.54c.05.24.26.42.5.42h3.8c.24 0 .45-.18.5-.42l.36-2.54c.59-.24 1.13-.55 1.63-.94l2.39.96c.25.1.54 0 .68-.24l1.92-3.32a.5.5 0 0 0-.12-.64l-2.03-1.58zM12 15.5A3.5 3.5 0 1 1 12 8.5a3.5 3.5 0 0 1 0 7z"/></svg>"""

_INFO_SVG = """<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="2"/><circle cx="12" cy="8" r="1.25" fill="currentColor"/><path fill="currentColor" d="M11 11h2v6h-2z"/></svg>"""

_CLOSE_SVG = """<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path fill="currentColor" d="M18.3 5.7a1 1 0 0 0-1.4 0L12 10.59 7.1 5.7A1 1 0 0 0 5.7 7.1L10.59 12 5.7 16.9a1 1 0 1 0 1.4 1.4L12 13.41l4.9 4.89a1 1 0 0 0 1.4-1.4L13.41 12l4.89-4.9a1 1 0 0 0 0-1.4z"/></svg>"""


def _topbar(*, session_enabled: bool) -> str:
    settings_btn = ""
    if session_enabled:
        settings_btn = f"""
      <button type="button" class="icon-btn" id="settings-open"
        data-i18n-aria="settings_open" aria-label="Open settings" title="Settings">{_GEAR_SVG}</button>
        """
    return f"""
  <header class="topbar">
    <a class="logo" href="/">
      <img class="logo-mark" src="/static/icon.png" alt="" width="28" height="28"/>
      <span>Heuriva</span>
    </a>
    <span class="topbar-spacer"></span>
    <div class="topbar-actions">
      <div class="lang-toggle" role="group" aria-label="Language">
        <button type="button" data-lang="en">EN</button>
        <button type="button" data-lang="zh">中文</button>
      </div>
      <button type="button" class="icon-btn" id="about-open"
        data-i18n-aria="about_open" aria-label="About" title="About">{_INFO_SVG}</button>
      {settings_btn}
    </div>
  </header>
    """


def _about_modal(*, version: str) -> str:
    return f"""
    <div id="about-modal" class="about-modal" aria-hidden="true" role="presentation">
      <div class="about-dialog" role="dialog" aria-modal="true" aria-labelledby="about-title">
        <div class="about-dialog-head">
          <h2 id="about-title" data-i18n="about_title">About Heuriva</h2>
          <button type="button" class="icon-btn" id="about-close"
            data-i18n-aria="about_close" aria-label="Close about">{_CLOSE_SVG}</button>
        </div>
        <div class="about-body">
          <p data-i18n="about_blurb">A local-first workspace for language-model tasks.</p>
          <div class="about-meta">
            <div><span class="label" data-i18n="about_github">GitHub</span>
              <a href="https://github.com/ingeniousfrog/Heuriva" target="_blank" rel="noopener noreferrer">ingeniousfrog/Heuriva</a></div>
            <div><span class="label" data-i18n="about_license">License</span>
              <a href="https://github.com/ingeniousfrog/Heuriva/blob/main/LICENSE" target="_blank" rel="noopener noreferrer">MIT</a></div>
            <div><span class="label" data-i18n="about_version">Version</span> <code>{_esc(version)}</code></div>
            <div><span class="label" data-i18n="about_port">Session port</span> <code id="about-port-value">8766</code></div>
          </div>
          <p data-i18n="about_port_hint">Default 8766. Override with heuriva serve --port.</p>
          <p><strong data-i18n="about_guidelines_title">Guidelines</strong></p>
          <ul>
            <li data-i18n="about_g1">Localhost-only Session UI.</li>
            <li data-i18n="about_g2">Each step picks one operator: ANALYZE, SEARCH, or ANSWER.</li>
            <li data-i18n="about_g3">Trajectory history is append-only.</li>
            <li data-i18n="about_g4">No default VERIFY / semantic enforce.</li>
          </ul>
        </div>
      </div>
    </div>
    """


def _settings_modal(*, base_url: str, model: str, session_enabled: bool) -> str:
    if not session_enabled:
        return ""
    return f"""
    <div id="settings-modal" class="settings-modal" aria-hidden="true" role="presentation">
      <div class="settings-dialog" role="dialog" aria-modal="true" aria-labelledby="settings-title">
        <div class="settings-dialog-head">
          <h2 id="settings-title" data-i18n="settings_title">Settings</h2>
          <button type="button" class="icon-btn" id="settings-close"
            data-i18n-aria="settings_close" aria-label="Close settings">{_CLOSE_SVG}</button>
        </div>
        <form id="settings-form" class="settings-form">
          <div class="field">
            <label for="settings-base-url" data-i18n="settings_base_url">LLM base URL</label>
            <input id="settings-base-url" name="base_url" type="text" autocomplete="off"
              spellcheck="false" value="{_esc(base_url)}"/>
            <p class="hint" data-i18n="settings_base_url_hint">OpenAI-compatible endpoint.</p>
          </div>
          <div class="field">
            <label for="settings-model" data-i18n="settings_model">Model</label>
            <input id="settings-model" name="model" type="text" autocomplete="off"
              value="{_esc(model)}"/>
          </div>
          <div class="settings-actions">
            <button type="button" class="secondary" id="settings-cancel" data-i18n="cancel">Cancel</button>
            <button type="submit" id="settings-save" data-i18n="save">Save</button>
          </div>
        </form>
      </div>
    </div>
    """


def render_session_home(
    tasks: tuple[TaskListItem, ...],
    *,
    db_path: str,
    session_enabled: bool = True,
    status: dict[str, Any] | None = None,
    llm_settings: dict[str, str] | None = None,
) -> str:
    del status
    base_url = (llm_settings or {}).get("base_url", "http://localhost:8765/v1")
    model = (llm_settings or {}).get("model", "auto")
    rows: list[str] = []
    for task in tasks:
        rows.append(
            f'<li><a href="/tasks/{_esc(task.task_id)}">'
            f'<span class="mono col-id">{_esc(task.task_id[:8])}…</span>'
            f'<span class="status-pill col-status">{_esc(task.status)}</span>'
            f'<span class="muted col-steps">{task.step_count}</span>'
            f'<span class="goal-cell col-goal">{_esc(task.goal_summary)}</span>'
            f"</a></li>"
        )
    list_html = (
        f'<ul class="task-list">{"".join(rows)}</ul>'
        if rows
        else f'<p class="empty" data-i18n="no_tasks">{_esc("No tasks yet.")}</p>'
    )

    composer = ""
    activity = ""
    if session_enabled:
        composer = """
      <form class="composer-card" id="session-form">
        <textarea id="session-goal" name="goal" data-i18n-placeholder="goal_ph"
          placeholder="What should Heuriva solve?" required></textarea>
        <div class="composer-toolbar">
          <div class="field">
            <label for="session-criterion" data-i18n="criterion">Criterion (optional)</label>
            <input id="session-criterion" type="text" data-i18n-placeholder="criterion_ph"
              placeholder="e.g. mention tradeoffs"/>
          </div>
          <div class="field" style="flex:0 1 7rem">
            <label for="session-search-policy" data-i18n="search">Search</label>
            <select id="session-search-policy">
              <option value="auto" selected>auto</option>
              <option value="required">required</option>
              <option value="forbidden">forbidden</option>
            </select>
          </div>
          <button type="submit" id="session-send" data-i18n="run">Run</button>
        </div>
      </form>
        """
        activity = """
      <aside class="activity-panel">
        <div class="activity-head">
          <span><span data-i18n="activity">Activity</span> <span class="sub" data-i18n="activity_hint">live</span></span>
          <div class="activity-actions">
            <button type="button" class="danger" id="activity-interrupt" disabled data-i18n="interrupt">Interrupt</button>
            <button type="button" class="secondary" id="activity-clear" data-i18n="clear_log">Clear</button>
          </div>
        </div>
        <div class="activity-feed" id="activity-feed">
          <div class="activity-empty" data-i18n="activity_empty">Progress lines appear here while a task runs.</div>
        </div>
      </aside>
        """
    else:
        composer = '<p class="banner" data-i18n="readonly">Read-only inspector mode.</p>'

    body = f"""
  <div class="page" data-session-enabled="{1 if session_enabled else 0}" data-page="home">
    {_topbar(session_enabled=session_enabled)}
    <section class="hero">
      <h1>Heuriva</h1>
      <p data-i18n="tagline">Local cognitive workspace — ask, inspect trajectories, resume safely.</p>
      <p class="chip" data-i18n="local_chip">localhost session</p>
    </section>
    {_about_modal(version=__version__)}
    {_settings_modal(base_url=base_url, model=model, session_enabled=session_enabled)}
    <div class="layout-split">
      <div>{composer}</div>
      {activity}
    </div>
    <details class="recent-drawer" id="recent-drawer">
      <summary>
        <span data-i18n="recent">Recent tasks</span>
        <span class="count">({len(tasks)})</span>
        <span class="db">{_esc(db_path)}</span>
      </summary>
      <div class="recent-body">{list_html}</div>
    </details>
  </div>
    """
    return _shell("Heuriva", body)


def render_task_list(tasks: tuple[TaskListItem, ...], *, db_path: str) -> str:
    return render_session_home(tasks, db_path=db_path, session_enabled=False)


def render_task_detail(
    detail: TaskDetail,
    *,
    db_path: str,
    session_enabled: bool = False,
    resume_eligibility: dict[str, Any] | None = None,
    status: dict[str, Any] | None = None,
    llm_settings: dict[str, str] | None = None,
) -> str:
    del status
    base_url = (llm_settings or {}).get("base_url", "http://localhost:8765/v1")
    model = (llm_settings or {}).get("model", "auto")
    eligibility = resume_eligibility or {}
    can_resume = bool(eligibility.get("eligible"))
    is_done = detail.status == "done"
    actions: list[str] = [
        f'<button type="button" class="secondary" data-copy-id="{_esc(detail.task_id)}" '
        f'data-i18n="copy_id">Copy id</button>'
    ]
    if session_enabled and can_resume:
        actions.append(
            f'<button type="button" data-resume-btn data-task-id="{_esc(detail.task_id)}" '
            f'data-force="0" data-i18n="resume">Resume</button>'
        )
    elif session_enabled and is_done:
        actions.append(
            f'<button type="button" class="danger" data-resume-btn '
            f'data-task-id="{_esc(detail.task_id)}" data-force="1" data-i18n="force_resume">'
            f"Force resume</button>"
        )

    steps_html: list[str] = []
    for step in detail.steps:
        steps_html.append(
            "<li>"
            f'<div class="op">{_esc(step.operator)} · step {step.step_index}</div>'
            f"<div>{_esc(step.objective)}</div>"
            f'<div class="obs">{_esc(step.observation_summary)}</div>'
            "</li>"
        )
    timeline = (
        f'<ol class="timeline">{"".join(steps_html)}</ol>'
        if steps_html
        else '<p class="empty">No steps.</p>'
    )

    contract_block = (
        f"<pre>{_esc(json.dumps(detail.task_contract, ensure_ascii=False, indent=2))}</pre>"
        if detail.task_contract
        else '<p class="empty">No task_contract.</p>'
    )
    assessment_block = (
        f"<pre>{_esc(json.dumps(detail.completion_assessment, ensure_ascii=False, indent=2))}</pre>"
        if detail.completion_assessment
        else '<p class="empty">No completion_assessment.</p>'
    )
    failed = ", ".join(detail.failed_criteria) if detail.failed_criteria else "—"

    body = f"""
  <div class="page" data-session-enabled="{1 if session_enabled else 0}" data-page="detail">
    {_topbar(session_enabled=session_enabled)}
    {_about_modal(version=__version__)}
    {_settings_modal(base_url=base_url, model=model, session_enabled=session_enabled)}
    <nav class="crumbs"><a href="/" data-i18n="back">← Session</a></nav>
    <header class="detail-hero">
      <p class="chip" data-i18n="local_chip">localhost session</p>
      <h1>{_esc(detail.goal or "(no goal)")}</h1>
      <div class="meta-row">
        <span class="mono">{_esc(detail.task_id)}</span>
        <span class="status-pill">{_esc(detail.status)}</span>
        <span class="mono">{_esc(db_path)}</span>
      </div>
      <div class="actions">{"".join(actions)}</div>
    </header>
    <section class="answer-block">
      <h2 data-i18n="final_answer">Final answer</h2>
      <div class="body">{_esc(detail.final_answer or "—")}</div>
    </section>
    <section class="panel">
      <h2 data-i18n="steps">Steps</h2>
      {timeline}
      <details class="fold">
        <summary>Contract &amp; quality</summary>
        <p class="muted">failed: {_esc(failed)} · termination: {_esc(detail.termination_reason or "—")}</p>
        {contract_block}
        {assessment_block}
      </details>
    </section>
  </div>
    """
    return _shell(f"Heuriva · {detail.task_id[:8]}", body)


def render_not_found(message: str) -> str:
    body = f"""
  <div class="page">
    {_topbar(session_enabled=False)}
    {_about_modal(version=__version__)}
    <section class="hero"><h1>404</h1><p>{_esc(message)}</p></section>
  </div>
    """
    return _shell("Not found", body)


def render_error(message: str) -> str:
    body = f"""
  <div class="page">
    {_topbar(session_enabled=False)}
    {_about_modal(version=__version__)}
    <section class="hero"><h1>Error</h1><p>{_esc(message)}</p></section>
  </div>
    """
    return _shell("Error", body)


def wants_json(headers: Any, query: dict[str, list[str]]) -> bool:
    if query.get("format", [""])[0].lower() == "json":
        return True
    accept = str(getattr(headers, "get", lambda _k, _d="": "")("Accept", "") or "")
    return "application/json" in accept and "text/html" not in accept
