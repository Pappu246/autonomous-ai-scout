from datetime import datetime, timezone

from autonomous_agent.dashboard import render_dashboard
from autonomous_agent.models import ScoutReport


def test_dashboard_is_read_only_and_includes_snapshot():
    report = ScoutReport(generated_at=datetime.now(timezone.utc), meaningful_change=True)

    rendered = render_dashboard(report)

    assert "# Autonomous AI Scout Dashboard" in rendered
    assert "Verified-free models: **0**" in rendered
    assert "Paid billing" in rendered
    assert "does not execute actions" in rendered
