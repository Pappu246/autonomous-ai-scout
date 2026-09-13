import pytest

from autonomous_agent.browser_connector import ControlledBrowser, browser_transport_from_mapping, validate_browser_url


def test_validate_browser_url_is_https_allowlisted_and_default_port_only():
    assert validate_browser_url("https://example.com/docs", frozenset({"example.com"})) == "https://example.com/docs"
    with pytest.raises(ValueError):
        validate_browser_url("http://example.com/docs", frozenset({"example.com"}))
    with pytest.raises(ValueError):
        validate_browser_url("https://example.org/docs", frozenset({"example.com"}))
    with pytest.raises(ValueError):
        validate_browser_url("https://user:pass@example.com/docs", frozenset({"example.com"}))
    with pytest.raises(ValueError):
        validate_browser_url("https://example.com:8443/docs", frozenset({"example.com"}))
    with pytest.raises(ValueError):
        validate_browser_url("file:///etc/passwd", frozenset({"example.com"}))


def test_controlled_browser_uses_injected_transport_and_bounds_output():
    transport = browser_transport_from_mapping({
        "open": {"status_code": 200, "title": "Example", "text": "hello"},
        "click": {"status_code": 200, "title": "Next", "text": "clicked"},
        "extract": {"status_code": 200, "title": "Example", "text": "facts", "links": [{"href": "https://example.com/a", "label": "A"}]},
    })
    browser = ControlledBrowser({"example.com"}, transport)
    assert browser.open("https://example.com", timeout_seconds=999).safe_dict()["status_code"] == 200
    assert browser.click("https://example.com", "#next").text == "clicked"
    assert browser.extract("https://example.com", ("title", "price")).links[0]["label"] == "A"


def test_browser_rejects_invalid_selector():
    browser = ControlledBrowser({"example.com"}, lambda *_: {})
    with pytest.raises(ValueError):
        browser.click("https://example.com", "")
