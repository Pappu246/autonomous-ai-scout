"""Real capability wiring for the domains that exist in this phase.

Every entry here binds to a tool that is *already* registered in
:data:`autonomous_agent.tool_registry.REGISTRY` and to a sandbox operation that
already exists. No capability in this file is a placeholder: reserved domains
(computer control, application adapters, document processing) are declared in
:mod:`autonomous_agent.digital.domains` and deliberately have **no** entry
here, so the catalog reports them as unregistered and the planner fails closed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..filesystem_workspace import WorkspaceConnector
from ..tool_registry import REGISTRY, ToolRegistry
from ..computer.backend import UnsupportedPlatformBackend
from ..computer.policy import is_windows
from ..computer.observer import ComputerPostConditionObserver
from .contract import (
    CapabilityAvailability,
    CapabilityExecution,
    CapabilityObservation,
    CapabilityRequest,
    RetryPolicy,
)
from .domains import CapabilityDomain
from .provider import RegisteredToolCapability, SandboxCapabilityExecutor


@dataclass(frozen=True)
class CapabilityDeclaration:
    """Declarative description of one capability to build from a real tool."""

    capability_id: str
    domain: CapabilityDomain
    tool_name: str
    connector_slot: str | None
    signals: tuple[str, ...] = ()
    stage: int = 50
    retry_policy: RetryPolicy = RetryPolicy()
    credential_handling: str = "none"
    notes: str = ""


# stage: read/discover = 10, transform = 30, side effect = 60, notify = 80.
# The planner orders multi-capability plans by stage so work reads naturally:
# look first, transform second, mutate last.
BUILTIN_DECLARATIONS: tuple[CapabilityDeclaration, ...] = (
    # -- filesystem -------------------------------------------------------
    CapabilityDeclaration(
        "filesystem:list",
        CapabilityDomain.FILESYSTEM,
        "filesystem.list",
        "workspace",
        signals=("list files", "list the files", "list directory", "list folder", "the files in", "files in the", "what files", "which files", "enumerate", "show files", "show me the files", "find files", "directory listing", "folder contents", "directory contents", "see what is in"),
        stage=10,
        retry_policy=RetryPolicy(2, 1),
    ),
    CapabilityDeclaration(
        "filesystem:read",
        CapabilityDomain.FILESYSTEM,
        "filesystem.read",
        "workspace",
        signals=("read file", "read the file", "open file", "open the file", "contents of", "show me the file", "file content", "what does the file say"),
        stage=20,
        retry_policy=RetryPolicy(2, 1),
    ),
    CapabilityDeclaration(
        "filesystem:write",
        CapabilityDomain.FILESYSTEM,
        "filesystem.write",
        "workspace",
        signals=("write file", "create file", "save file", "new file", "write a file"),
        stage=60,
    ),
    CapabilityDeclaration(
        "filesystem:transform",
        CapabilityDomain.FILESYSTEM,
        "filesystem.transform",
        "workspace",
        signals=("transform file", "replace in file", "modify file", "edit file", "update file contents"),
        stage=60,
    ),
    # -- workspace shell (read-only by construction) ----------------------
    CapabilityDeclaration(
        "os_shell:run",
        CapabilityDomain.OS_SHELL,
        "workspace.shell",
        "workspace",
        signals=("run command", "shell command", "terminal command", "py_compile", "compile python"),
        stage=20,
        notes="Allowlisted, root-bound, read-only. Not general shell access.",
    ),
    # -- public web -------------------------------------------------------
    CapabilityDeclaration(
        "web:search",
        CapabilityDomain.WEB,
        "web.search",
        "web",
        signals=("search", "look up", "find information", "research", "investigate", "what is the best"),
        stage=10,
        retry_policy=RetryPolicy(2, 1),
    ),
    CapabilityDeclaration(
        "web:read",
        CapabilityDomain.WEB,
        "web.read",
        "web",
        signals=("http://", "https://", "read this page", "open this page", "read the page", "url"),
        stage=20,
        retry_policy=RetryPolicy(2, 1),
    ),
    CapabilityDeclaration(
        "web:extract",
        CapabilityDomain.WEB,
        "web.extract",
        "web",
        signals=("extract", "pull out", "fields from"),
        stage=30,
    ),
    CapabilityDeclaration(
        "web:compare",
        CapabilityDomain.WEB,
        "web.compare",
        "web",
        signals=("compare sources", "compare these", "cross-check"),
        stage=40,
    ),
    # -- email ------------------------------------------------------------
    CapabilityDeclaration(
        "email:search",
        CapabilityDomain.EMAIL,
        "email.search",
        "gmail",
        signals=("search email", "find email", "emails about", "unread", "inbox"),
        stage=10,
        retry_policy=RetryPolicy(2, 1),
        credential_handling="reference_only",
    ),
    CapabilityDeclaration(
        "email:read",
        CapabilityDomain.EMAIL,
        "email.read",
        "gmail",
        signals=("read email", "read message", "open email", "read the mail"),
        stage=20,
        retry_policy=RetryPolicy(2, 1),
        credential_handling="reference_only",
    ),
    CapabilityDeclaration(
        "email:thread",
        CapabilityDomain.EMAIL,
        "email.thread",
        "gmail",
        signals=("thread", "conversation", "message thread"),
        stage=20,
        credential_handling="reference_only",
    ),
    CapabilityDeclaration(
        "email:draft",
        CapabilityDomain.EMAIL,
        "email.draft",
        "gmail",
        signals=("draft email", "draft an email", "compose email", "compose an email", "write an email"),
        stage=60,
        credential_handling="reference_only",
    ),
    CapabilityDeclaration(
        "email:send",
        CapabilityDomain.EMAIL,
        "email.send",
        "gmail",
        signals=("send email", "send a mail", "reply email", "send the email", "send it to"),
        stage=80,
        credential_handling="reference_only",
    ),
    # -- calendar ---------------------------------------------------------
    CapabilityDeclaration(
        "calendar:list",
        CapabilityDomain.CALENDAR,
        "calendar.list",
        "calendar",
        signals=("list events", "what is on my calendar", "upcoming events", "today's events", "agenda"),
        stage=10,
        retry_policy=RetryPolicy(2, 1),
        credential_handling="reference_only",
    ),
    CapabilityDeclaration(
        "calendar:read",
        CapabilityDomain.CALENDAR,
        "calendar.read",
        "calendar",
        signals=("read event", "event details", "get event"),
        stage=20,
        credential_handling="reference_only",
    ),
    CapabilityDeclaration(
        "calendar:find_free_time",
        CapabilityDomain.CALENDAR,
        "calendar.find_free_time",
        "calendar",
        signals=("free time", "available time", "availability", "when am i free"),
        stage=20,
        credential_handling="reference_only",
    ),
    CapabilityDeclaration(
        "calendar:event.create",
        CapabilityDomain.CALENDAR,
        "calendar.event.create",
        "calendar",
        signals=("create event", "book", "add event", "schedule a meeting", "schedule meeting", "set up a meeting"),
        stage=60,
        credential_handling="reference_only",
    ),
    CapabilityDeclaration(
        "calendar:event.update",
        CapabilityDomain.CALENDAR,
        "calendar.event.update",
        "calendar",
        signals=("update event", "reschedule", "move meeting", "modify event"),
        stage=60,
        credential_handling="reference_only",
    ),
    CapabilityDeclaration(
        "calendar:event.cancel",
        CapabilityDomain.CALENDAR,
        "calendar.event.cancel",
        "calendar",
        signals=("cancel event", "cancel meeting", "delete event"),
        stage=60,
        credential_handling="reference_only",
    ),
    # -- browser ----------------------------------------------------------
    CapabilityDeclaration(
        "browser:open",
        CapabilityDomain.BROWSER,
        "browser.open",
        "browser",
        signals=("open the page", "open the site", "open the url", "open the website", "open the link", "visit", "navigate", "load page", "browse to"),
        stage=10,
        retry_policy=RetryPolicy(2, 1),
    ),
    CapabilityDeclaration(
        "browser:click",
        CapabilityDomain.BROWSER,
        "browser.click",
        "browser",
        signals=("click", "press", "select", "submit"),
        stage=40,
    ),
    CapabilityDeclaration(
        "browser:extract",
        CapabilityDomain.BROWSER,
        "browser.extract",
        "browser",
        signals=("extract", "read page", "collect text", "scrape"),
        stage=30,
    ),
    # -- advanced bounded browser agent (Phase 3) -------------------------
    CapabilityDeclaration(
        "browser:session.open",
        CapabilityDomain.BROWSER,
        "browser.session.open",
        "browser",
        signals=("open a browser session", "start a browser session", "browser session", "browsing session"),
        stage=5,
        notes="Opens a stateful bounded session with an explicit host allowlist.",
    ),
    CapabilityDeclaration(
        "browser:navigate",
        CapabilityDomain.BROWSER,
        "browser.navigate",
        "browser",
        signals=("navigate to", "go to the page", "open the url", "load the page", "visit the site"),
        stage=10,
        retry_policy=RetryPolicy(2, 1),
    ),
    CapabilityDeclaration(
        "browser:back",
        CapabilityDomain.BROWSER,
        "browser.back",
        "browser",
        signals=("go back", "navigate back", "previous page", "back button"),
        stage=10,
    ),
    CapabilityDeclaration(
        "browser:forward",
        CapabilityDomain.BROWSER,
        "browser.forward",
        "browser",
        signals=("go forward", "navigate forward", "next page", "forward button"),
        stage=10,
    ),
    CapabilityDeclaration(
        "browser:reload",
        CapabilityDomain.BROWSER,
        "browser.reload",
        "browser",
        signals=("reload the page", "refresh the page", "reload page", "refresh page"),
        stage=10,
    ),
    CapabilityDeclaration(
        "browser:page.observe",
        CapabilityDomain.BROWSER,
        "browser.page.observe",
        "browser",
        signals=("observe the page", "read the page", "what is on the page", "page contents", "inspect the page"),
        stage=15,
        retry_policy=RetryPolicy(2, 1),
    ),
    CapabilityDeclaration(
        "browser:element.find",
        CapabilityDomain.BROWSER,
        "browser.element.find",
        "browser",
        signals=("find the element", "find the button", "find the link", "locate the field", "find element"),
        stage=20,
    ),
    CapabilityDeclaration(
        "browser:element.click",
        CapabilityDomain.BROWSER,
        "browser.element.click",
        "browser",
        signals=("click the button", "click the link", "press the button", "click element", "tap the"),
        stage=40,
    ),
    CapabilityDeclaration(
        "browser:element.type",
        CapabilityDomain.BROWSER,
        "browser.element.type",
        "browser",
        signals=("type into", "enter text into", "fill in the field", "type in the", "enter into the field"),
        stage=50,
    ),
    CapabilityDeclaration(
        "browser:element.select",
        CapabilityDomain.BROWSER,
        "browser.element.select",
        "browser",
        signals=("select the option", "choose from the dropdown", "select from", "pick the option"),
        stage=45,
    ),
    CapabilityDeclaration(
        "browser:download.start",
        CapabilityDomain.BROWSER,
        "browser.download.start",
        "browser",
        signals=("download the file", "download this", "save the file", "download the pdf", "download file"),
        stage=60,
    ),
    CapabilityDeclaration(
        "browser:file.extract",
        CapabilityDomain.BROWSER,
        "browser.file.extract",
        "browser",
        signals=("extract from the downloaded", "read the downloaded file", "checksum the file", "extract text from the file"),
        stage=30,
        notes="Bounded read of a workspace file previously downloaded; no document parsing.",
    ),
    # -- computer control -------------------------------------------------
    CapabilityDeclaration(
        "computer:screen.capture",
        CapabilityDomain.COMPUTER,
        "computer.screen.capture",
        "computer",
        signals=("capture screen", "screenshot", "screen capture", "take screenshot", "capture the screen", "grab screen", "display capture", "screen image"),
        stage=10,
        retry_policy=RetryPolicy(2, 1),
    ),
    CapabilityDeclaration(
        "computer:window.list",
        CapabilityDomain.COMPUTER,
        "computer.window.list",
        "computer",
        signals=("list windows", "list window", "enumerate windows", "open windows", "all windows", "show windows", "find windows", "get windows", "running windows"),
        stage=10,
        retry_policy=RetryPolicy(2, 1),
    ),
    CapabilityDeclaration(
        "computer:window.active",
        CapabilityDomain.COMPUTER,
        "computer.window.active",
        "computer",
        signals=("active window", "current window", "foreground window", "focused window", "get active window", "which window is active"),
        stage=10,
        retry_policy=RetryPolicy(2, 1),
    ),
    CapabilityDeclaration(
        "computer:window.focus",
        CapabilityDomain.COMPUTER,
        "computer.window.focus",
        "computer",
        signals=("focus window", "switch to window", "bring window to front", "activate window", "switch window", "focus application"),
        stage=30,
        retry_policy=RetryPolicy(2, 1),
    ),
    CapabilityDeclaration(
        "computer:app.launch",
        CapabilityDomain.COMPUTER,
        "computer.app.launch",
        "computer",
        signals=("launch app", "launch application", "open app", "open application", "open an application", "open the application", "open the app", "open an app", "start application", "start app", "run application"),
        stage=60,
    ),
    CapabilityDeclaration(
        "computer:mouse.move",
        CapabilityDomain.COMPUTER,
        "computer.mouse.move",
        "computer",
        signals=("move mouse", "pointer move", "mouse position", "move cursor", "hover"),
        stage=20,
    ),
    CapabilityDeclaration(
        "computer:mouse.click",
        CapabilityDomain.COMPUTER,
        "computer.mouse.click",
        "computer",
        signals=("mouse click", "click mouse", "click at", "double click", "right click", "click button"),
        stage=40,
    ),
    CapabilityDeclaration(
        "computer:keyboard.type",
        CapabilityDomain.COMPUTER,
        "computer.keyboard.type",
        "computer",
        signals=("type text", "keyboard type", "enter text", "type input", "write text in window"),
        stage=50,
    ),
    CapabilityDeclaration(
        "computer:keyboard.hotkey",
        CapabilityDomain.COMPUTER,
        "computer.keyboard.hotkey",
        "computer",
        signals=("keyboard shortcut", "hotkey", "press keys", "shortcut combination", "key combination"),
        stage=40,
    ),
    CapabilityDeclaration(
        "computer:clipboard.read",
        CapabilityDomain.COMPUTER,
        "computer.clipboard.read",
        "computer",
        signals=("read clipboard", "get clipboard", "paste from clipboard", "clipboard content"),
        stage=10,
        retry_policy=RetryPolicy(2, 1),
    ),
    CapabilityDeclaration(
        "computer:clipboard.write",
        CapabilityDomain.COMPUTER,
        "computer.clipboard.write",
        "computer",
        signals=("write clipboard", "set clipboard", "copy to clipboard", "update clipboard"),
        stage=50,
    ),
    # -- GitHub (one domain among many) -----------------------------------
    CapabilityDeclaration(
        "github:inspect",
        CapabilityDomain.GITHUB,
        "github.inspect",
        None,
        signals=("inspect repository", "inspect repo", "repository state", "repo state", "audit repository", "review repository", "pull request", "issues", "branch", "commit", "ci status"),
        stage=10,
        retry_policy=RetryPolicy(2, 1),
        credential_handling="reference_only",
    ),
    # -- testing / validation --------------------------------------------
    CapabilityDeclaration(
        "testing:run",
        CapabilityDomain.TESTING,
        "tests.run",
        None,
        signals=("run tests", "test suite", "pytest", "unittest", "tests pass"),
        stage=40,
        retry_policy=RetryPolicy(2, 1),
    ),
    CapabilityDeclaration(
        "testing:lint",
        CapabilityDomain.TESTING,
        "lint.run",
        None,
        signals=("lint", "static check", "syntax check", "typecheck", "type check"),
        stage=30,
        retry_policy=RetryPolicy(2, 1),
    ),
    CapabilityDeclaration(
        "testing:metrics",
        CapabilityDomain.TESTING,
        "metrics.collect",
        None,
        signals=("metrics", "measure", "collect stats"),
        stage=20,
    ),
)


#: Domains whose fallback selection is used when only the domain matched.
#: Restricted to read-only capabilities so an ambiguous goal can never select a
#: side effect by accident.
#: Exactly one capability per domain: the narrowest read-only entry point.
#: An ambiguous goal therefore never selects more than it can justify, and can
#: never select a side effect by accident.
DEFAULT_CAPABILITIES: Mapping[CapabilityDomain, tuple[str, ...]] = {
    CapabilityDomain.FILESYSTEM: ("filesystem:list",),
    CapabilityDomain.OS_SHELL: ("os_shell:run",),
    CapabilityDomain.WEB: ("web:search",),
    CapabilityDomain.EMAIL: ("email:search",),
    CapabilityDomain.CALENDAR: ("calendar:list",),
    CapabilityDomain.BROWSER: ("browser:open",),
    CapabilityDomain.COMPUTER: ("computer:window.active",),
    CapabilityDomain.GITHUB: ("github:inspect",),
    CapabilityDomain.TESTING: ("testing:run",),
}


class FilesystemPostConditionObserver:
    """Confirm a workspace write actually happened by re-reading it.

    Reuses the existing root-bound :class:`WorkspaceConnector`, so verification
    is a real observation of the workspace rather than a trust in the call
    having returned success.
    """

    def __init__(self, connector: WorkspaceConnector) -> None:
        self._connector = connector

    def observe(
        self, request: CapabilityRequest, execution: CapabilityExecution
    ) -> CapabilityObservation:
        if request.capability_id not in {"filesystem:write", "filesystem:transform"}:
            if execution.has_evidence:
                return CapabilityObservation(
                    request.capability_id, True, execution.evidence, "workspace read evidence"
                )
            return CapabilityObservation(
                request.capability_id, False, detail="workspace read produced no evidence"
            )
        target = str(request.arguments.get("path", "")).strip()
        if not target:
            return CapabilityObservation(
                request.capability_id, False, detail="write request had no target path"
            )
        try:
            current = self._connector.read(target).safe_dict()
        except Exception as exc:  # connector raises WorkspaceError subclasses
            return CapabilityObservation(
                request.capability_id,
                False,
                detail=f"post-write read failed: {type(exc).__name__}",
            )
        written = str(execution.evidence.get("content", ""))
        if written and str(current.get("content", "")) != written:
            return CapabilityObservation(
                request.capability_id,
                False,
                evidence=current,
                detail="workspace content does not match the written payload",
            )
        return CapabilityObservation(
            request.capability_id,
            True,
            {
                "relative_path": current.get("relative_path"),
                "fingerprint": current.get("fingerprint"),
            },
            "workspace post-condition confirmed by independent re-read",
        )


from ..browser.observer import BrowserPostConditionObserver  # noqa: E402,F401


def build_capabilities(
    *,
    root: Path | str,
    connectors: Mapping[str, Any] | None = None,
    tool_registry: ToolRegistry = REGISTRY,
    declarations: Iterable[CapabilityDeclaration] = BUILTIN_DECLARATIONS,
    timeout_seconds: int = 30,
) -> tuple[RegisteredToolCapability, ...]:
    """Build the real capability set for one workspace root.

    A capability whose tool is not present in the target registry is skipped
    rather than faked, so the catalog can never advertise something that cannot
    be executed.
    """
    executor = SandboxCapabilityExecutor(
        root, connectors=connectors, timeout_seconds=timeout_seconds
    )
    workspace_connector = (connectors or {}).get("workspace")
    computer_connector = (connectors or {}).get("computer")
    browser_connector = (connectors or {}).get("browser")
    built: list[RegisteredToolCapability] = []
    for declaration in declarations:
        if tool_registry.get(declaration.tool_name) is None:
            continue
        observer = None
        availability = CapabilityAvailability.AVAILABLE
        if declaration.domain is CapabilityDomain.FILESYSTEM and workspace_connector is not None:
            observer = FilesystemPostConditionObserver(workspace_connector)
        elif declaration.domain is CapabilityDomain.BROWSER:
            if browser_connector is not None:
                observer = BrowserPostConditionObserver(browser_connector)
        elif declaration.domain is CapabilityDomain.COMPUTER:
            if computer_connector is not None:
                observer = ComputerPostConditionObserver(computer_connector)
            is_mock = (
                computer_connector is not None
                and hasattr(computer_connector, "backend")
                and not isinstance(getattr(computer_connector, "backend", None), UnsupportedPlatformBackend)
            )
            if not is_windows() and not is_mock:
                availability = CapabilityAvailability.DISABLED

        built.append(
            RegisteredToolCapability.from_tool(
                declaration.tool_name,
                domain=declaration.domain,
                capability_id=declaration.capability_id,
                signals=declaration.signals,
                stage=declaration.stage,
                retry_policy=declaration.retry_policy,
                availability=availability,
                credential_handling=declaration.credential_handling,
                executor=executor,
                tool_registry=tool_registry,
                observer=observer,
                notes=declaration.notes,
            )
        )
    return tuple(built)


def declared_capability_ids() -> tuple[str, ...]:
    return tuple(item.capability_id for item in BUILTIN_DECLARATIONS)


def availability_report(tool_registry: ToolRegistry = REGISTRY) -> tuple[dict[str, object], ...]:
    """Report which declared capabilities are actually wired in this registry."""
    return tuple(
        {
            "capability_id": item.capability_id,
            "domain": item.domain.value,
            "tool_name": item.tool_name,
            "availability": (
                CapabilityAvailability.AVAILABLE.value
                if tool_registry.get(item.tool_name) is not None
                else CapabilityAvailability.UNREGISTERED.value
            ),
        }
        for item in BUILTIN_DECLARATIONS
    )


__all__ = [
    "BUILTIN_DECLARATIONS",
    "DEFAULT_CAPABILITIES",
    "CapabilityDeclaration",
    "FilesystemPostConditionObserver",
    "availability_report",
    "build_capabilities",
    "declared_capability_ids",
]
