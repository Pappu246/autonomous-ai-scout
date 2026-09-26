"""Fail-closed policy for application adapters."""

from __future__ import annotations

from dataclasses import dataclass

from .models import ApplicationManifest, ApplicationRequest, InvocationKind


class ApplicationPolicyError(ValueError):
    """Raised when an application request violates the adapter boundary."""


@dataclass(frozen=True)
class ApplicationPolicy:
    max_argument_count: int = 64
    allow_process_launch: bool = False
    allow_shell: bool = False
    allow_unrestricted_javascript: bool = False
    allow_debugger_protocol: bool = False
    require_approval_for_consequential: bool = True

    def validate_manifest(self, manifest: ApplicationManifest) -> None:
        if not manifest.allowlisted:
            raise ApplicationPolicyError("adapter is not allowlisted")

    def validate_request(self, request: ApplicationRequest, manifest: ApplicationManifest) -> None:
        self.validate_manifest(manifest)
        if request.adapter_id != manifest.adapter_id:
            raise ApplicationPolicyError("request adapter does not match manifest")
        if request.operation not in manifest.supported_operations:
            raise ApplicationPolicyError("operation is not declared by the adapter")
        if len(request.arguments) > self.max_argument_count:
            raise ApplicationPolicyError("application arguments exceed policy limit")
        if request.invocation is InvocationKind.CONSEQUENT and self.require_approval_for_consequential and not request.approved:
            raise ApplicationPolicyError("consequential invocation requires explicit approval")
        if self.allow_process_launch or self.allow_shell or self.allow_unrestricted_javascript or self.allow_debugger_protocol:
            raise ApplicationPolicyError("unsafe application execution policy cannot be enabled")

    def allows(self, request: ApplicationRequest) -> bool:
        return request.invocation is not InvocationKind.CONSEQUENT or request.approved


DEFAULT_APPLICATION_POLICY = ApplicationPolicy()

__all__ = ["ApplicationPolicy", "ApplicationPolicyError", "DEFAULT_APPLICATION_POLICY"]
