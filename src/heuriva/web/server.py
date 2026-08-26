"""Localhost HTTP server for Session UI (interactive) + trajectory inspector."""

from __future__ import annotations

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from heuriva.config import llm_settings_public, update_llm_settings
from heuriva.runtime.engine import ResumeRejected
from heuriva.web.display import display_storage_path
from heuriva.web.html import (
    render_error,
    render_not_found,
    render_session_home,
    render_task_detail,
    wants_json,
)
from heuriva.web.queries import TrajectoryBrowser
from heuriva.web.session import SessionBusy, SessionService

_STATIC_DIR = Path(__file__).resolve().parent / "static"
_STATIC_FILES = frozenset({"icon.png", "favicon.png", "apple-touch-icon.png"})

_TASK_PATH = re.compile(r"^/tasks/([^/]+)/?$")
_API_RESUME_ELIGIBILITY = re.compile(r"^/api/tasks/([^/]+)/resume-eligibility/?$")


class _ServerState:
    def __init__(
        self,
        browser: TrajectoryBrowser,
        db_path: Path,
        session: SessionService | None = None,
    ) -> None:
        self.browser = browser
        self.db_path = db_path
        self.session = session


def create_handler(state: _ServerState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            return

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            path = parsed.path.rstrip("/") or "/"
            try:
                if path == "/api/status":
                    self._handle_api_status()
                    return
                if path == "/api/settings":
                    self._handle_api_settings_get()
                    return
                if path == "/api/tasks":
                    self._handle_api_list(query)
                    return
                eligibility = _API_RESUME_ELIGIBILITY.match(parsed.path)
                if eligibility:
                    self._handle_api_resume_eligibility(eligibility.group(1), query)
                    return
                if path == "/":
                    self._handle_home(query)
                    return
                if path.startswith("/static/"):
                    self._handle_static(path)
                    return
                match = _TASK_PATH.match(parsed.path)
                if match:
                    self._handle_detail(match.group(1), query)
                    return
                self._respond(
                    404,
                    render_not_found("Unknown path."),
                    content_type="text/html; charset=utf-8",
                )
            except KeyError:
                if wants_json(self.headers, query):
                    self._respond_json(404, {"error": "task_not_found"})
                else:
                    self._respond(
                        404,
                        render_not_found("Task not found."),
                        content_type="text/html; charset=utf-8",
                    )
            except Exception as exc:
                if wants_json(self.headers, query) or path.startswith("/api/"):
                    self._respond_json(500, {"error": str(exc)})
                else:
                    self._respond(
                        500,
                        render_error(str(exc)),
                        content_type="text/html; charset=utf-8",
                    )

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            if state.session is None:
                self._method_not_allowed_readonly()
                return
            try:
                if path == "/api/run":
                    self._handle_api_run()
                    return
                if path == "/api/resume":
                    self._handle_api_resume()
                    return
                if path == "/api/interrupt":
                    self._handle_api_interrupt()
                    return
                if path == "/api/settings":
                    self._handle_api_settings_patch()
                    return
                self._respond_json(404, {"error": "unknown_endpoint"})
            except SessionBusy as exc:
                self._respond_json(
                    409,
                    {
                        "error": "session_busy",
                        "active_task_id": exc.active_task_id,
                        "job_kind": exc.job_kind,
                        "status": state.session.snapshot().to_dict(),
                    },
                )
            except ResumeRejected as exc:
                self._respond_json(
                    409,
                    {
                        "error": "resume_rejected",
                        "message": str(exc),
                        "eligibility": exc.eligibility.to_dict(),
                    },
                )
            except ValueError as exc:
                self._respond_json(400, {"error": "bad_request", "message": str(exc)})
            except KeyError as exc:
                self._respond_json(
                    404,
                    {"error": "task_not_found", "task_id": str(exc.args[0] if exc.args else "")},
                )
            except Exception as exc:
                self._respond_json(500, {"error": str(exc)})

        def do_PUT(self) -> None:  # noqa: N802
            self._method_not_allowed_readonly()

        def do_PATCH(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            if state.session is None:
                self._method_not_allowed_readonly()
                return
            if path == "/api/settings":
                try:
                    self._handle_api_settings_patch()
                except ValueError as exc:
                    self._respond_json(400, {"error": "bad_request", "message": str(exc)})
                except Exception as exc:
                    self._respond_json(500, {"error": str(exc)})
                return
            self._respond_json(404, {"error": "unknown_endpoint"})

        def do_DELETE(self) -> None:  # noqa: N802
            self._method_not_allowed_readonly()

        def _method_not_allowed_readonly(self) -> None:
            self._respond(
                405,
                "Write methods require Session UI. Use POST /api/run or /api/resume.\n",
                content_type="text/plain; charset=utf-8",
            )

        def _handle_static(self, path: str) -> None:
            name = path.removeprefix("/static/")
            if name not in _STATIC_FILES:
                self._respond(404, "Not found.", content_type="text/plain; charset=utf-8")
                return
            file_path = _STATIC_DIR / name
            if not file_path.is_file():
                self._respond(404, "Not found.", content_type="text/plain; charset=utf-8")
                return
            self._respond_bytes(
                200,
                file_path.read_bytes(),
                content_type="image/png",
                cache_control="public, max-age=86400",
            )

        def _handle_home(self, query: dict[str, list[str]]) -> None:
            limit = _int_param(query, "limit", default=100, minimum=1, maximum=500)
            offset = _int_param(query, "offset", default=0, minimum=0, maximum=100_000)
            tasks = state.browser.list_tasks(limit=limit, offset=offset)
            if wants_json(self.headers, query):
                payload: dict[str, Any] = {
                    "db_path": display_storage_path(state.db_path),
                    "tasks": [task.to_dict() for task in tasks],
                    "session_enabled": state.session is not None,
                    "read_only": state.session is None,
                }
                if state.session is not None:
                    payload["status"] = state.session.snapshot().to_dict()
                self._respond_json(200, payload)
                return
            body = render_session_home(
                tasks,
                db_path=display_storage_path(state.db_path),
                session_enabled=state.session is not None,
                status=(state.session.snapshot().to_dict() if state.session else None),
                llm_settings=llm_settings_public(),
            )
            self._respond(200, body, content_type="text/html; charset=utf-8")

        def _handle_detail(self, task_id: str, query: dict[str, list[str]]) -> None:
            detail = state.browser.get_task(task_id)
            eligibility = None
            if state.session is not None:
                eligibility = state.session.resume_eligibility(task_id).to_dict()
            if wants_json(self.headers, query):
                payload = detail.to_dict()
                payload["db_path"] = display_storage_path(state.db_path)
                payload["session_enabled"] = state.session is not None
                payload["read_only"] = state.session is None
                if eligibility is not None:
                    payload["resume_eligibility"] = eligibility
                if state.session is not None:
                    payload["status"] = state.session.snapshot().to_dict()
                self._respond_json(200, payload)
                return
            body = render_task_detail(
                detail,
                db_path=display_storage_path(state.db_path),
                session_enabled=state.session is not None,
                resume_eligibility=eligibility,
                status=(state.session.snapshot().to_dict() if state.session else None),
                llm_settings=llm_settings_public(),
            )
            self._respond(200, body, content_type="text/html; charset=utf-8")

        def _handle_api_status(self) -> None:
            if state.session is None:
                self._respond_json(200, {"session_enabled": False, "busy": False})
                return
            snap = state.session.snapshot().to_dict()
            snap["session_enabled"] = True
            self._respond_json(200, snap)

        def _handle_api_list(self, query: dict[str, list[str]]) -> None:
            limit = _int_param(query, "limit", default=100, minimum=1, maximum=500)
            offset = _int_param(query, "offset", default=0, minimum=0, maximum=100_000)
            tasks = state.browser.list_tasks(limit=limit, offset=offset)
            self._respond_json(
                200,
                {
                    "db_path": display_storage_path(state.db_path),
                    "tasks": [task.to_dict() for task in tasks],
                    "session_enabled": state.session is not None,
                },
            )

        def _handle_api_resume_eligibility(self, task_id: str, query: dict[str, list[str]]) -> None:
            if state.session is None:
                self._respond_json(404, {"error": "session_disabled"})
                return
            force = query.get("force", ["0"])[0].lower() in {"1", "true", "yes"}
            eligibility = state.session.resume_eligibility(task_id, force=force)
            self._respond_json(200, eligibility.to_dict())

        def _handle_api_run(self) -> None:
            assert state.session is not None
            body = self._read_json_body()
            goal = str(body.get("goal") or "").strip()
            criteria_raw = body.get("criteria") or []
            if not isinstance(criteria_raw, list):
                raise ValueError("criteria must be a list")
            search_policy = str(body.get("search_policy") or "auto")
            result = state.session.start_run(
                goal,
                criteria=tuple(criteria_raw),
                search_policy=search_policy,
            )
            result["status"] = state.session.snapshot().to_dict()
            self._respond_json(202, result)

        def _handle_api_settings_get(self) -> None:
            settings = llm_settings_public()
            settings["db_path"] = display_storage_path(state.db_path)
            self._respond_json(200, settings)

        def _handle_api_settings_patch(self) -> None:
            body = self._read_json_body()
            base_url = body.get("base_url")
            model = body.get("model")
            if base_url is None and model is None:
                raise ValueError("provide base_url and/or model")
            updated = update_llm_settings(
                base_url=str(base_url) if base_url is not None else None,
                model=str(model) if model is not None else None,
            )
            self._respond_json(
                200,
                {
                    "base_url": updated.llm.base_url,
                    "model": updated.llm.model,
                    "db_path": display_storage_path(state.db_path),
                },
            )

        def _handle_api_resume(self) -> None:
            assert state.session is not None
            body = self._read_json_body()
            task_id = str(body.get("task_id") or "").strip()
            force = bool(body.get("force"))
            result = state.session.start_resume(task_id, force=force)
            result["status"] = state.session.snapshot().to_dict()
            self._respond_json(202, result)

        def _handle_api_interrupt(self) -> None:
            assert state.session is not None
            result = state.session.request_interrupt()
            result["status"] = state.session.snapshot().to_dict()
            self._respond_json(202, result)

        def _read_json_body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or "0")
            if length < 0 or length > 1_000_000:
                raise ValueError("invalid Content-Length")
            raw = self.rfile.read(length) if length else b"{}"
            if not raw:
                return {}
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("body must be JSON") from exc
            if not isinstance(payload, dict):
                raise ValueError("JSON body must be an object")
            return payload

        def _respond(self, status: int, body: str, *, content_type: str) -> None:
            payload = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def _respond_bytes(
            self,
            status: int,
            body: bytes,
            *,
            content_type: str,
            cache_control: str = "no-store",
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache_control)
            self.end_headers()
            self.wfile.write(body)

        def _respond_json(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
            self._respond(status, body, content_type="application/json; charset=utf-8")

    return Handler


def _int_param(
    query: dict[str, list[str]],
    name: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    raw = query.get(name, [str(default)])[0]
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


def is_loopback_host(host: str) -> bool:
    normalized = host.strip().lower()
    return normalized in {"127.0.0.1", "localhost", "::1"}


def serve_browser(
    *,
    browser: TrajectoryBrowser,
    host: str = "127.0.0.1",
    port: int = 8766,
    db_path: Path | str,
    session: SessionService | None = None,
) -> ThreadingHTTPServer:
    """Create and bind the Session / browser server. Caller owns serve_forever/shutdown."""
    state = _ServerState(
        browser=browser,
        db_path=Path(db_path).expanduser(),
        session=session,
    )
    handler = create_handler(state)
    return ThreadingHTTPServer((host, port), handler)
