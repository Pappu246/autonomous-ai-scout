from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, HttpUrl


class AccessStatus(str, Enum):
    VERIFIED_FREE = "verified_free"
    UNKNOWN = "unknown"
    EXPIRED = "expired"
    PAID_ONLY = "paid_only"
    BLOCKED = "blocked"


class ModelCandidate(BaseModel):
    provider: str
    model: str
    source_url: HttpUrl
    access_status: AccessStatus = AccessStatus.UNKNOWN
    evidence: str = ""
    limits: dict[str, Any] = Field(default_factory=dict)
    discovered_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None
    source_hash: str = ""
    source_changed: bool = False
    benchmark_latency_ms: int | None = None
    benchmark_ok: bool | None = None


class ProjectFinding(BaseModel):
    repository: str
    severity: str
    title: str
    detail: str
    recommendation: str
    confidence: float = Field(default=0.8, ge=0, le=1)


class Opportunity(BaseModel):
    title: str
    description: str
    score: float = Field(ge=0, le=100)
    source_url: HttpUrl | None = None
    next_step: str


class ScoutReport(BaseModel):
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    models: list[ModelCandidate] = Field(default_factory=list)
    project_findings: list[ProjectFinding] = Field(default_factory=list)
    opportunities: list[Opportunity] = Field(default_factory=list)
    meaningful_change: bool = False
    notes: list[str] = Field(default_factory=list)
