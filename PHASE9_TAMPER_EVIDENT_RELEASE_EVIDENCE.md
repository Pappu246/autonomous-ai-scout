# Phase 9 — Tamper-Evident Release Evidence

## Status

Phase 9 adds immutable, deterministic release evidence on top of the Phase 8
controlled release boundary.

```
Phase 6 verification
      ↓
Phase 7 live GitHub evidence
      ↓
Phase 8 release eligibility
      ↓
Phase 9 immutable evidence record
```

The evidence record is audit data, not a deployment authority.

## Evidence bound into the record

- repository identity
- non-production release target
- exact commit SHA
- pull-request number
- artifact SHA-256
- Phase 6 request digest
- Phase 6 evidence digest
- review-policy result
- CI result
- explicit release approval
- Phase 8 eligibility state
- deployment permission, which is always false
- optional previous evidence digest for append-only chaining

The complete canonical record is SHA-256 hashed into `evidence_digest`.
Verification reconstructs the record and checks the exact identities again.

## Security boundary

The module contains no filesystem, network, subprocess, environment, credential,
merge, deployment, or workflow-dispatch path.

`ReleaseEvidence` is frozen/immutable. Tampering with any field or digest makes
verification fail.

An evidence chain can be anchored with `previous_evidence_digest`; this does
not itself create persistence or prove external storage integrity.

## Non-goals

- deployment
- merge
- automatic approval
- secrets or credentials
- infrastructure mutation
- external signing service
