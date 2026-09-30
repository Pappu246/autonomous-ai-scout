from pathlib import Path


WORKFLOW = Path(".github/workflows/gate3-coding-provider-smoke.yml")


def test_gate3_workflow_is_manual_and_read_only():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "workflow_dispatch:" in text
    assert "permissions:\n  contents: read" in text
    assert "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1" in text
    assert "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97" in text


def test_gate3_requires_real_credential_and_https_endpoint():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "SCOUT_CODING_PROVIDER_API_KEY" in text
    assert "https://*)" in text
    assert "Gate 3 live execution." in text


def test_gate3_binds_pipeline_to_exact_checkout_and_approval_boundary():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "--expected-head-sha \\\"" in text
    assert "autonomous-scout-code" in text
    assert "READY_FOR_APPROVAL" in text
    assert "never approves, executes, merges, or deploys" in text
    assert "github.sha" in text
