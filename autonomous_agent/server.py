from __future__ import annotations

import argparse
import hmac
import ipaddress
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .execution_engine import ExecutionState
from .mission_control import MissionController
from .runtime import run_task


AUTH_HEADER = "X-Autonomous-Scout-Token"
MAX_TASK_LENGTH = 4_000
MAX_JSON_BODY_BYTES = 8_192


def _is_loopback_host(host: str) -> bool:
    normalized = str(host).strip().lower()
    if normalized in {"localhost", "127.0.0.1", "::1"}:
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _validate_bind_security(host: str, token: str) -> bool:
    require_auth = not _is_loopback_host(host)
    if require_auth and not token:
        raise RuntimeError(
            "SCOUT_SERVER_TOKEN is required when the runtime server binds to a non-loopback host"
        )
    return require_auth


def _mission_ui() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="Content-Security-Policy" content="default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'">
  <title>Autonomous AI Scout</title>
  <style>
    :root { color-scheme: dark; font-family: system-ui, sans-serif; }
    body { margin: 0; background: #0b1020; color: #edf2f7; }
    main { max-width: 980px; margin: 0 auto; padding: 32px 20px 60px; }
    h1 { margin-bottom: 6px; }
    .muted { color: #a0aec0; }
    .panel { background: #111827; border: 1px solid #243044; border-radius: 14px; padding: 18px; margin-top: 20px; }
    textarea { width: 100%; min-height: 110px; box-sizing: border-box; background: #0f172a; color: #fff; border: 1px solid #334155; border-radius: 10px; padding: 12px; resize: vertical; }
    button { margin-top: 12px; border: 0; border-radius: 9px; padding: 10px 16px; cursor: pointer; background: #2563eb; color: white; font-weight: 700; }
    button.secondary { background: #334155; }
    .mission { border-top: 1px solid #243044; padding: 14px 0; }
    .mission:first-child { border-top: 0; }
    .pill { display: inline-block; border-radius: 999px; padding: 3px 8px; font-size: 12px; background: #243044; margin-left: 6px; }
    .role { display: inline-block; border-radius: 999px; padding: 3px 8px; font-size: 12px; background: #1d4ed8; margin-left: 6px; }
    pre { white-space: pre-wrap; word-break: break-word; color: #cbd5e1; }
  </style>
</head>
<body>
<main>
  <h1>Autonomous AI Scout</h1>
  <div class="muted">Mission Control · durable missions · specialist routing · persistent memory · verified results</div>
  <section class="panel">
    <form id="mission-form">
      <label for="task">Give Scout a digital goal</label>
      <textarea id="task" maxlength="4000" placeholder="Example: inspect the repository and summarize current test failures"></textarea>
      <br>
      <button type="submit">Start Mission</button>
      <button type="button" class="secondary" id="refresh">Refresh</button>
    </form>
    <div id="notice" class="muted"></div>
  </section>
  <section class="panel">
    <h2>Recent missions</h2>
    <div id="missions">Loading…</div>
  </section>
</main>
<script>
const notice = document.getElementById('notice');
const missions = document.getElementById('missions');

function esc(value) {
  const node = document.createElement('div');
  node.textContent = String(value ?? '');
  return node.innerHTML;
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.error || ('HTTP ' + response.status));
  return body;
}

function render(items) {
  if (!items.length) {
    missions.innerHTML = '<div class="muted">No missions yet.</div>';
    return;
  }
  missions.innerHTML = items.map(item => `
    <article class="mission">
      <div><strong>${esc(item.task)}</strong><span class="pill">${esc(item.state)}</span><span class="role">${esc(item.specialist_role)}</span></div>
      <div class="muted">ID: ${esc(item.mission_id)}</div>
      <pre>${esc(item.reason)}</pre>
      <div class="muted">Updated: ${esc(item.updated_at)} · attempts: ${esc(item.attempts)}</div>
      <button class="secondary" data-memory="${esc(item.mission_id)}">Memory</button>
      ${['pending','recovery_required'].includes(item.state) ? '<button class="secondary" data-cancel="' + esc(item.mission_id) + '">Cancel</button>' : ''}
      <pre id="memory-${esc(item.mission_id)}"></pre>
    </article>`
  ).join('');
  missions.querySelectorAll('[data-memory]').forEach(button => {
    button.addEventListener('click', async () => {
      try {
        const body = await api('/api/missions/' + encodeURIComponent(button.dataset.memory) + '/memory');
        const target = document.getElementById('memory-' + button.dataset.memory);
        target.textContent = body.memory.map(item => '[' + item.kind + '] ' + (item.data.summary || item.data.task || item.outcome)).join('\\n') || 'No matching mission memory.';
      } catch (error) { notice.textContent = error.message; }
    });
  });
  missions.querySelectorAll('[data-cancel]').forEach(button => {
    button.addEventListener('click', async () => {
      try {
        await api('/api/missions/' + encodeURIComponent(button.dataset.cancel) + '/cancel', {method: 'POST'});
        await refresh();
      } catch (error) { notice.textContent = error.message; }
    });
  });
}

async function refresh() {
  try {
    const body = await api('/api/missions');
    render(body.missions);
    notice.textContent = '';
  } catch (error) {
    notice.textContent = error.message;
  }
}

document.getElementById('mission-form').addEventListener('submit', async event => {
  event.preventDefault();
  const task = document.getElementById('task').value.trim();
  if (!task) return;
  try {
    await api('/api/missions', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({task})
    });
    document.getElementById('task').value = '';
    notice.textContent = 'Mission accepted.';
    await refresh();
  } catch (error) {
    notice.textContent = error.message;
  }
});

document.getElementById('refresh').addEventListener('click', refresh);
refresh();
setInterval(refresh, 2000);
</script>
</body>
</html>"""


class RuntimeHandler(BaseHTTPRequestHandler):
    server_version = "AutonomousAIScout/0.3"

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _text(self, status: int, body: str, *, content_type: str = "text/html; charset=utf-8") -> None:
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _authorized(self) -> bool:
        if not getattr(self.server, "require_auth", False):
            return True
        expected = getattr(self.server, "server_token", "")
        provided = self.headers.get(AUTH_HEADER, "")
        return bool(expected) and hmac.compare_digest(provided, expected)

    def _controller(self) -> MissionController | None:
        return getattr(self.server, "mission_controller", None)

    def _mission_payload(self, record) -> dict:
        return {
            "mission_id": record.mission_id,
            "task_id": record.task_id,
            "execution_id": record.execution_id,
            "task": record.task,
            "state": record.state,
            "reason": record.reason,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
            "attempts": record.attempts,
            "specialist_role": record.specialist_role,
        }

    def _request_json(self) -> dict:
        raw_length = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise ValueError("invalid Content-Length") from exc
        if length < 0 or length > MAX_JSON_BODY_BYTES:
            raise ValueError("request body is too large")
        body = self.rfile.read(length)
        if len(body) != length:
            raise ValueError("incomplete request body")
        payload = json.loads(body.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("request body must be a JSON object")
        return payload

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._json(200, {"status": "ok", "service": "autonomous-ai-scout"})
            return
        if not self._authorized():
            self._json(401, {"error": "authentication required"})
            return
        if parsed.path == "/":
            self._text(200, _mission_ui())
            return
        if parsed.path == "/run":
            query = parse_qs(parsed.query)
            task = query.get("task", [os.getenv("TASK_REQUEST", "inspect repository")])[0]
            if len(task) > MAX_TASK_LENGTH:
                self._json(400, {"error": "task exceeds maximum length"})
                return
            result = run_task(
                task,
                root=Path.cwd(),
                audit_path=Path.cwd() / "state" / "runtime_execution.jsonl",
            )
            self._json(200 if result.state is ExecutionState.VERIFIED else 422, {
                "state": result.state.value,
                "reason": result.reason,
                "attempts": result.attempts,
                "results": [
                    {"operation": item.operation, "success": item.success, "verification": item.verification_status, "output": item.output}
                    for item in result.results
                ],
                "audit_path": result.audit_path,
            })
            return
        if parsed.path == "/api/missions":
            controller = self._controller()
            if controller is None:
                self._json(503, {"error": "mission control is not running"})
                return
            self._json(200, {"missions": [self._mission_payload(item) for item in controller.store.list()]})
            return
        prefix = "/api/missions/"
        if parsed.path.startswith(prefix):
            controller = self._controller()
            mission_id = parsed.path[len(prefix):].strip("/")
            if controller is None or not mission_id or "/" in mission_id:
                self._json(404, {"error": "mission not found"})
                return
            record = controller.store.get(mission_id)
            if record is None:
                self._json(404, {"error": "mission not found"})
                return
            if parsed.path.endswith("/memory"):
                matches = controller.memory.recall(controller.MEMORY_PROJECT, record.task, limit=5)
                self._json(200, {"memory": [
                    {
                        "score": item.score,
                        "kind": item.kind,
                        "outcome": item.outcome,
                        "data": dict(item.data),
                    }
                    for item in matches
                ]})
                return
            self._json(200, {"mission": self._mission_payload(record)})
            return
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if not self._authorized():
            self._json(401, {"error": "authentication required"})
            return
        controller = self._controller()
        if controller is None:
            self._json(503, {"error": "mission control is not running"})
            return
        if parsed.path == "/api/missions":
            try:
                payload = self._request_json()
                task = str(payload.get("task", "")).strip()
                if not task:
                    raise ValueError("task is required")
                if len(task) > MAX_TASK_LENGTH:
                    raise ValueError("task exceeds maximum length")
                steps = payload.get("steps")
                record = controller.submit_plan(task, steps) if steps is not None else controller.submit(task)
            except (TypeError, ValueError) as exc:
                self._json(400, {"error": str(exc)})
                return
            self._json(202 if record.state == "pending" else 422, {"mission": self._mission_payload(record)})
            return
        prefix = "/api/missions/"
        if parsed.path.startswith(prefix) and parsed.path.endswith("/cancel"):
            mission_id = parsed.path[len(prefix):-len("/cancel")].strip("/")
            try:
                record = controller.cancel(mission_id)
            except KeyError:
                self._json(404, {"error": "mission not found"})
                return
            except ValueError as exc:
                self._json(409, {"error": str(exc)})
                return
            self._json(200, {"mission": self._mission_payload(record)})
            return
        self._json(404, {"error": "not found"})

    def log_message(self, format: str, *args: object) -> None:
        return


def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    token = os.getenv("SCOUT_SERVER_TOKEN", "").strip()
    require_auth = _validate_bind_security(host, token)
    server = ThreadingHTTPServer((host, int(port)), RuntimeHandler)
    server.require_auth = require_auth
    server.server_token = token
    server.mission_controller = MissionController(root=Path.cwd())
    server.mission_controller.start()
    print(f"Autonomous AI Scout runtime listening on http://{host}:{port}")
    print(f"Mission Control UI: http://{host}:{port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.mission_controller.stop()
        server.server_close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve the local Autonomous AI Scout runtime")
    parser.add_argument("--host", default=os.getenv("SCOUT_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("SCOUT_PORT", "8000")))
    args = parser.parse_args(list(argv) if argv is not None else None)
    serve(args.host, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
