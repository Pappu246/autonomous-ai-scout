from __future__ import annotations

import os
from http.server import ThreadingHTTPServer

from .vercel_proxy import ScoutVercelProxyHandler


def _server_port(raw_port: str) -> int:
    """Parse the platform port; container services may legitimately use port 80."""
    try:
        port = int(raw_port)
    except (TypeError, ValueError) as exc:
        raise SystemExit("PORT must be an integer") from exc
    if not 1 <= port <= 65535:
        raise SystemExit("PORT must be between 1 and 65535")
    return port


def main() -> None:
    """Run the bounded Vercel gateway as a private container service."""
    port = _server_port(os.getenv("PORT", "3000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), ScoutVercelProxyHandler)
    server.daemon_threads = True
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
