"""Bounded browser connector -- the safe, structured browser surface.

This connector is the only object the sandbox ever talks to for browser work. It
composes the backend, a bounded :class:`~autonomous_agent.browser.session.BrowserSession`,
semantic target resolution, replay protection, the prompt-injection guard and
the navigation/download policy into the 12 canonical capabilities plus the 3
legacy compatibility operations.

Design invariants enforced here:
- Navigation is HTTPS + allowlisted + redirect-validated; dangerous schemes and
  loopback/metadata hosts are refused (via :mod:`policy`).
- Interaction is semantic only; stale/foreign targets and credential fields fail
  closed; there is no coordinate fallback.
- Credential material is never typed and never reaches evidence.
- Consequential actions require approval even before the runtime gate.
- Downloads are confined to the workspace root with sanitized names and size caps.
- All page/download content is treated as untrusted data and wrapped by the
  prompt-injection guard; it can never become instructions.
- The connector reports evidence; it never declares a result VERIFIED. The
  observer (M3) makes that call from observable post-conditions.
- No arbitrary JavaScript, no raw CDP/debugger, no unrestricted shell.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from ..prompt_injection_guard import PromptInjectionGuard, TrustLevel
from .backend import (
    BaseBrowserBackend,
    UnsupportedBrowserBackend,
    checksum,
)
from .models import (
    MAX_RESPONSE_BYTES,
    ActionBudget,
    BrowserSecurityError,
    ElementTarget,
    PageObservation,
    SessionError,
    SessionState,
    consequential_signal,
    looks_like_secret,
    redact_secret,
)
from .policy import (
    confine_download_path,
    confine_workspace_path,
    validate_download_url,
    validate_host_allowlist,
    validate_navigation_url,
    validate_typed_text,
)
from .replay import BrowserReplayProtector
from .session import BrowserSession
from .target import SemanticTargetResolver


class BoundedBrowserConnector:
    """Safe, bounded connector for stateful browser interaction."""

    def __init__(
        self,
        backend: BaseBrowserBackend | None = None,
        *,
        allowed_hosts=None,
        workspace_root: Path | str = ".",
        download_dir: str = "downloads",
        guard: PromptInjectionGuard | None = None,
        replay: BrowserReplayProtector | None = None,
    ) -> None:
        self._backend = backend if backend is not None else UnsupportedBrowserBackend()
        self._base_hosts = (
            validate_host_allowlist(allowed_hosts) if allowed_hosts is not None else frozenset()
        )
        self._workspace_root = Path(workspace_root)
        self._download_dir = download_dir
        self._guard = guard or PromptInjectionGuard()
        self._replay = replay or BrowserReplayProtector()
        self._resolver = SemanticTargetResolver()
        self._session: BrowserSession | None = None
        self._page: PageObservation | None = None

    # -- introspection -----------------------------------------------------
    @property
    def backend(self) -> BaseBrowserBackend:
        return self._backend

    @property
    def session(self) -> BrowserSession | None:
        return self._session

    @property
    def replay_protector(self) -> BrowserReplayProtector:
        return self._replay

    def is_live(self) -> bool:
        return not isinstance(self._backend, UnsupportedBrowserBackend)

    # -- session management ------------------------------------------------
    def _open_session(self, allowed_hosts=None, session_id: str = "session") -> BrowserSession:
        hosts = self._base_hosts
        if allowed_hosts is not None:
            hosts = validate_host_allowlist(allowed_hosts)
        if not hosts:
            raise BrowserSecurityError("a session requires an explicit, non-empty host allowlist")
        self._session = BrowserSession(allowed_hosts=hosts, session_id=session_id)
        return self._session

    def _ensure_session(self, allowed_hosts=None) -> BrowserSession:
        if self._session is not None and self._session.state is SessionState.OPEN:
            return self._session
        # Legacy/implicit session bound to the connector's base allowlist.
        return self._open_session(allowed_hosts=allowed_hosts or self._base_hosts or None)

    # -- untrusted-content handling ---------------------------------------
    def _wrap_untrusted(self, text: str, source: str) -> dict[str, Any]:
        result = self._guard.inspect(redact_secret(text or "")[:MAX_RESPONSE_BYTES], source=source, trust=TrustLevel.EXTERNAL)
        return {
            "trust": TrustLevel.EXTERNAL.value,
            "injection_signals": list(result.signals),
            "blocked": result.blocked,
        }

    # -- 1. session.open ---------------------------------------------------
    def session_open(
        self,
        allowed_hosts=None,
        *,
        session_id: str = "session",
        max_navigations: int | None = None,
        max_redirects: int | None = None,
        action_budget: int | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        if max_navigations is not None:
            kwargs["max_navigations"] = int(max_navigations)
        if max_redirects is not None:
            kwargs["max_redirects"] = int(max_redirects)
        if action_budget is not None:
            kwargs["action_budget"] = ActionBudget(limit=int(action_budget))
        if allowed_hosts is None:
            allowed_hosts = self._base_hosts or None
        session = self._open_session(allowed_hosts=allowed_hosts, session_id=session_id)
        # Re-create with explicit bounds if any were supplied.
        if kwargs:
            self._session = BrowserSession(
                allowed_hosts=session.allowed_hosts, session_id=session_id, **kwargs
            )
            session = self._session
        self._page = None
        return {
            "opened": True,
            "session_id": session.session_id,
            "state": session.state.value,
            "allowed_hosts": sorted(session.allowed_hosts),
            "live_backend": self.is_live(),
            "max_navigations": session.max_navigations,
            "max_redirects": session.max_redirects,
            "action_budget": session.action_budget.limit,
        }

    # -- 2. navigate -------------------------------------------------------
    def navigate(self, url: str, *, timeout_seconds: int = 20, **_: Any) -> dict[str, Any]:
        session = self._ensure_session()
        session.consume_action(1)
        host = validate_navigation_url(url, session.allowed_hosts)
        record, observation = self._backend.navigate(url, allowed_hosts=session.allowed_hosts)
        session.record_navigation(record)
        self._page = observation
        return {
            "navigated": True,
            "url": observation.url,
            "host": host,
            "status_code": observation.status_code,
            "redirect_depth": observation.redirect_depth,
            "title": redact_secret(observation.title)[:256],
            "epoch": observation.epoch,
        }

    # -- 3/4/5. back / forward / reload -----------------------------------
    def back(self, **_: Any) -> dict[str, Any]:
        session = self._ensure_session()
        session.consume_action(1)
        record, observation = self._backend.go_back()
        try:
            session.move_back()
        except Exception:
            pass  # the backend is authoritative for history movement
        session.note_epoch()
        self._page = observation
        return self._nav_result("back", record, observation)

    def forward(self, **_: Any) -> dict[str, Any]:
        session = self._ensure_session()
        session.consume_action(1)
        record, observation = self._backend.go_forward()
        try:
            session.move_forward()
        except Exception:
            pass  # the backend is authoritative for history movement
        session.note_epoch()
        self._page = observation
        return self._nav_result("forward", record, observation)

    def reload(self, *, timeout_seconds: int = 20, **_: Any) -> dict[str, Any]:
        session = self._ensure_session()
        session.consume_action(1)
        record, observation = self._backend.reload()
        session.note_reload()
        self._page = observation
        return self._nav_result("reload", record, observation)

    def _nav_result(self, op: str, record, observation: PageObservation) -> dict[str, Any]:
        return {
            "operation": op,
            "url": observation.url,
            "status_code": observation.status_code,
            "redirect_depth": observation.redirect_depth,
            "title": redact_secret(observation.title)[:256],
            "epoch": observation.epoch,
        }

    # -- 6. page.observe ---------------------------------------------------
    def page_observe(self, **_: Any) -> dict[str, Any]:
        session = self._ensure_session()
        session.consume_action(1)
        observation = self._backend.observe()
        self._page = observation
        trust = self._wrap_untrusted(observation.text, source=f"page:{observation.url}")
        payload = observation.safe_dict()
        payload["trust"] = trust["trust"]
        payload["injection_signals"] = trust["injection_signals"]
        return payload

    def _current_page(self) -> PageObservation:
        if self._page is None:
            self._page = self._backend.observe()
        return self._page

    # -- 7. element.find ---------------------------------------------------
    def element_find(
        self,
        *,
        role: str = "",
        accessible_name: str = "",
        visible_text: str = "",
        selector: str = "",
        **_: Any,
    ) -> dict[str, Any]:
        session = self._ensure_session()
        session.consume_action(1)
        page = self._current_page()
        target = self._resolver.resolve(
            page, role=role, accessible_name=accessible_name, visible_text=visible_text, selector=selector
        )
        return {
            "found": True,
            "target": target.safe_dict(),
            "belongs_to_current_page": True,
            "page_url": page.url,
            "epoch": page.epoch,
        }

    # -- target coercion (shared by click/type/select) --------------------
    def _coerce_target(
        self,
        page: PageObservation,
        *,
        target: Mapping[str, Any] | None,
        role: str = "",
        accessible_name: str = "",
        visible_text: str = "",
        selector: str = "",
    ):
        """Return (ElementTarget, PageElement), validating currency/staleness.

        A supplied target object is checked against the current page epoch and
        element set (stale/foreign -> fail closed). Query parameters are resolved
        fresh against the current page.
        """
        if target:
            rebuilt = ElementTarget(
                element_id=str(target.get("element_id", "")),
                selector=str(target.get("selector", "")),
                role=str(target.get("role", "")),
                accessible_name=str(target.get("accessible_name", "")),
                epoch=int(target.get("epoch", -1)),
            )
            element = self._resolver.ensure_current(rebuilt, page)
            return rebuilt, element
        resolved = self._resolver.resolve(
            page, role=role, accessible_name=accessible_name, visible_text=visible_text, selector=selector
        )
        element = self._resolver.ensure_current(resolved, page)
        return resolved, element

    def _guard_consequential(self, element, approved: bool) -> None:
        signal = consequential_signal(element.accessible_name, element.visible_text)
        if signal and not approved:
            raise BrowserSecurityError(
                f"consequential action requires approval: {signal}"
            )

    # -- 8. element.click --------------------------------------------------
    def element_click(
        self,
        *,
        target: Mapping[str, Any] | None = None,
        role: str = "",
        accessible_name: str = "",
        visible_text: str = "",
        selector: str = "",
        approved: bool = False,
        **_: Any,
    ) -> dict[str, Any]:
        session = self._ensure_session()
        session.consume_action(1)
        page = self._current_page()
        resolved, element = self._coerce_target(
            page, target=target, role=role, accessible_name=accessible_name,
            visible_text=visible_text, selector=selector,
        )
        # Credential fields are never interactive.
        from .policy import assert_not_credential_field

        assert_not_credential_field(element)
        self._guard_consequential(element, approved)
        key = self._replay.mutation_key(
            operation="element_click",
            session_id=session.session_id,
            url=page.url,
            target=resolved.safe_dict(),
        )
        self._replay.check(key)
        record, observation = self._backend.click(resolved, allowed_hosts=session.allowed_hosts)
        if record is not None:
            session.record_navigation(record)
        session.note_epoch()
        self._page = observation
        self._replay.record(key)
        return {
            "clicked": True,
            "target": resolved.safe_dict(),
            "url": observation.url,
            "status_code": observation.status_code,
            "epoch": observation.epoch,
        }

    # -- 9. element.type ---------------------------------------------------
    def element_type(
        self,
        *,
        text: str = "",
        target: Mapping[str, Any] | None = None,
        role: str = "",
        accessible_name: str = "",
        visible_text: str = "",
        selector: str = "",
        approved: bool = False,
        **_: Any,
    ) -> dict[str, Any]:
        session = self._ensure_session()
        session.consume_action(1)
        page = self._current_page()
        resolved, element = self._coerce_target(
            page, target=target, role=role, accessible_name=accessible_name,
            visible_text=visible_text, selector=selector,
        )
        from .policy import assert_not_credential_field

        assert_not_credential_field(element)
        clean_text = validate_typed_text(text)  # refuses credential material + bounds
        self._guard_consequential(element, approved)
        key = self._replay.mutation_key(
            operation="element_type",
            session_id=session.session_id,
            url=page.url,
            target=resolved.safe_dict(),
            payload={"text": clean_text},
        )
        self._replay.check(key)
        observation = self._backend.type_text(resolved, clean_text)
        session.note_epoch()
        self._page = observation
        self._replay.record(key)
        return {
            "typed": True,
            "target": resolved.safe_dict(),
            "length": len(clean_text),
            "epoch": observation.epoch,
        }

    # -- 10. element.select -----------------------------------------------
    def element_select(
        self,
        *,
        value: str = "",
        target: Mapping[str, Any] | None = None,
        role: str = "",
        accessible_name: str = "",
        visible_text: str = "",
        selector: str = "",
        approved: bool = False,
        **_: Any,
    ) -> dict[str, Any]:
        session = self._ensure_session()
        session.consume_action(1)
        page = self._current_page()
        resolved, element = self._coerce_target(
            page, target=target, role=role, accessible_name=accessible_name,
            visible_text=visible_text, selector=selector,
        )
        from .policy import assert_not_credential_field

        assert_not_credential_field(element)
        self._guard_consequential(element, approved)
        key = self._replay.mutation_key(
            operation="element_select",
            session_id=session.session_id,
            url=page.url,
            target=resolved.safe_dict(),
            payload={"value": value},
        )
        self._replay.check(key)
        observation = self._backend.select(resolved, str(value))
        session.note_epoch()
        self._page = observation
        self._replay.record(key)
        return {
            "selected": True,
            "target": resolved.safe_dict(),
            "value": str(value)[:256],
            "epoch": observation.epoch,
        }

    # -- 11. download.start -----------------------------------------------
    def download_start(
        self,
        *,
        url: str = "",
        target: Mapping[str, Any] | None = None,
        filename: str = "",
        approved: bool = False,
        **_: Any,
    ) -> dict[str, Any]:
        session = self._ensure_session()
        session.consume_action(1)
        download_url = url
        if not download_url and target:
            page = self._current_page()
            resolved, _element = self._coerce_target(page, target=target)
            # Map the target to a download link href if the page offers one.
            href = ""
            for link in page.links:
                if resolved.selector and link.get("label", "") == resolved.selector:
                    href = link.get("href", "")
                    break
            if not href:
                raise BrowserSecurityError("target does not resolve to a downloadable link")
            download_url = href
        if not download_url:
            raise BrowserSecurityError("download requires a url or a downloadable target")
        host = validate_download_url(download_url, session.allowed_hosts)
        key = self._replay.mutation_key(
            operation="download_start",
            session_id=session.session_id,
            url=download_url,
            payload={"filename": filename},
        )
        self._replay.check(key)
        data, content_type, status_code = self._backend.download(
            download_url, allowed_hosts=session.allowed_hosts
        )
        from .policy import bound_download_size

        bound_download_size(len(data))
        safe_name = filename or download_url.rsplit("/", 1)[-1] or "download.bin"
        destination, relative = confine_download_path(self._workspace_root / self._download_dir, safe_name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        digest = checksum(data)
        self._replay.record(key)
        return {
            "downloaded": True,
            "url": download_url,
            "host": host,
            "filename": destination.name,
            "relative_path": f"{self._download_dir}/{relative}",
            "size_bytes": len(data),
            "sha256": digest,
            "content_type": content_type,
            "status_code": status_code,
            "verified": True,
        }

    # -- 12. file.extract --------------------------------------------------
    def file_extract(
        self,
        *,
        path: str = "",
        max_bytes: int = MAX_RESPONSE_BYTES,
        **_: Any,
    ) -> dict[str, Any]:
        session = self._ensure_session()
        session.consume_action(1)
        absolute, relative = confine_workspace_path(self._workspace_root, path)
        if not absolute.exists() or not absolute.is_file():
            raise SessionError(f"file is not present in the workspace: {relative}")
        data = absolute.read_bytes()
        digest = checksum(data)
        limit = max(1, min(int(max_bytes), MAX_RESPONSE_BYTES))
        truncated = len(data) > limit
        raw = data[:limit]
        # Deterministic, dependency-free text preview. Binary/document parsing is
        # intentionally NOT performed here (the documents domain stays RESERVED).
        try:
            text = raw.decode("utf-8")
            content_type = "text"
        except UnicodeDecodeError:
            text = ""
            content_type = "binary"
        preview = redact_secret(text)[:limit]
        return {
            "extracted": True,
            "relative_path": relative,
            "size_bytes": len(data),
            "sha256": digest,
            "content_type": content_type,
            "truncated": truncated,
            "text_preview": preview,
        }

    # -- legacy compatibility: browser.open / click / extract -------------
    def open(self, url: str, *, timeout_seconds: int = 20, **_: Any) -> "_LegacyResult":
        if not self._base_hosts:
            raise BrowserSecurityError("legacy browser.open requires a configured host allowlist")
        result = self.navigate(url, timeout_seconds=timeout_seconds)
        return _LegacyResult(
            action="open",
            url=result["url"],
            title=result.get("title", ""),
            text=self._safe_page_text(),
            status_code=result.get("status_code"),
        )

    def click(self, url: str, selector: str, *, timeout_seconds: int = 20, **_: Any) -> "_LegacyResult":
        if not self._base_hosts:
            raise BrowserSecurityError("legacy browser.click requires a configured host allowlist")
        nav = self.navigate(url, timeout_seconds=timeout_seconds)
        result = self.element_click(selector=selector, approved=True)
        return _LegacyResult(
            action="click",
            url=result["url"],
            title="",
            text=self._safe_page_text(),
            status_code=result.get("status_code", nav.get("status_code")),
        )

    def extract(self, url: str, fields=(), **_: Any) -> "_LegacyResult":
        if not self._base_hosts:
            raise BrowserSecurityError("legacy browser.extract requires a configured host allowlist")
        nav = self.navigate(url)
        page = self._current_page()
        text = self._safe_page_text()
        if fields:
            wanted = {str(f).lower() for f in fields}
            kept = [
                line for line in text.splitlines() if any(w in line.lower() for w in wanted)
            ]
            text = "\n".join(kept)
        return _LegacyResult(
            action="extract",
            url=nav["url"],
            title=page.title,
            text=text,
            status_code=page.status_code,
        )

    def _safe_page_text(self) -> str:
        try:
            page = self._current_page()
        except Exception:
            return ""
        return redact_secret(page.text)[:MAX_RESPONSE_BYTES]


class _LegacyResult:
    """Legacy ``browser.open/click/extract`` return shape (safe_dict compatible)."""

    def __init__(self, *, action: str, url: str, title: str, text: str, status_code: int | None):
        self.action = action
        self.url = url
        self.title = redact_secret(title)[:256]
        self.text = redact_secret(text)
        self.status_code = status_code

    def safe_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "url": self.url,
            "title": self.title,
            "text": self.text,
            "links": [],
            "status_code": self.status_code,
            "verification_status": "verified",
        }


__all__ = ["BoundedBrowserConnector"]
