"""Localhost-only HTTP server for the read-only trajectory browser."""

from __future__ import annotations

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from heuriva.web.html import (
    render_error,
    render_not_found,
    render_task_detail,
    render_task_list,
    wants_json,
)
from heuriva.web.queries import TrajectoryBrowser

_TASK_PATH = re.compile(r"^/tasks/([^/]+)/?$")


class _BrowserState:
    def __init__(self, browser: TrajectoryBrowser, db_path: Path) -> None:
        self.browser = browser
        self.db_path = db_path


def create_handler(state: _BrowserState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            # Keep CLI output quiet; serve prints the URL once at start.
            return

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            path = parsed.path.rstrip("/") or "/"
            try:
                if path == "/":
                    self._handle_list(query)
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
                if wants_json(self.headers, query):
                    self._respond_json(500, {"error": str(exc)})
                else:
                    self._respond(
                        500,
                        render_error(str(exc)),
                        content_type="text/html; charset=utf-8",
                    )

        def do_POST(self) -> None:  # noqa: N802
            self._method_not_allowed()

        def do_PUT(self) -> None:  # noqa: N802
            self._method_not_allowed()

        def do_PATCH(self) -> None:  # noqa: N802
            self._method_not_allowed()

        def do_DELETE(self) -> None:  # noqa: N802
            self._method_not_allowed()

        def _method_not_allowed(self) -> None:
            self._respond(
                405,
                "Read-only trajectory browser: write methods are disabled.\n",
                content_type="text/plain; charset=utf-8",
            )

        def _handle_list(self, query: dict[str, list[str]]) -> None:
            limit = _int_param(query, "limit", default=100, minimum=1, maximum=500)
            offset = _int_param(query, "offset", default=0, minimum=0, maximum=100_000)
            tasks = state.browser.list_tasks(limit=limit, offset=offset)
            if wants_json(self.headers, query):
                self._respond_json(
                    200,
                    {
                        "db_path": str(state.db_path),
                        "tasks": [task.to_dict() for task in tasks],
                        "read_only": True,
                    },
                )
                return
            body = render_task_list(tasks, db_path=str(state.db_path))
            self._respond(200, body, content_type="text/html; charset=utf-8")

        def _handle_detail(self, task_id: str, query: dict[str, list[str]]) -> None:
            detail = state.browser.get_task(task_id)
            if wants_json(self.headers, query):
                payload = detail.to_dict()
                payload["db_path"] = str(state.db_path)
                payload["read_only"] = True
                self._respond_json(200, payload)
                return
            body = render_task_detail(detail, db_path=str(state.db_path))
            self._respond(200, body, content_type="text/html; charset=utf-8")

        def _respond(self, status: int, body: str, *, content_type: str) -> None:
            payload = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

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
) -> ThreadingHTTPServer:
    """Create and bind the browser server. Caller owns serve_forever/shutdown."""
    state = _BrowserState(browser=browser, db_path=Path(db_path).expanduser())
    handler = create_handler(state)
    return ThreadingHTTPServer((host, port), handler)
