from __future__ import annotations

import json
from pathlib import Path

import pytest

from autonomous_agent.vercel_gateway_server import _server_port
from autonomous_agent.vercel_proxy import normalize_backend_origin, resolve_request_path, target_is_allowed


def test_backend_origin_accepts_https_origin_only() -> None:
    assert normalize_backend_origin("https://scout.example.azurecontainerapps.io") == "https://scout.example.azurecontainerapps.io"
    assert normalize_backend_origin("https://scout.example.azurecontainerapps.io/") == "https://scout.example.azurecontainerapps.io"


@pytest.mark.parametrize(
    "value",
    [
        "",
        "http://scout.example.azurecontainerapps.io",
        "https://scout.example.azurecontainerapps.io/api",
        "https://scout.example.azurecontainerapps.io?x=1",
        "https://user:password@scout.example.azurecontainerapps.io",
        "//scout.example.azurecontainerapps.io",
    ],
)
def test_backend_origin_rejects_non_origin_urls(value: str) -> None:
    with pytest.raises(ValueError):
        normalize_backend_origin(value)


def test_gateway_allows_only_mission_control_routes_and_expected_methods() -> None:
    assert target_is_allowed("/", "GET")
    assert target_is_allowed("/health", "GET")
    assert target_is_allowed("/api/missions", "GET")
    assert target_is_allowed("/api/missions/mission-123/timeline", "GET")
    assert target_is_allowed("/api/approvals", "GET")
    assert target_is_allowed("/api/missions", "POST")
    assert target_is_allowed("/api/missions/mission-123/resume", "POST")
    assert target_is_allowed("/api/approvals/action-123/approve", "POST")
    assert target_is_allowed("/login", "GET")
    assert target_is_allowed("/login", "POST")
    assert target_is_allowed("/logout", "POST")


@pytest.mark.parametrize(
    ("path", "method"),
    [
        ("/api/proxy", "GET"),
        ("//evil.example/", "GET"),
        ("https://evil.example/", "GET"),
        ("/../etc/passwd", "GET"),
        ("/api/missions", "DELETE"),
        ("/run?task=send-email", "GET"),
        ("/api/missions/mission-123/delete", "POST"),
        ("/api/missions/mission-123/extra/cancel", "POST"),
        ("/api/missions//resume", "POST"),
        ("/api/approvals/action-123/extra/approve", "POST"),
        ("/api/approvals//approve", "POST"),
        ("/api/approvals/action-123/approve", "GET"),
        ("/unknown", "POST"),
    ],
)
def test_gateway_rejects_routes_outside_the_bounded_contract(path: str, method: str) -> None:
    assert not target_is_allowed(path, method)


def test_vercel_services_route_through_gateway_to_internal_runtime() -> None:
    root = Path(__file__).resolve().parents[1]
    config = json.loads((root / "vercel.json").read_text(encoding="utf-8"))

    services = config["services"]
    gateway = services["gateway"]
    runtime = services["runtime"]

    assert gateway["runtime"] == "container"
    assert gateway["entrypoint"] == "Dockerfile.vercel"
    assert gateway["bindings"] == [
        {
            "type": "service",
            "service": "runtime",
            "format": "url",
            "env": "SCOUT_API_ORIGIN",
        }
    ]
    assert runtime["runtime"] == "container"
    assert runtime["entrypoint"] == "Dockerfile"
    assert config["rewrites"]
    assert all(route["destination"]["service"] == "gateway" for route in config["rewrites"])



@pytest.mark.parametrize("raw, expected", [("80", 80), ("3000", 3000), ("65535", 65535)])
def test_vercel_gateway_accepts_valid_platform_ports(raw: str, expected: int) -> None:
    assert _server_port(raw) == expected


@pytest.mark.parametrize("raw", ["", "abc", "0", "-1", "65536"])
def test_vercel_gateway_rejects_invalid_platform_ports(raw: str) -> None:
    with pytest.raises(SystemExit):
        _server_port(raw)



@pytest.mark.parametrize(
    ("request_target", "method", "expected"),
    [
        ("/", "GET", "/"),
        ("/login", "GET", "/login"),
        ("/login?vercel-routing=1", "GET", "/login"),
        ("/health", "GET", "/health"),
        ("/health?vercel-routing=1", "GET", "/health"),
        ("/api/missions/mission-123/timeline", "GET", "/api/missions/mission-123/timeline"),
        ("/api/proxy?path=/login", "GET", "/login"),
        ("/api/proxy?path=/api/missions", "POST", "/api/missions"),
    ],
)
def test_request_path_supports_direct_services_and_legacy_proxy_targets(
    request_target: str, method: str, expected: str
) -> None:
    assert resolve_request_path(request_target, method) == expected


@pytest.mark.parametrize(
    ("request_target", "method"),
    [
        ("/api/proxy", "GET"),
        ("/not-allowed", "GET"),
        ("/login?path=/api/missions", "GET"),
        ("/api/proxy?path=/unknown", "GET"),
        ("/api/proxy?path=/login&path=/logout", "GET"),
        ("/api/proxy?other=value", "GET"),
        ("/api/approvals/abc/approve", "GET"),
    ],
)
def test_request_path_rejects_ambiguous_or_unlisted_targets(
    request_target: str, method: str
) -> None:
    assert resolve_request_path(request_target, method) == ""


def test_mission_control_ui_has_bounded_api_body_reads_and_visible_failures() -> None:
    from autonomous_agent.server import _mission_ui

    page = _mission_ui()
    assert "const timeout = window.setTimeout(() => controller.abort(), 8000)" in page
    assert "body = await response.json().catch(() => ({}));" in page
    assert "Promise.allSettled" in page
    assert "Unable to load missions:" in page
    assert "Unable to load approvals:" in page
    assert "if (refreshInProgress) return {missionListLoaded: false" in page
    assert "setInterval(refresh" not in page
    assert "lastActionNotice" in page
    assert "lastRefreshMissionIds" in page
    assert "refreshResult.missionListLoaded" in page
    assert "Shared state is inconsistent" in page
    assert r".join('\n') || 'No matching mission memory.'" in page
