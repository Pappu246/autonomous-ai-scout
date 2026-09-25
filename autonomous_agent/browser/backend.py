"""Browser backends for the bounded browser agent.

A backend is the *only* place that could ever touch a real browser. The design
is deliberately fail-closed:

- :class:`BaseBrowserBackend` defines the safe, structured surface. It exposes
  navigation, observation, semantic element queries and downloads. It never
  exposes arbitrary JavaScript evaluation, raw CDP/debugger access, or
  coordinate-based input.
- :class:`MockBrowserBackend` is a deterministic in-memory backend used for CI
  and tests. It models pages, redirects, elements, history and downloads with
  no network and no browser.
- :class:`UnsupportedBrowserBackend` is the default when no safe live backend
  is configured. Every operation fails closed.

There is intentionally no "LiveCDPBackend": raw Chrome DevTools Protocol /
debugger access would be arbitrary code execution and is out of scope by
policy. A real deployment would inject a narrowly-scoped, audited backend that
implements the same structured surface and nothing more.

The page ``epoch`` models *page identity*: it advances on navigation
(navigate/back/forward/reload) so that a target resolved on one page is
detected as stale once the agent moves to a different page. In-page
interactions (click/type/select that stay on the same page) do not change the
epoch, so a legitimately resolved target stays usable across a bounded
multi-step workflow.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Mapping

from .models import (
    BackendUnavailableError,
    BrowserSecurityError,
    ElementTarget,
    NavigationRecord,
    PageElement,
    PageObservation,
)
from .policy import (
    validate_download_url,
    validate_navigation_url,
    validate_redirect,
)


@dataclass
class _MockPage:
    """An in-memory page model for the deterministic backend."""

    url: str
    title: str = ""
    status_code: int = 200
    text: str = ""
    elements: tuple[PageElement, ...] = ()
    # Optional: url this page redirects to (single hop) and its status.
    redirect_to: str | None = None
    redirect_status: int = 302
    # Optional downloads offered by this page: url -> (bytes, content_type)
    downloads: Mapping[str, tuple[bytes, str]] = field(default_factory=dict)
    # Optional download links: element selector -> download url
    download_links: Mapping[str, str] = field(default_factory=dict)
    # Optional in-page navigation: element selector -> destination url
    link_targets: Mapping[str, str] = field(default_factory=dict)


class BaseBrowserBackend:
    """The safe, structured browser surface. Never exposes script/CDP/coords."""

    name = "base"

    def navigate(self, url: str, *, allowed_hosts: frozenset[str]) -> tuple[NavigationRecord, PageObservation]:
        raise BackendUnavailableError("base backend cannot navigate")

    def go_back(self) -> tuple[NavigationRecord, PageObservation]:
        raise BackendUnavailableError("base backend cannot navigate history")

    def go_forward(self) -> tuple[NavigationRecord, PageObservation]:
        raise BackendUnavailableError("base backend cannot navigate history")

    def reload(self) -> tuple[NavigationRecord, PageObservation]:
        raise BackendUnavailableError("base backend cannot reload")

    def observe(self) -> PageObservation:
        raise BackendUnavailableError("base backend cannot observe")

    def find_element(self, *, role: str = "", accessible_name: str = "", visible_text: str = "", selector: str = "") -> ElementTarget | None:
        raise BackendUnavailableError("base backend cannot resolve targets")

    def click(self, target: ElementTarget, *, allowed_hosts: frozenset[str]) -> tuple[NavigationRecord | None, PageObservation]:
        raise BackendUnavailableError("base backend cannot interact")

    def type_text(self, target: ElementTarget, text: str) -> PageObservation:
        raise BackendUnavailableError("base backend cannot interact")

    def select(self, target: ElementTarget, value: str) -> PageObservation:
        raise BackendUnavailableError("base backend cannot interact")

    def download(self, url: str, *, allowed_hosts: frozenset[str]) -> tuple[bytes, str, int]:
        raise BackendUnavailableError("base backend cannot download")


class UnsupportedBrowserBackend(BaseBrowserBackend):
    """Default backend: every operation fails closed (no live browser)."""

    name = "unsupported"

    def navigate(self, url, *, allowed_hosts):
        raise BackendUnavailableError("no safe browser backend is available in this environment")

    def go_back(self):
        raise BackendUnavailableError("no safe browser backend is available in this environment")

    def go_forward(self):
        raise BackendUnavailableError("no safe browser backend is available in this environment")

    def reload(self):
        raise BackendUnavailableError("no safe browser backend is available in this environment")

    def observe(self):
        raise BackendUnavailableError("no safe browser backend is available in this environment")

    def find_element(self, **kwargs):
        raise BackendUnavailableError("no safe browser backend is available in this environment")

    def click(self, target, *, allowed_hosts):
        raise BackendUnavailableError("no safe browser backend is available in this environment")

    def type_text(self, target, text):
        raise BackendUnavailableError("no safe browser backend is available in this environment")

    def select(self, target, value):
        raise BackendUnavailableError("no safe browser backend is available in this environment")

    def download(self, url, *, allowed_hosts):
        raise BackendUnavailableError("no safe browser backend is available in this environment")


class MockBrowserBackend(BaseBrowserBackend):
    """Deterministic, in-memory backend for CI and tests.

    Models a small site as a mapping of URL -> :class:`_MockPage`. Supports
    navigation, bounded redirects, history (back/forward/reload), semantic
    element resolution, element interaction that mutates page state, and
    downloads. It performs *no* network I/O and never executes scripts.
    """

    name = "mock"

    def __init__(self, pages: Mapping[str, _MockPage] | None = None) -> None:
        self._pages: dict[str, _MockPage] = dict(pages or {})
        self._history: list[NavigationRecord] = []
        self._index = -1
        self._epoch = 0
        self._current: PageObservation | None = None

    # -- page registration -------------------------------------------------
    def add_page(self, page: _MockPage) -> None:
        self._pages[page.url] = page

    def _resolve(self, url: str, allowed_hosts: frozenset[str]) -> tuple[str, _MockPage, int]:
        """Follow bounded redirects and return (final_url, page, redirect_depth)."""
        depth = 0
        current = url
        while True:
            validate_navigation_url(current, allowed_hosts)
            page = self._pages.get(current)
            if page is None:
                raise BackendUnavailableError(f"mock page not found: {current}")
            if page.redirect_to:
                next_url = page.redirect_to
                validate_redirect(current, next_url, allowed_hosts, depth=depth)
                current = next_url
                depth += 1
                if depth > 10:  # hard stop independent of the connector bound
                    raise BrowserSecurityError("mock redirect chain is unbounded")
                continue
            return current, page, depth

    def _observation(self, url: str, page: _MockPage, depth: int) -> PageObservation:
        return PageObservation(
            url=url,
            title=page.title,
            status_code=page.status_code,
            text=page.text,
            elements=tuple(page.elements),
            links=tuple(
                {"href": href, "label": selector}
                for selector, href in list(page.download_links.items())[:200]
            ),
            redirect_depth=depth,
            epoch=self._epoch,
        )

    # -- navigation --------------------------------------------------------
    def navigate(self, url, *, allowed_hosts):
        final_url, page, depth = self._resolve(url, allowed_hosts)
        self._epoch += 1  # navigation -> new page identity
        record = NavigationRecord(final_url, page.status_code, depth)
        del self._history[self._index + 1 :]
        self._history.append(record)
        self._index = len(self._history) - 1
        self._current = self._observation(final_url, page, depth)
        return record, self._current

    def _current_page(self) -> tuple[str, _MockPage]:
        if self._index < 0 or not self._history:
            raise BackendUnavailableError("no page is currently loaded")
        url = self._history[self._index].url
        page = self._pages.get(url)
        if page is None:
            raise BackendUnavailableError(f"mock page not found: {url}")
        return url, page

    def go_back(self):
        if self._index <= 0:
            raise BackendUnavailableError("no previous page in history")
        self._index -= 1
        self._epoch += 1
        url, page = self._current_page()
        rec = self._history[self._index]
        self._current = self._observation(url, page, rec.redirect_depth)
        return rec, self._current

    def go_forward(self):
        if self._index >= len(self._history) - 1:
            raise BackendUnavailableError("no forward page in history")
        self._index += 1
        self._epoch += 1
        url, page = self._current_page()
        rec = self._history[self._index]
        self._current = self._observation(url, page, rec.redirect_depth)
        return rec, self._current

    def reload(self):
        url, page = self._current_page()
        self._epoch += 1
        rec = self._history[self._index]
        self._current = self._observation(url, page, rec.redirect_depth)
        return rec, self._current

    def observe(self) -> PageObservation:
        if self._current is None:
            raise BackendUnavailableError("no page is currently loaded")
        return self._current

    # -- semantic target resolution ---------------------------------------
    def find_element(self, *, role="", accessible_name="", visible_text="", selector=""):
        if self._current is None:
            raise BackendUnavailableError("no page is currently loaded")
        name = accessible_name.strip().lower()
        text = visible_text.strip().lower()
        sel = selector.strip()
        for element in self._current.elements:
            if sel and element.selector == sel:
                return self._as_target(element)
            if role and element.role.lower() != role.strip().lower():
                continue
            if name and element.accessible_name.strip().lower() != name:
                continue
            if text and element.visible_text.strip().lower() != text:
                continue
            if not role and not name and not text and not sel:
                continue
            return self._as_target(element)
        return None

    def _as_target(self, element: PageElement) -> ElementTarget:
        return ElementTarget(
            element_id=element.element_id,
            selector=element.selector,
            role=element.role,
            accessible_name=element.accessible_name,
            epoch=self._epoch,
        )

    def _live_element(self, target: ElementTarget) -> tuple[str, _MockPage, PageElement]:
        url, page = self._current_page()
        for element in page.elements:
            if element.element_id == target.element_id and element.selector == target.selector:
                return url, page, element
        raise BackendUnavailableError("target element is not present on the current page")

    # -- interaction -------------------------------------------------------
    def click(self, target: ElementTarget, *, allowed_hosts: frozenset[str]):
        url, page, element = self._live_element(target)
        next_url = page.link_targets.get(element.selector)
        if next_url and next_url != url:
            return self.navigate(next_url, allowed_hosts=allowed_hosts)
        # In-page click: page identity is unchanged -> epoch is preserved.
        rec = self._history[self._index]
        self._current = self._observation(url, page, rec.redirect_depth)
        return None, self._current

    def _mutate_value(self, element_id: str, value: str) -> PageObservation:
        url, page = self._current_page()
        new_elements = tuple(
            PageElement(
                element_id=e.element_id,
                role=e.role,
                accessible_name=e.accessible_name,
                visible_text=e.visible_text,
                tag=e.tag,
                selector=e.selector,
                input_type=e.input_type,
                value=value if e.element_id == element_id else e.value,
                disabled=e.disabled,
                options=e.options,
            )
            for e in page.elements
        )
        self._pages[url] = _MockPage(
            url=page.url,
            title=page.title,
            status_code=page.status_code,
            text=page.text,
            elements=new_elements,
            redirect_to=page.redirect_to,
            redirect_status=page.redirect_status,
            downloads=page.downloads,
            download_links=page.download_links,
            link_targets=page.link_targets,
        )
        rec = self._history[self._index]
        # In-page mutation: page identity unchanged -> epoch preserved.
        self._current = self._observation(url, self._pages[url], rec.redirect_depth)
        return self._current

    def type_text(self, target: ElementTarget, text: str) -> PageObservation:
        _url, _page, element = self._live_element(target)
        return self._mutate_value(element.element_id, text)

    def select(self, target: ElementTarget, value: str) -> PageObservation:
        _url, _page, element = self._live_element(target)
        if element.options and value not in element.options:
            raise BackendUnavailableError("select value is not an available option")
        return self._mutate_value(element.element_id, value)

    # -- download ----------------------------------------------------------
    def download(self, url, *, allowed_hosts):
        validate_download_url(url, allowed_hosts)
        for page in self._pages.values():
            if url in page.downloads:
                data, content_type = page.downloads[url]
                return data, content_type, 200
        raise BackendUnavailableError(f"mock download not found: {url}")

    # -- helpers -----------------------------------------------------------
    def snapshot_history(self) -> list[NavigationRecord]:
        return list(self._history)

    def snapshot_index(self) -> int:
        return self._index


def checksum(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


__all__ = [
    "BaseBrowserBackend",
    "MockBrowserBackend",
    "UnsupportedBrowserBackend",
    "checksum",
]
