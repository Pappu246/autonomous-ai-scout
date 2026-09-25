"""Phase 1: goal -> capability routing, driven entirely by catalog data."""

from __future__ import annotations

import pytest

from autonomous_agent.digital.builtins import DEFAULT_CAPABILITIES, CapabilityDeclaration
from autonomous_agent.digital.catalog import CapabilityCatalog, signal_matches
from autonomous_agent.digital.contract import CapabilityError, RetryPolicy
from autonomous_agent.digital.domains import CapabilityDomain
from autonomous_agent.digital.intent import DigitalGoal, understand_goal

from .digital_support import RecordingExecutor, build_capability, build_catalog


@pytest.fixture()
def catalog() -> CapabilityCatalog:
    return build_catalog(
        (
            "filesystem:list",
            "filesystem:read",
            "filesystem:write",
            "os_shell:run",
            "web:search",
            "web:read",
            "email:search",
            "email:read",
            "email:thread",
            "email:send",
            "calendar:list",
            "calendar:event.cancel",
            "browser:open",
            "github:inspect",
            "testing:run",
        ),
        RecordingExecutor(),
    )


def test_natural_language_goal_routes_to_registered_capabilities(catalog):
    """The user states a goal; the system chooses the capabilities."""
    routing = catalog.route("list the files in the docs folder")
    assert routing.selected == ("filesystem:list",)
    assert routing.routed
    assert routing.matched_domains == (CapabilityDomain.FILESYSTEM,)


@pytest.mark.parametrize(
    "goal,expected",
    [
        ("read the file at docs/ci-validation.md", ("filesystem:read",)),
        ("run tests", ("testing:run",)),
        ("run tests and lint", ("testing:run",)),
        ("research the best free llm providers", ("web:search",)),
        ("inspect the repository and open a pr", ("github:inspect",)),
        ("what is on my calendar today", ("calendar:list",)),
        ("read email thread from alice", ("email:read", "email:thread")),
        ("run command py_compile on main.py", ("os_shell:run",)),
        ("browse the website", ("browser:open",)),
    ],
)
def test_routing_is_deterministic_and_narrow(catalog, goal, expected):
    assert catalog.route(goal).selected == expected
    assert catalog.route(goal).selected == catalog.route(goal).selected


def test_multi_capability_goal_selects_several_capabilities(catalog):
    routing = catalog.route("list the files in the docs folder and read the file notes.txt")
    assert routing.selected == ("filesystem:list", "filesystem:read")


def test_multi_capability_plan_orders_steps_by_declared_stage(catalog):
    from autonomous_agent.digital.planner import CapabilityPlanner

    plan = CapabilityPlanner(catalog).plan(
        "list the files in the docs folder and read the file notes.txt"
    )
    assert plan.executable
    assert [step.capability_id for step in plan.steps] == ["filesystem:list", "filesystem:read"]
    stages = [step.stage for step in plan.steps]
    assert stages == sorted(stages)


def test_unknown_goal_fails_closed_instead_of_defaulting_to_github(catalog):
    routing = catalog.route("please do something completely unspecified")
    assert routing.selected == ()
    assert not routing.routed
    assert "fails closed" in routing.reason
    # The historical default of routing anything unknown to github.inspect is gone.
    assert "github:inspect" not in routing.selected


def test_empty_goal_fails_closed(catalog):
    routing = catalog.route("   ")
    assert routing.selected == ()
    assert not routing.routed


def test_reserved_domain_makes_routing_fail_closed(catalog):
    routing = catalog.route("extract text from the pdf report")
    assert routing.reserved_required == (CapabilityDomain.DOCUMENTS,)
    assert not routing.routed
    assert "not registered" in routing.reason


def test_computer_control_goal_is_honest_about_being_unavailable(catalog):
    routing = catalog.route("open an application called the calculator")
    assert CapabilityDomain.COMPUTER in routing.reserved_required
    assert not routing.routed


def test_file_level_organization_stays_in_the_filesystem_domain(catalog):
    """Moving/renaming files is filesystem work, not document processing."""
    routing = catalog.route("organize today's downloaded PDFs")
    assert routing.reserved_required == ()
    assert CapabilityDomain.FILESYSTEM in routing.matched_domains
    assert routing.selected


@pytest.mark.parametrize(
    "goal",
    [
        "remove everything from my machine",
        "delete all files recursively",
        "wipe the disk clean",
        "purge the whole workspace",
        "rm -rf the project",
    ],
)
def test_destructive_goal_fails_closed(catalog, goal):
    routing = catalog.route(goal)
    assert routing.selected == ()
    assert not routing.routed
    assert "destructive" in routing.reason


def test_narrowly_scoped_deletion_still_routes_to_its_gated_capability(catalog):
    """A blocked phrase list must not disable legitimately gated operations."""
    routing = catalog.route("delete event from my calendar")
    assert routing.selected == ("calendar:event.cancel",)
    assert catalog.get("calendar:event.cancel").discover().approval == "human_review"


