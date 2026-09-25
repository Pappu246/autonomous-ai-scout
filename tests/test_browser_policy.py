"""Policy tests for the advanced bounded browser agent (Phase 3, M1).

These tests lock down the navigation, redirect, host, download, filename and
credential boundaries. They are deterministic and never launch a browser.
"""

from __future__ import annotations

import pytest

from autonomous_agent.browser import policy
from autonomous_agent.browser.models import (
    BrowserSecurityError,
    PageElement,
    consequential_signal,
    is_credential_field,
    looks_like_secret,
    redact_secret,
)
from autonomous_agent.browser.policy import (
    ALLOWED_PORTS,
    BLOCKED_SCHEMES,
    bound_download_size,
    bound_response_size,
    confine_download_path,
    is_blocked_host,
    sanitize_filename,
    validate_download_url,
    validate_host_allowlist,
    validate_navigation_url,
    validate_redirect,
    validate_selector,
    validate_typed_text,
)

HOSTS = frozenset({"example.com", "shop.example.com"})


# --------------------------------------------------------------------------
# Host allowlist
# --------------------------------------------------------------------------
def test_allowlist_rejects_empty():
    with pytest.raises(BrowserSecurityError):
        validate_host_allowlist([])
    with pytest.raises(BrowserSecurityError):
        validate_host_allowlist(None)


def test_allowlist_normalizes_and_dedupes():
    result = validate_host_allowlist(["Example.COM.", "example.com", " shop.example.com "])
    assert result == frozenset({"example.com", "shop.example.com"})


def test_allowlist_refuses_loopback_and_metadata_even_if_listed():
    for bad in ("localhost", "127.0.0.1", "169.254.169.254", "10.0.0.5", "::1"):
        with pytest.raises(BrowserSecurityError):
            validate_host_allowlist([bad])


# --------------------------------------------------------------------------
# Blocked host detection
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "host",
    [
        "localhost",
        "127.0.0.1",
        "127.5.5.5",
        "0.0.0.0",
        "10.1.2.3",
        "192.168.1.10",
        "172.16.9.9",
        "169.254.169.254",
        "100.100.100.200",
        "::1",
        "fe80::1",
        "metadata.google.internal",
        "foo.internal",
        "svc.local",
    ],
)
def test_blocked_hosts(host):
    assert is_blocked_host(host) is True


@pytest.mark.parametrize("host", ["example.com", "shop.example.com", "a.b.c.example.org"])
def test_allowed_hosts_not_blocked(host):
    assert is_blocked_host(host) is False


# --------------------------------------------------------------------------
# Navigation URL validation
# --------------------------------------------------------------------------
def test_navigation_accepts_allowlisted_https():
    assert validate_navigation_url("https://example.com/path?q=1", HOSTS) == "example.com"


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/",                # not https
        "https://evil.com/",                  # not allowlisted
        "file:///etc/passwd",                 # forbidden scheme
        "javascript:alert(1)",                # forbidden scheme
        "data:text/html,<script>alert(1)</script>",
        "blob:https://example.com/uuid",
        "about:blank",
        "ftp://example.com/file",
        "https://user:pass@example.com/",     # embedded credentials
        "https://127.0.0.1/",                 # loopback
        "https://169.254.169.254/latest/meta-data/",  # metadata
        "https://10.0.0.1/",                  # private
        "https://example.com:8080/",          # unsafe port
        "https://example.com:22/",            # unsafe port
        "https://" + "a" * 3000 + ".com",     # too long
    ],
)
def test_navigation_rejects_unsafe(url):
    with pytest.raises(BrowserSecurityError):
        validate_navigation_url(url, HOSTS)


def test_blocked_scheme_set_is_comprehensive():
    for scheme in ("file", "javascript", "data", "blob", "about"):
        assert scheme in BLOCKED_SCHEMES


# --------------------------------------------------------------------------
# Redirect validation
# --------------------------------------------------------------------------
def test_redirect_accepts_allowlisted_hop():
    assert (
        validate_redirect("https://example.com/a", "https://shop.example.com/b", HOSTS, depth=0)
        == "shop.example.com"
    )


def test_redirect_rejects_off_allowlist_target():
    with pytest.raises(BrowserSecurityError):
        validate_redirect("https://example.com/", "https://evil.com/", HOSTS, depth=0)


def test_redirect_rejects_scheme_downgrade():
    with pytest.raises(BrowserSecurityError):
        validate_redirect("https://example.com/", "http://example.com/", HOSTS, depth=1)


def test_redirect_depth_is_bounded():
    from autonomous_agent.browser.models import MAX_REDIRECTS

    with pytest.raises(BrowserSecurityError):
        validate_redirect("https://example.com/", "https://example.com/x", HOSTS, depth=MAX_REDIRECTS)


