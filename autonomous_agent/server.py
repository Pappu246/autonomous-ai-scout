from __future__ import annotations

import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .execution_engine import ExecutionState
from .runtime import run_task


class RuntimeHandler(BaseHTTPRequestHandler):
    server_version = "AutonomousAIScout/0.1"

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._json(200, {"status": "ok", "service": "autonomous-ai-scout"})
            return
        if parsed.path == "/run":
            query = parse_qs(parsed.query)
            task = query.get("task", [os.getenv("TASK_REQUEST", "inspect repository")])[0]
            result = run_task(task, root=Path.cwd(), audit_path=Path.cwd() / "state" / "runtime_execution.jsonl")
            self._json(200 if result.state is ExecutionState.VERIFIED else 422, {
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
            })
            return
        self._json(404, {"error": "not found"})

    def log_message(self, format: str, *args: object) -> None:
        return


def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    server = ThreadingHTTPServer((host, int(port)), RuntimeHandler)
    print(f"Autonomous AI Scout runtime listening on http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve the local Autonomous AI Scout runtime")
    parser.add_argument("--host", default=os.getenv("SCOUT_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("SCOUT_PORT", "8000")))
    args = parser.parse_args(argv)
    serve(args.host, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
