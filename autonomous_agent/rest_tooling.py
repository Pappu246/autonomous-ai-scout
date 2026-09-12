from __future__ import annotations

from typing import Any, Mapping

from .rest_connector import RestConnector, RestRequest


def execute_rest_tool(
    connector: RestConnector,
    request: Mapping[str, Any],
    *,
    approved: bool = False,
    resolve_dns: bool = True,
) -> dict[str, Any]:
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
    credential_ref = request.get("credential_ref")
    if credential_ref is not None and not isinstance(credential_ref, str):
        raise ValueError("credential_ref must be a string reference")
    return connector.safe_json(
        RestRequest(
            method=method,
            url=url,
            headers={str(k): str(v) for k, v in headers.items()},
            body=body,
            credential_ref=credential_ref.strip() if isinstance(credential_ref, str) and credential_ref.strip() else None,
            idempotency_key=(str(request["idempotency_key"]) if request.get("idempotency_key") else None),
        ),
        approved=approved,
        resolve_dns=resolve_dns,
    )
