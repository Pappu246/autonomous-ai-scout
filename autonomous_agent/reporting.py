from __future__ import annotations

from .models import AccessStatus, ScoutReport


def render_markdown(report: ScoutReport) -> str:
    lines = ["# Autonomous AI Scout Report", "", f"Generated: {report.generated_at.isoformat()}", ""]

    verified = [m for m in report.models if m.access_status == AccessStatus.VERIFIED_FREE]
    lines += ["## Verified free candidates", ""]
    if verified:
        for model in verified:
            lines.append(f"- **{model.provider} / {model.model}** — {model.evidence} ([official source]({model.source_url}))")
    else:
        lines.append("- None verified in this run.")

    if report.project_findings:
        lines += ["", "## Project findings", ""]
        for finding in report.project_findings[:10]:
            lines.append(f"- **{finding.severity}: {finding.title}** — {finding.detail} Recommendation: {finding.recommendation}")

    if report.opportunities:
        lines += ["", "## Opportunities", ""]
        for opportunity in sorted(report.opportunities, key=lambda x: x.score, reverse=True)[:10]:
            lines.append(f"- **{opportunity.title}** ({opportunity.score:.0f}/100) — {opportunity.description} Next: {opportunity.next_step}")

    if report.notes:
        lines += ["", "## Notes", ""] + [f"- {note}" for note in report.notes]

    return "\n".join(lines) + "\n"
