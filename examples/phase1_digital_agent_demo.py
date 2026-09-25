"""Phase 1 walkthrough: the universal bounded digital agent.

Run with:

    python examples/phase1_digital_agent_demo.py

Everything here is real. The demo builds an agent from capabilities that are
already registered in the process tool registry, then shows:

1. capability discovery across peer domains,
2. goal -> capability routing from natural language,
3. honest reporting of reserved (unimplemented) domains,
4. approval gating for actions with side effects,
5. execution with real post-condition verification,
6. checkpoint/resume that does not replay verified work.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from autonomous_agent.capability_policy import Capability
from autonomous_agent.digital import build_agent
from autonomous_agent.digital.runtime import DigitalResultState
from autonomous_agent.filesystem_workspace import WorkspaceConnector


GOALS = (
    "list the files in the docs folder",
    "read the file notes.txt",
    "run tests and lint",
    "inspect the repository and open a pr",
    "organize today's downloaded PDFs",
    "extract text from the pdf report",
    "open an application called the calculator",
    "delete all files recursively",
    "please do something completely unspecified",
)


def section(title: str) -> None:
    print()
    print(title)
    print("=" * len(title))


def demo_discovery(agent) -> None:
    section("1. Capability domains are peers")
    for entry in agent.domain_status():
        flag = "active  " if entry["usable"] else "reserved"
        print(f"  [{flag}] {entry['domain']:<12} {entry['title']}")
    print()
    print(f"  registered capabilities: {len(agent.catalog.capabilities())}")
    print("  GitHub is one domain here, not the definition of the product.")


def demo_routing(agent) -> None:
    section("2. Natural-language goal -> registered capabilities")
    for goal in GOALS:
        routing = agent.catalog.route(goal)
        selected = ", ".join(routing.selected) or "none"
        reserved = ", ".join(item.value for item in routing.reserved_required)
        print(f"  goal: {goal}")
        print(f"    selected: {selected}")
        if reserved:
            print(f"    blocked on reserved domain(s): {reserved}")
        print(f"    reason:   {routing.reason}")
        print()


def demo_approval_gate(agent, root: Path, audit: Path) -> None:
    section("3. Side effects stop at the approval boundary")
    result = agent.run(
        "write file report.md",
        root=root,
        audit_path=audit,
        execution_id="demo-approval",
        granted=[Capability.FILES_WORKSPACE],
        requests={"filesystem:write": {"path": "report.md", "content": "# Report\n"}},
    )
    print(f"  state:  {result.state.value}")
    print(f"  reason: {result.reason}")
    print(f"  report.md created: {(root / 'report.md').exists()}  (must be False)")


def demo_verified_execution(agent, root: Path, audit: Path) -> None:
    section("4. Approved side effect executes and is verified by re-read")
    result = agent.run(
        "write file report.md",
        root=root,
        audit_path=audit,
        execution_id="demo-write",
        granted=[Capability.FILES_WORKSPACE],
        explicitly_approved=True,
        requests={"filesystem:write": {"path": "report.md", "content": "# Report\n"}},
    )
    print(f"  state:  {result.state.value}")
    for step in result.steps:
        print(f"    {step.step_id} {step.capability_id}: {step.observation}")
    print(f"  file on disk: {(root / 'report.md').read_text(encoding='utf-8')!r}")


def demo_resume(agent, root: Path, audit: Path) -> None:
    section("5. Resume skips work that was already verified")
    checkpoint = root / "checkpoint.json"
    requests = {
        "filesystem:list": {"path": "docs"},
        "filesystem:read": {"path": "docs/notes.txt"},
    }
    goal = "list the files in the docs folder and read the file notes.txt"

    first = agent.run(
        goal,
        root=root,
        audit_path=audit,
        execution_id="demo-resume",
        requests=requests,
        checkpoint_path=checkpoint,
    )
    verified = sum(1 for step in first.steps if step.verified)
    print(f"  first run:  {first.state.value} ({verified} of {len(first.steps)} steps verified)")
    print("    -> notes.txt does not exist yet, so the read step fails")

    (root / "docs" / "notes.txt").write_text("hello\n", encoding="utf-8")
    second = agent.run(
        goal,
        root=root,
        audit_path=audit,
        execution_id="demo-resume",
        requests=requests,
        checkpoint_path=checkpoint,
        resume=True,
    )
    print(f"  resume run: {second.state.value}")
    print(f"  steps not replayed: {second.resumed_steps or 'none'}")


def main() -> int:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        (root / "docs").mkdir()
        connector = WorkspaceConnector(root)
        agent = build_agent(root=root, connectors={"workspace": connector})
        audit = root / "digital_audit.jsonl"

        print("Phase 1 — universal bounded digital agent")
        demo_discovery(agent)
        demo_routing(agent)
        demo_approval_gate(agent, root, audit)
        demo_verified_execution(agent, root, audit)
        demo_resume(agent, root, audit)

        section("6. Reserved domains block instead of pretending")
        blocked = agent.run(
            "extract text from the pdf report",
            root=root,
            audit_path=audit,
            execution_id="demo-reserved",
        )
        print(f"  state:  {blocked.state.value}")
        print(f"  reason: {blocked.reason}")
        assert blocked.state is DigitalResultState.BLOCKED

        print()
        from autonomous_agent.execution_audit import verify_execution_audit

        print(f"audit chain valid: {verify_execution_audit(audit)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