@pytest.mark.parametrize(
    "signal,text,expected",
    [
        ("repo", "extract text from the pdf report", False),
        ("repo", "inspect the repo", True),
        ("move", "remove everything", False),
        ("move", "move the file", True),
        ("file", "my profile page", False),
        ("file", "read the file", True),
        ("https://", "read https://example.com", True),
        ("pr", "open a pr", True),
        ("pr", "prepare the report", False),
    ],
)
def test_signal_matching_uses_word_boundaries(signal, text, expected):
    assert signal_matches(signal, text) is expected


def test_default_selection_is_read_only_and_narrow(catalog):
    """An ambiguous goal must never select a side effect by accident."""
    for domain, capability_ids in DEFAULT_CAPABILITIES.items():
        for capability_id in capability_ids:
            cap = catalog.get(capability_id)
            if cap is None:
                continue
            descriptor = cap.discover()
            assert descriptor.domain is domain
            assert descriptor.read_write == "read_only"
            assert descriptor.safe_autonomous


def test_write_capability_requires_an_explicit_signal(catalog):
    routing = catalog.route("look at the files in this folder")
    assert "filesystem:write" not in routing.selected
    assert routing.selected == ("filesystem:list",)


def test_write_capability_is_selected_when_explicitly_asked(catalog):
    assert catalog.route("write file notes.txt").selected == ("filesystem:write",)


def test_new_capability_routes_without_touching_the_planner():
    """Registering a capability with its own vocabulary is enough to route to it.

    Neither the planner nor the runtime is edited: the catalog is the only
    thing that changes.
    """
    from autonomous_agent.digital.planner import CapabilityPlanner

    declaration = CapabilityDeclaration(
        "testing:metrics",
        CapabilityDomain.TESTING,
        "metrics.collect",
        None,
        signals=("collect project metrics", "measure the workspace"),
        stage=20,
        retry_policy=RetryPolicy(1),
    )
    executor = RecordingExecutor()
    catalog = CapabilityCatalog(
        (
            build_capability("testing:metrics", declaration, executor),
            *build_catalog(("filesystem:list",), executor).capabilities(),
        )
    )
    routing = catalog.route("collect project metrics now")
    assert routing.selected == ("testing:metrics",)
    plan = CapabilityPlanner(catalog).plan("collect project metrics now")
    assert plan.executable
    assert plan.capability_ids == ("testing:metrics",)


def test_a_new_domain_is_purely_declarative():
    """Activating a reserved domain needs one descriptor entry, no core change.

    This exercises the mechanism with a real registered tool; it does not ship a
    computer-control capability, which stays reserved and unimplemented.
    """
    from autonomous_agent.digital.domains import DOMAIN_DESCRIPTORS, DomainDescriptor, DomainPhase
    from autonomous_agent.digital.planner import CapabilityPlanner

    domains = tuple(
        DomainDescriptor(
            item.domain,
            item.title,
            item.description,
            DomainPhase.ACTIVE if item.domain is CapabilityDomain.COMPUTER else item.phase,
            signals=(*item.signals, "activate the desktop icon")
            if item.domain is CapabilityDomain.COMPUTER
            else item.signals,
            notes=item.notes,
        )
        for item in DOMAIN_DESCRIPTORS
    )
    declaration = CapabilityDeclaration(
        "computer:activate",
        CapabilityDomain.COMPUTER,
        "metrics.collect",  # stands in for a future registered computer tool
        None,
        signals=("activate the desktop icon",),
        stage=40,
    )
    catalog = CapabilityCatalog(
        (build_capability("computer:activate", declaration, RecordingExecutor()),),
        domains=domains,
    )
    routing = catalog.route("activate the desktop icon to start")
    assert routing.reserved_required == ()
    assert routing.selected == ("computer:activate",)
    assert CapabilityPlanner(catalog).plan("activate the desktop icon to start").executable


def test_understand_goal_reports_reserved_requirements():
    catalog = build_catalog(("filesystem:list",), RecordingExecutor())
    profile = understand_goal("extract text from the pdf report", catalog)
    assert not profile.understood
    assert profile.requires_unregistered_capability
    assert profile.reserved_domains == (CapabilityDomain.DOCUMENTS,)


def test_understand_goal_never_exposes_credentials():
    """Credential material must be refused before intent understanding runs."""
    catalog = build_catalog(("filesystem:list",), RecordingExecutor())
    goal_text = "read the file with api_key=abc123 inside"
    with pytest.raises(CapabilityError):
        DigitalGoal(goal_text)
    with pytest.raises(CapabilityError):
        understand_goal(goal_text, catalog)


def test_goal_digest_is_stable_and_project_scoped():
    goal = DigitalGoal("list the files", project="scout")
    assert goal.digest == DigitalGoal("list   the files", project="scout").digest
    assert goal.digest != DigitalGoal("list the files", project="other").digest


def test_goal_rejects_empty_and_oversized_text():
    with pytest.raises(CapabilityError):
        DigitalGoal("   ")
    with pytest.raises(CapabilityError):
        DigitalGoal("x" * 5000)


def test_unknown_capability_id_is_not_invented(catalog):
    assert catalog.get("computer:click") is None
    assert catalog.by_tool("computer.click") is None
