
from __future__ import annotations

import argparse
import json
import os
import secrets
import threading
import time
import webbrowser
from dataclasses import asdict, dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .action_queue import load_queue, prioritize_queue
from .execution_engine import ExecutionState
from .run_journal import read_run_records
from .runtime import run_task

ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = ROOT / "web"
MAX_TASKS = 32
MAX_ACTIVE_TASKS = 4
MAX_TASK_LENGTH = 4000


@dataclass
class RuntimeTask:
    task_id: str
    execution_id: str
    task: str
    state: str = "queued"
    reason: str = ""
    attempts: int = 0
    submitted_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    results: list[dict[str, Any]] = field(default_factory=list)
    explicitly_approved: bool = False


class RuntimeTaskManager:
    """Bounded in-process task launcher behind the browser control surface."""

    def __init__(self, root: Path, *, max_active: int = MAX_ACTIVE_TASKS, max_retained: int = MAX_TASKS) -> None:
        self.root = root.resolve()
        self.max_active = max(1, min(int(max_active), MAX_ACTIVE_TASKS))
        self.max_retained = max(8, min(int(max_retained), MAX_TASKS))
        self._lock = threading.RLock()
        self._tasks: dict[str, RuntimeTask] = {}

    def _active_count(self) -> int:
        return sum(item.state in {"queued", "running"} for item in self._tasks.values())

    @staticmethod
    def _clean_task(value: object) -> str:
        task = " ".join(str(value).strip().split())
        if not task:
            raise ValueError("task is required")
        if len(task) > MAX_TASK_LENGTH:
            raise ValueError("task exceeds 4000 characters")
        return task

    def submit(self, task: object, *, explicitly_approved: bool = False) -> RuntimeTask:
        task_text = self._clean_task(task)
        with self._lock:
            if self._active_count() >= self.max_active:
                raise RuntimeError("runtime task capacity is full")
            task_id = secrets.token_hex(8)
            execution_id = secrets.token_hex(8)
            record = RuntimeTask(
                task_id=task_id,
                execution_id=execution_id,
                task=task_text,
                explicitly_approved=bool(explicitly_approved),
            )
            self._tasks[task_id] = record
            self._trim_locked()
            threading.Thread(target=self._run, args=(task_id,), daemon=True).start()
            return self._copy(record)

    def _run(self, task_id: str) -> None:
        with self._lock:
            record = self._tasks.get(task_id)
            if record is None:
                return
            record.state = "running"
            record.started_at = time.time()
            execution_id = record.execution_id
            task = record.task
        try:
            audit_path = self.root / "state" / "runtime_execution.jsonl"
            journal_path = self.root / "state" / "runtime_runs.jsonl"
            result = run_task(
                task,
                root=self.root,
                audit_path=audit_path,
                journal_path=journal_path,
                execution_id=execution_id,
                explicitly_approved=record.explicitly_approved,
            )
            safe_results = [
                {
                    "operation": str(item.operation),
                    "success": bool(item.success),
                    "verification": str(item.verification_status),
                    "output": str(item.output)[:4000],
                }
                for item in result.results[:20]
            ]
            with self._lock:
                current = self._tasks.get(task_id)
                if current is not None:
                    current.state = result.state.value
                    current.reason = result.reason[:4096]
                    current.attempts = int(result.attempts)
                    current.results = safe_results
                    current.finished_at = time.time()
                    self._trim_locked()
        except Exception as exc:
            with self._lock:
                current = self._tasks.get(task_id)
                if current is not None:
                    current.state = ExecutionState.FAILED.value
                    current.reason = f"runtime worker failed: {type(exc).__name__}"[:4096]
                    current.finished_at = time.time()
                    self._trim_locked()

    def _trim_locked(self) -> None:
        if len(self._tasks) <= self.max_retained:
            return
        completed = sorted(
            (item for item in self._tasks.values() if item.state not in {"queued", "running"}),
            key=lambda item: item.finished_at or item.submitted_at,
        )
        for item in completed[: max(0, len(self._tasks) - self.max_retained)]:
            self._tasks.pop(item.task_id, None)

    @staticmethod
    def _copy(item: RuntimeTask) -> RuntimeTask:
        return RuntimeTask(
            task_id=item.task_id,
            execution_id=item.execution_id,
            task=item.task,
            state=item.state,
            reason=item.reason,
            attempts=item.attempts,
            submitted_at=item.submitted_at,
            started_at=item.started_at,
            finished_at=item.finished_at,
            results=list(item.results),
            explicitly_approved=item.explicitly_approved,
        )

    @staticmethod
    def _to_dict(item: RuntimeTask) -> dict[str, Any]:
        return {
            **asdict(item),
            "elapsed_seconds": round(
                max(0.0, (item.finished_at or time.time()) - (item.started_at or item.submitted_at)),
                2,
            ),
            "approval_required": item.state == ExecutionState.BLOCKED.value and any(
                marker in item.reason.lower()
                for marker in ("approval required", "requires explicit approval")
            ),
        }

    def get(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            item = self._tasks.get(task_id)
            return None if item is None else self._to_dict(item)

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            values = [self._to_dict(item) for item in self._tasks.values()]
        return sorted(values, key=lambda item: item["submitted_at"], reverse=True)


_DEFAULT_MANAGER = RuntimeTaskManager(Path.cwd())


class RuntimeHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address, request_handler_class, runtime_manager: RuntimeTaskManager):
        super().__init__(server_address, request_handler_class)
        self.runtime_manager = runtime_manager


