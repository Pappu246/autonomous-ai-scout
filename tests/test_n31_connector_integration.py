from pathlib import Path

import pytest

from autonomous_agent.external_side_effects import ExternalSideEffectStore
from autonomous_agent.gmail_connector import GmailConnector, GmailError
from autonomous_agent.rest_connector import RestConnector, RestRequest, RestResponse


class GmailTransport:
    def __init__(self):
        self.calls = 0

    def request(self, method, url, *, params=None, body=None, timeout_seconds=10):
        self.calls += 1
        return {"id": f"draft-{self.calls}"}


class RestTransport:
    def __init__(self):
        self.calls = 0

    def request(self, method, url, *, headers, body, timeout):
        self.calls += 1
        return RestResponse(200, {"content-type": "application/json"}, b"{}", url)


def test_gmail_draft_persists_side_effect_claim(tmp_path: Path):
    store = ExternalSideEffectStore(tmp_path / "effects.json")
    transport = GmailTransport()
    connector = GmailConnector(transport, side_effect_store=store)
    connector.draft(to="a@example.com", subject="hello", body="body", approved=True)
    assert transport.calls == 1

    reopened = GmailConnector(transport, side_effect_store=ExternalSideEffectStore(tmp_path / "effects.json"))
    with pytest.raises(GmailError, match="already been executed|unresolved"):
        reopened.draft(to="a@example.com", subject="hello", body="body", approved=True)
    assert transport.calls == 1


def test_rest_write_persists_side_effect_claim(tmp_path: Path):
    store = ExternalSideEffectStore(tmp_path / "effects.json")
    transport = RestTransport()
    connector = RestConnector({"example.com"}, transport=transport, side_effect_store=store)
    request = RestRequest("POST", "https://example.com/a", {}, b"data")
    connector.request(request, approved=True, resolve_dns=False)
    assert transport.calls == 1

    reopened = RestConnector(
        {"example.com"},
        transport=transport,
        side_effect_store=ExternalSideEffectStore(tmp_path / "effects.json"),
    )
    with pytest.raises(Exception, match="already been executed|unresolved"):
        reopened.request(request, approved=True, resolve_dns=False)
    assert transport.calls == 1
