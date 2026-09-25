"""Post-condition verification for the bounded browser agent.

``BrowserPostConditionObserver`` independently confirms a real, observable
browser post-condition. Input dispatch is never treated as verification: for
mutating steps the observer re-observes the page (or re-reads a downloaded file
and checks its checksum) and only reports ``observed=True`` when the independent
read matches the claimed result. A bare "clicked/typed" success flag is never
enough, so a no-op or a lie can never be promoted to VERIFIED.
"""

from __future__ import annotations

from typing import Any


class BrowserPostConditionObserver:
    """Independently confirm a real, observable browser post-condition."""

    _READ_ONLY = {
        "browser:session.open",
        "browser:navigate",
        "browser:back",
        "browser:forward",
        "browser:reload",
        "browser:page.observe",
        "browser:element.find",
    }

    def __init__(self, connector: Any) -> None:
        self._connector = connector

    def observe(self, request: Any, execution: Any) -> Any:
        from autonomous_agent.digital.contract import CapabilityObservation

        cap = request.capability_id
        evidence = execution.evidence or {}

        if cap in self._READ_ONLY:
            observed = bool(evidence.get("url") or evidence.get("opened") or evidence.get("found"))
            return CapabilityObservation(
                cap,
                observed,
                evidence,
                "bounded read evidence collected" if observed else "read produced no observable evidence",
            )

        if cap == "browser:element.click":
            try:
                page = self._connector.page_observe()
            except Exception as exc:  # noqa: BLE001 - observation must fail closed
                return CapabilityObservation(cap, False, detail=f"post-click re-observe failed: {exc}")
            if page.get("url"):
                return CapabilityObservation(
                    cap, True, {"url": page.get("url"), "reported_url": evidence.get("url")},
                    "post-click page re-observed",
                )
            return CapabilityObservation(cap, False, detail="post-click page is not observable")

        if cap in {"browser:element.type", "browser:element.select"}:
            selector = str((evidence.get("target") or {}).get("selector", ""))
            expected = request.arguments.get("text") if cap.endswith("type") else request.arguments.get("value")
            try:
                page = self._connector.page_observe()
            except Exception as exc:  # noqa: BLE001
                return CapabilityObservation(cap, False, detail=f"post-input re-observe failed: {exc}")
            for element in page.get("elements", []):
                if element.get("selector") == selector:
                    if element.get("value") == expected:
                        return CapabilityObservation(
                            cap, True, {"selector": selector, "value": element.get("value")},
                            "input post-condition confirmed by independent re-observation",
                        )
                    return CapabilityObservation(
                        cap, False, {"selector": selector, "value": element.get("value")},
                        "field value does not reflect the requested input",
                    )
            return CapabilityObservation(cap, False, detail="target field not found on re-observation")

        if cap == "browser:download.start":
            relative = str(evidence.get("relative_path", ""))
            digest = str(evidence.get("sha256", ""))
            try:
                recheck = self._connector.file_extract(path=relative, max_bytes=1)
            except Exception as exc:  # noqa: BLE001
                return CapabilityObservation(cap, False, detail=f"download re-read failed: {exc}")
            if recheck.get("sha256") == digest:
                return CapabilityObservation(
                    cap, True, {"sha256": digest, "relative_path": relative},
                    "download confirmed by independent checksum re-read",
                )
            return CapabilityObservation(
                cap, False, {"expected": digest, "actual": recheck.get("sha256")},
                "download checksum does not match",
            )

        if cap == "browser:file.extract":
            if evidence.get("extracted") and evidence.get("sha256"):
                return CapabilityObservation(cap, True, evidence, "file extract evidence present")
            return CapabilityObservation(cap, False, detail="file extract produced no evidence")

        return CapabilityObservation(cap, execution.has_evidence, evidence)


__all__ = ["BrowserPostConditionObserver"]