class RuntimeHandler(BaseHTTPRequestHandler):
    server_version = "AutonomousAIScout/0.2"

    def _manager(self) -> RuntimeTaskManager:
        return getattr(self.server, "runtime_manager", _DEFAULT_MANAGER)

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _static(self, relative: str, content_type: str) -> None:
        candidate = (WEB_ROOT / relative).resolve()
        if WEB_ROOT.resolve() not in candidate.parents or not candidate.is_file():
            self._json(404, {"error": "not found"})
            return
        data = candidate.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _bad_request(self, message: str) -> None:
        self._json(400, {"error": message})

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path in {"/", "/index.html"}:
            return self._static("index.html", "text/html; charset=utf-8")
        if path == "/assets/styles.css":
            return self._static("styles.css", "text/css; charset=utf-8")
        if path == "/assets/app.js":
            return self._static("app.js", "application/javascript; charset=utf-8")

        if path in {"/health", "/api/health"}:
            manager = self._manager()
            self._json(200, {"status": "ok", "service": "autonomous-ai-scout", "root_label": manager.root.name or str(manager.root)})
            return

        if path == "/api/tasks":
            manager = self._manager()
            self._json(200, {"root_label": manager.root.name or str(manager.root), "tasks": manager.list()})
            return

        if path.startswith("/api/tasks/"):
            item = self._manager().get(path.rsplit("/", 1)[-1].strip())
            if item is None:
                self._json(404, {"error": "task not found"})
            else:
                self._json(200, item)
            return

        if path == "/api/history":
            try:
                requested = int(parse_qs(parsed.query).get("limit", ["30"])[0])
                limit = max(1, min(requested, 100))
                all_records = list(read_run_records(self._manager().root / "state" / "runtime_runs.jsonl", limit=1000))
                records = all_records[-limit:]
                records.reverse()
                self._json(200, {"records": [asdict(record) for record in records]})
            except (ValueError, OSError) as exc:
                self._json(422, {"error": str(exc)})
            return

        if path == "/api/approvals":
            try:
                queue_path = self._manager().root / "state" / "approval_queue.json"
                actions = prioritize_queue(load_queue(queue_path))[:50]
                self._json(
                    200,
                    {
                        "pending_count": sum(item.status == "pending" for item in actions),
                        "actions": [asdict(item) for item in actions],
                    },
                )
            except (ValueError, OSError) as exc:
                self._json(422, {"error": str(exc)})
            return

        if path == "/api/report":
            dashboard = self._manager().root / "state" / "dashboard.md"
            report = self._manager().root / "state" / "latest_report.md"
            self._json(
                200,
                {
                    "dashboard": dashboard.read_text(encoding="utf-8")[:32768] if dashboard.exists() else "",
                    "report": report.read_text(encoding="utf-8")[:32768] if report.exists() else "",
                },
            )
            return

        if path == "/run":
            query = parse_qs(parsed.query)
            task = query.get("task", [os.getenv("TASK_REQUEST", "inspect repository")])[0]
            manager = self._manager()
            result = run_task(
                task,
                root=manager.root,
                audit_path=manager.root / "state" / "runtime_execution.jsonl",
                journal_path=manager.root / "state" / "runtime_runs.jsonl",
            )
            self._json(
                200 if result.state is ExecutionState.VERIFIED else 422,
                {
                    "state": result.state.value,
                    "reason": result.reason,
                    "attempts": result.attempts,
                    "results": [
                        {
                            "operation": item.operation,
                            "success": item.success,
                            "verification": item.verification_status,
                            "output": item.output,
                        }
                        for item in result.results
                    ],
                    "audit_path": result.audit_path,
                },
            )
            return

        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        parsed_path = urlparse(self.path).path
        if parsed_path.startswith("/api/tasks/") and parsed_path.endswith("/approve"):
            task_id = parsed_path[len("/api/tasks/"):-len("/approve")].strip("/ ")
            if not task_id:
                return self._bad_request("task id is required")
            current = self._manager().get(task_id)
            if current is None:
                return self._json(404, {"error": "task not found"})
            if not current.get("approval_required"):
                return self._json(409, {"error": "task is not awaiting approval"})
            try:
                item = self._manager().submit(current["task"], explicitly_approved=True)
            except RuntimeError as exc:
                return self._json(429, {"error": str(exc)})
            self._json(
                202,
                {
                    "task_id": item.task_id,
                    "execution_id": item.execution_id,
                    "state": item.state,
                    "explicitly_approved": True,
                    "message": "approval accepted; task was re-queued for bounded execution",
                },
            )
            return
        if parsed_path != "/api/tasks":
            self._json(404, {"error": "not found"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return self._bad_request("invalid content length")
        if size <= 0 or size > 16_384:
            return self._bad_request("request body is missing or too large")
        try:
            payload = json.loads(self.rfile.read(size).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return self._bad_request("request must contain valid JSON")
        if not isinstance(payload, dict):
            return self._bad_request("request body must be a JSON object")
        approved = payload.get("approved", False)
        if not isinstance(approved, bool):
            return self._bad_request("approved must be a boolean")
        try:
            item = self._manager().submit(payload.get("task", ""), explicitly_approved=approved)
        except RuntimeError as exc:
            return self._json(429, {"error": str(exc)})
        except ValueError as exc:
            return self._bad_request(str(exc))
        self._json(
            202,
            {
                "task_id": item.task_id,
                "execution_id": item.execution_id,
                "state": item.state,
                "explicitly_approved": item.explicitly_approved,
                "message": "task accepted for bounded background execution",
            },
        )

    def log_message(self, format: str, *args: object) -> None:
        return


def serve(host: str = "127.0.0.1", port: int = 8000, *, root: Path | None = None, open_browser: bool = False) -> None:
    manager = RuntimeTaskManager((root or Path.cwd()).resolve())
    server = RuntimeHTTPServer((host, int(port)), RuntimeHandler, manager)
    url = f"http://{host}:{port}/"
    print(f"Autonomous AI Scout web console: {url}")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve the local Autonomous AI Scout web console")
    parser.add_argument("--host", default=os.getenv("SCOUT_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("SCOUT_PORT", "8000")))
    parser.add_argument("--root", default=os.getenv("SCOUT_ROOT", str(Path.cwd())))
    parser.add_argument("--open", action="store_true", help="open the web console in the default browser")
    args = parser.parse_args(argv)
    serve(args.host, args.port, root=Path(args.root), open_browser=args.open)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