def test_redirect_source_is_revalidated():
    with pytest.raises(BrowserSecurityError):
        validate_redirect("https://evil.com/", "https://example.com/", HOSTS, depth=0)


# --------------------------------------------------------------------------
# Download URL + sizing
# --------------------------------------------------------------------------
def test_download_url_uses_same_policy():
    assert validate_download_url("https://example.com/file.pdf", HOSTS) == "example.com"
    with pytest.raises(BrowserSecurityError):
        validate_download_url("https://evil.com/file", HOSTS)


def test_response_size_bound():
    assert bound_response_size(1000) == 1000
    with pytest.raises(BrowserSecurityError):
        bound_response_size(10_000_000_000)


def test_download_size_bound():
    assert bound_download_size(1000) == 1000
    with pytest.raises(BrowserSecurityError):
        bound_download_size(10_000_000_000)


# --------------------------------------------------------------------------
# Filename sanitization and path confinement
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("report.pdf", "report.pdf"),
        ("../../etc/passwd", "passwd"),
        ("..\\..\\windows\\system32\\cmd.exe", "cmd.exe"),
        ("a/b/c.txt", "c.txt"),
        ("", "download.bin"),
        ("...", "download.bin"),
        ("weird name!!.txt", "weird_name_.txt"),
        ("\x00null.txt", "null.txt"),
    ],
)
def test_sanitize_filename(raw, expected):
    assert sanitize_filename(raw) == expected


def test_confine_download_path_stays_in_root(tmp_path):
    dest, rel = confine_download_path(tmp_path, "safe.txt")
    assert rel == "safe.txt"
    assert str(dest).startswith(str(tmp_path.resolve()))
    assert dest.name == "safe.txt"


def test_confine_download_path_blocks_traversal(tmp_path):
    # Even a traversal-looking name is sanitized to a single safe segment.
    dest, rel = confine_download_path(tmp_path, "../../escape.txt")
    assert ".." not in rel
    assert str(dest).startswith(str(tmp_path.resolve()))


# --------------------------------------------------------------------------
# Selector / typed text
# --------------------------------------------------------------------------
def test_validate_selector_bounds():
    assert validate_selector("  button#submit  ") == "button#submit"
    with pytest.raises(BrowserSecurityError):
        validate_selector("")
    with pytest.raises(BrowserSecurityError):
        validate_selector("x" * 5000)
    with pytest.raises(BrowserSecurityError):
        validate_selector("a\x00b")


def test_validate_typed_text_blocks_secrets():
    assert validate_typed_text("hello world") == "hello world"
    with pytest.raises(BrowserSecurityError):
        validate_typed_text("authorization: Bearer abcdef123456")
    with pytest.raises(BrowserSecurityError):
        validate_typed_text("api_key=SUPERSECRETVALUE")


# --------------------------------------------------------------------------
# Credential handling
# --------------------------------------------------------------------------
def test_is_credential_field():
    assert is_credential_field(input_type="password") is True
    assert is_credential_field(autocomplete="current-password") is True
    assert is_credential_field(name="api_key") is True
    assert is_credential_field(aria_label="credit card number") is True
    assert is_credential_field(tag="input", input_type="text", name="search") is False


def test_looks_like_secret_and_redact():
    assert looks_like_secret("token: abc123") is True
    assert looks_like_secret("Bearer xyz") is True
    assert looks_like_secret("api_key=SUPERSECRET") is True
    assert looks_like_secret("just some text") is False
    red = redact_secret("authorization: Bearer supersecretvalue")
    assert "supersecretvalue" not in red
    assert "[REDACTED]" in red
    assert "ABC123XYZ" not in redact_secret("api_key=ABC123XYZ")


def test_page_element_value_never_exposes_credential():
    secret_field = PageElement(element_id="e1", input_type="password", value="hunter2")
    assert secret_field.safe_dict()["value"] == ""
    normal = PageElement(element_id="e2", input_type="text", value="hello")
    assert normal.safe_dict()["value"] == "hello"


# --------------------------------------------------------------------------
# Consequential action detection
# --------------------------------------------------------------------------
def test_consequential_signal_detects_high_impact():
    assert consequential_signal("Pay now") != ""
    assert consequential_signal("Delete account") != ""
    assert consequential_signal("Send message") != ""
    assert consequential_signal("Read more") == ""


def test_policy_module_exposes_expected_bounds():
    assert 443 in ALLOWED_PORTS and None in ALLOWED_PORTS
    assert policy.MAX_URL_LENGTH > 0
