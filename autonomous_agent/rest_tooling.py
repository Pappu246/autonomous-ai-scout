from __future__ import annotations

from typing import Any, Mapping

from .rest_connector import RestConnector, RestRequest


def execute_rest_tool(connector: RestConnector, request: Mapping[str, Any], *, approved: bool = False, resolve_dns: bool = False) -> dict[str, Any]:
    if not isinstance(request, Mapping):
        raise ValueError("REST request must be structured")
    method = str(request.get("method", "GET")).upper().strip()
    url = str(request.get("url", ""))
    body_value = request.get("body", "")
    if isinstance(body_value, bytes):
        body = body_value
    else:
        body = str(body_value).encode("utf-8")
    headers = request.get("headers", {})
    if not isinstance(headers, Mapping):
        raise ValueError("REST headers must be an object")
    return connector.safe_json(
        RestRequest(
            method=method,
            url=url,
            headers={str(k): str(v) for k, v in headers.items()},
            body=body,
            credential_ref=(str(request["credential_ref"]) if request.get("credential_ref") else None),
            idempotency_key=(str(request["idempotency_key"]) if request.get("idempotency_key") else None),
        ),
        approved=approved,
        resolve_dns=resolve_dns,
    )
