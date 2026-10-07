from pathlib import Path


WORKFLOWS = {
    'gate4': Path('.github/workflows/gate4-external-connector-smoke.yml'),
    'gate5': Path('.github/workflows/gate5-github-admin-readiness.yml'),
    'gate6': Path('.github/workflows/gate6-formal-release.yml'),
}


def test_readiness_workflows_pin_actions():
    for path in WORKFLOWS.values():
        text = path.read_text(encoding='utf-8')
        assert 'actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1' in text


def test_gate4_requires_real_read_only_accounts():
    text = WORKFLOWS['gate4'].read_text(encoding='utf-8')
    assert 'workflow_dispatch:' in text
    assert 'SCOUT_GMAIL_ACCESS_TOKEN' in text
    assert 'SCOUT_CALENDAR_ACCESS_TOKEN' in text
    assert 'live_gate4_smoke.py' in text


def test_gate5_requires_admin_credential_and_explicit_apply():
    text = WORKFLOWS['gate5'].read_text(encoding='utf-8')
    assert 'SCOUT_GITHUB_ADMIN_TOKEN' in text
    assert 'apply_protection' in text
    assert 'required_approving_review_count' in text
    assert 'required_conversation_resolution' in text


def test_gate6_is_explicit_and_green_sha_gated():
    text = WORKFLOWS['gate6'].read_text(encoding='utf-8')
    assert 'workflow_dispatch:' in text
    assert "github.ref == 'refs/heads/main'" in text
    assert 'inputs.confirm' in text
    assert 'No CI test check run exists for release SHA.' in text
    assert "x.get('name')=='test'" in text

    assert 'gh release create' in text