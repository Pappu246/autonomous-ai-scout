# Phase 12 — Operational Readiness Reconciliation

## Purpose

Phase 12 closes the repository-level gap between the implemented control stack and
its top-level operational documentation.

It does not introduce a new executor, permission system, deployment path, or
autonomous mutation capability.

## Reconciled facts

The current implementation declares the computer, application, documents, email,
calendar, browser, web, filesystem, os_shell, testing, github, workflow and
communication domains as active. Domain availability remains capability-backed:
an active domain is usable only when a registered capability is actually available.
Unsupported external backends still fail closed.

Phase 12 also reconciles the README with the already-merged Phase 4, Phase 10 and
Phase 11 implementation/documentation.

## Regression gate

The Phase 12 regression test verifies:

1. application and documents remain active domains;
2. the real capability catalog reports active-domain availability;
3. readiness and production-safety evidence use their canonical structures;
4. N46 admission accepts matching task/authorization/execution evidence;
5. side-effecting execution still requires explicit approval.

## External-environment boundary

This phase does not claim live third-party application or document drivers.
Phase 4 provides deterministic/mock backends and unsupported-backend fail-closed
behaviour. Live providers remain configuration-dependent.

## Status

Phase 12 is complete when this document, the reconciled README, the regression
test, and CI all agree with current main.
