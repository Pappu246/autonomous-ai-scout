from __future__ import annotations

import pytest

from autonomous_agent.vercel_proxy import normalize_backend_origin, target_is_allowed


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
