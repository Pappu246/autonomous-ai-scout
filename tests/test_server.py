from __future__ import annotations

import json
import threading
import urllib.request

from autonomous_agent.server import RuntimeHandler
from http.server import ThreadingHTTPServer


def test_health_endpoint_reports_runtime_status():
    server = ThreadingHTTPServer(("127.0.0.1", 0), RuntimeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/health", timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert response.status == 200
        assert payload == {"service": "autonomous-ai-scout", "status": "ok"}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
