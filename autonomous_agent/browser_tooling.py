from __future__ import annotations

from typing import Any, Mapping

from .browser_connector import ControlledBrowser


def execute_browser_tool(
    browser: ControlledBrowser,
    request: Mapping[str, Any],
) -> dict[str, Any]:
    """Execute one bounded browser operation through the injected browser transport."""
    if not isinstance(request, Mapping):
        raise ValueError("browser request must be a structured mapping")
    operation = str(request.get("operation", "")).strip().lower()
    if operation == "open":
        return browser.open(
            str(request.get("url", "")),
            timeout_seconds=int(request.get("timeout_seconds", 20)),
        ).safe_dict()
    if operation == "click":
        return browser.click(
            str(request.get("url", "")),
            str(request.get("selector", "")),
            timeout_seconds=int(request.get("timeout_seconds", 20)),
        ).safe_dict()
    if operation == "extract":
        fields = request.get("fields", ())
        if not isinstance(fields, (list, tuple)):
            raise ValueError("browser extract fields must be a list")
        return browser.extract(str(request.get("url", "")), tuple(str(x) for x in fields)).safe_dict()
    raise ValueError("browser allowlist supports only open/click/extract")
