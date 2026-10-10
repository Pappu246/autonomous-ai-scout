from __future__ import annotations

import json
from pathlib import Path

from autonomous_agent.release_discovery import discover_releases
from autonomous_agent.sources import SourceCheck


def test_release_discovery_sanitizes_html_headlines(monkeypatch):
    html = "<html><head><title>Official Changelog</title></head><body><h2>September 11, 2026</h2><p>Released a new model</p></body></html>"
    monkeypatch.setattr("autonomous_agent.release_discovery.fetch_source", lambda url: SourceCheck(url, True, text=html))
    findings = discover_releases(
        [{"id": "demo", "enabled": True, "release_sources": ["https://official.example/changelog"]}],
        {},
    )
    assert len(findings) == 1
    assert "September 11, 2026" in findings[0].headline
    assert "<html>" not in findings[0].headline


def test_release_discovery_keeps_plain_text_behavior(monkeypatch):
    monkeypatch.setattr(
        "autonomous_agent.release_discovery.fetch_source",
        lambda url: SourceCheck(url, True, text="## Release\nNew model launched"),
    )
    findings = discover_releases(
        [{"id": "demo", "enabled": True, "release_sources": ["https://official.example/changelog"]}],
        {},
    )
    assert findings[0].headline == "Release"


def test_release_sources_are_official_announcements_and_registry_models_are_current():
    config = json.loads(Path("config/providers.json").read_text(encoding="utf-8"))
    providers = {item["id"]: item for item in config["providers"]}

    assert providers["openrouter"]["release_sources"] == ["https://openrouter.ai/blog/announcements/"]
    assert all("models?pricing=free" not in url for url in providers["openrouter"]["release_sources"])
    assert "gemini-3.8-flash" in providers["gemini"]["models"]
    assert "gemini-3.7-flash" not in providers["gemini"]["models"]
