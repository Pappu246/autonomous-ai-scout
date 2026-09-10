from __future__ import annotations

from .models import AccessStatus, ScoutReport


def render_dashboard(report: ScoutReport) -> str:
    """Render a compact read-only dashboard from the current report."""
    verified = [m for m in report.models if m.access_status == AccessStatus.VERIFIED_FREE]
    high_findings = sum(1 for f in report.project_findings if f.severity == "high")
    medium_findings = sum(1 for f in report.project_findings if f.severity == "medium")
    opportunities = sorted(report.opportunities, key=lambda item: item.score, reverse=True)

    lines = [
        "# Autonomous AI Scout Dashboard",
        "",
        f"Generated: {report.generated_at.isoformat()}",
        f"Meaningful change: {'YES' if report.meaningful_change else 'NO'}",
        "",
        "## Snapshot",
        "",
        f"- Verified-free models: **{len(verified)}**",
        f"- High-severity findings: **{high_findings}**",
        f"- Medium-severity findings: **{medium_findings}**",
        f"- Opportunities: **{len(opportunities)}**",
        "",
        "## Top opportunities",
        "",
    ]
    if opportunities:
        for opportunity in opportunities[:5]:
            lines.append(f"- **{opportunity.title}** — {opportunity.score:.0f}/100 — {opportunity.next_step}")
    else:
        lines.append("- None recorded in this run.")

    lines += [
        "",
        "## Safety",
        "",
        "- Read-only presentation only; this dashboard does not execute actions.",
        "- Paid billing, access-control bypass, credential handling, and production deployment remain approval-gated.",
        "",
    ]
    return "\n".join(lines)
