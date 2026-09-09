from __future__ import annotations

from .models import AccessStatus, ScoutReport


def render_markdown(report: ScoutReport) -> str:
    lines = ["# Autonomous AI Scout Report", "", f"Generated: {report.generated_at.isoformat()}", f"Meaningful change: {'YES' if report.meaningful_change else 'NO'}", ""]
    verified = [m for m in report.models if m.access_status == AccessStatus.VERIFIED_FREE]
    lines += ["## Verified free candidates", ""]
    if verified:
        for model in verified:
            bench = "benchmark: not run"
            if model.benchmark_ok is True:
                bench = f"benchmark: PASS, {model.benchmark_latency_ms} ms"
            elif model.benchmark_ok is False:
                bench = "benchmark: FAIL"
            changed = "; official source changed since last run" if model.source_changed else ""
            lines.append(f"- **{model.provider} / {model.model}** — {bench}{changed} — [official source]({model.source_url})")
    else:
        lines.append("- None verified in this run.")
    if report.project_findings:
        lines += ["", "## Project findings", ""]
        for finding in report.project_findings[:20]:
            lines.append(f"- **{finding.severity.upper()} — {finding.repository}: {finding.title}** — {finding.detail} Recommendation: {finding.recommendation}")
    if report.opportunities:
        lines += ["", "## Monetization / opportunity ideas", ""]
        for opportunity in sorted(report.opportunities, key=lambda x: x.score, reverse=True)[:10]:
            lines.append(f"- **{opportunity.title}** ({opportunity.score:.0f}/100) — {opportunity.description} Next: {opportunity.next_step}")
    if report.notes:
        lines += ["", "## Notes", ""] + [f"- {note}" for note in report.notes]
    return "\n".join(lines) + "\n"
