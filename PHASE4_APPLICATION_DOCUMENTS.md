# Phase 4: Bounded Application and Documents Domains

## 1. Executive Summary & Scope

Phase 4 activates and secures two structured, capability-bounded domains within the Autonomous AI Scout architecture:

1. **Documents Domain (`CapabilityDomain.DOCUMENTS`)**: Safe, structured inspection, bounded text/table extraction, single-page reading, and deterministic transformation (merge, split, convert) of workspace documents without executing embedded macros or active scripts.
2. **Application Domain (`CapabilityDomain.APPLICATION`)**: Bounded, adapter-mediated semantic application automation (session lifecycle, semantic view observation, and bounded command execution) without raw shell execution, arbitrary process spawning, or unconfined UI scripting.

All implementations strictly adhere to fail-closed security invariants, deterministic mock boundaries, cryptographic replay protection, post-condition verification, and strict human-in-the-loop approval gates for mutating operations.

---

## 2. Milestone Architecture & Implementation Journey

### Milestone 1 (M1) — Models & Security Policies
- **Domain Activation**: Transitioned `APPLICATION` and `DOCUMENTS` domains from `RESERVED` to `ACTIVE` in `autonomous_agent/digital/domains.py`.
- **Domain Models (`autonomous_agent/documents/models.py`, `autonomous_agent/application/models.py`)**:
  - Implemented immutable, bounded data structures: `DocumentMetadata`, `DocumentPage`, `DocumentTable`, `DocumentObservation`, `DocumentTransformRequest`, `DocumentTransformResult`, `ApplicationObservation`, `ApplicationCommand`, `ApplicationSessionSnapshot`, and `ApplicationDescriptor`.
  - Defined explicit fail-closed domain exception hierarchies (`DocumentError`, `DocumentSecurityError`, `DocumentFormatError`, `DocumentSessionError`, `DocumentReplayError`, `ApplicationError`, `ApplicationSecurityError`, `ApplicationSessionError`, `ApplicationReplayError`, `ActionBudgetExceededError`, `BackendUnavailableError`).
  - Integrated consequential action regex detection for high-impact document mutations (signatures, decryption, page wiping) and application operations (account deletion, payments, credential transfers).
- **Security Policies (`autonomous_agent/documents/policy.py`, `autonomous_agent/application/policy.py`)**:
  - Workspace path confinement rejecting POSIX traversals, Windows drive letters (`C:\`, `D:\`), UNC network shares, and oversize paths.
  - Extension filtering and blocked lists rejecting executable, script, and macro-enabled formats (`.exe`, `.dll`, `.bat`, `.cmd`, `.ps1`, `.vbs`, `.js`, `.py`, `.sh`, `.pif`, `.docm`, `.xlsm`, `.dotm`, `.xltm`, `.pptm`, `.jar`, `.scr`).
  - PDF active content scanner detecting and rejecting dangerous PDF tokens (`/JavaScript`, `/JS`, `/Launch`, `/EmbeddedFiles`, `/SubmitForm`, `/ImportData`, `/RichMedia`).
  - Application allowlists and executable denylists blocking system shells and utilities (`cmd`, `powershell`, `pwsh`, `bash`, `sh`, `zsh`, `wscript`, `cscript`, `regedit`, `curl`, `wget`, `python`, `node`, `ruby`, `perl`, `rundll32`, `certutil`).
  - Dangerous flag blocking for debugging and script evaluation (`--inspect`, `--remote-debugging-port`, `--eval`, `-e`, `--no-sandbox`, `--disable-web-security`).

### Milestone 2 (M2) — Deterministic Backends, Sessions, Targets, Replay & Connectors
- **Deterministic Backends (`autonomous_agent/documents/backend.py`, `autonomous_agent/application/backend.py`)**:
  - `BaseDocumentsBackend` & `BaseApplicationBackend`: Abstract contracts defining structured domain interfaces.
  - `MockDocumentsBackend` & `MockApplicationBackend`: In-memory, deterministic, workspace-confined backends for unit testing and CI.
  - `UnsupportedDocumentsBackend` & `UnsupportedApplicationBackend`: Fail-closed default backends raising `BackendUnavailableError` whenever real drivers are unavailable.
- **Session Management (`autonomous_agent/documents/session.py`, `autonomous_agent/application/session.py`)**:
  - State machines (`OPEN`, `CLOSED`, `SUSPENDED`, `ERROR`) enforcing session lifecycles, active document tracking, action budgets, and secret-free snapshots.
- **Semantic Target Resolution (`autonomous_agent/documents/target.py`, `autonomous_agent/application/target.py`)**:
  - `SemanticDocumentTargetResolver`: Resolves document pages and extracted tables bound to observation epochs; fails closed on stale or foreign document targets.
  - `ApplicationSemanticTargetResolver`: Resolves semantic views and active documents bound to application epochs without coordinate-based automation.
- **Replay Protection (`autonomous_agent/documents/replay.py`, `autonomous_agent/application/replay.py`)**:
  - `DocumentsReplayProtector` & `ApplicationReplayProtector`: SHA-256 mutation key digest trackers preventing duplicate execution of state-mutating actions upon session resumption.
- **Bounded Connectors (`autonomous_agent/documents/connector.py`, `autonomous_agent/application/connector.py`)**:
  - `BoundedDocumentsConnector` & `BoundedApplicationConnector`: Safe domain entrypoints coordinating policy enforcement, target validation, budget tracking, and replay safety.

### Milestone 3 (M3) — Registry, Provider Bindings, Execution Engine & Verification Observers
- **Tool Registry (`autonomous_agent/tool_registry.py`)**:
  - Registered 9 tools with risk levels, read/write modes, and approval requirements:
    - `documents.inspect`, `documents.extract_text`, `documents.extract_tables`, `documents.read_page`, `documents.transform`.
    - `application.open_session`, `application.close_session`, `application.observe`, `application.command.execute`.
- **Sandbox Boundary & Execution Engine (`autonomous_agent/sandbox.py`, `autonomous_agent/execution_engine.py`)**:
  - Added safe sandbox dispatch handlers `_run_documents` and `_run_application` enforcing operation allowlists and payload parameter validation.
- **Provider Bindings & Capability Declarations (`autonomous_agent/digital/provider.py`, `autonomous_agent/digital/builtins.py`)**:
  - Mapped digital capability IDs to tool specs and sandbox bindings in `TOOL_SANDBOX_BINDINGS`.
  - Registered built-in declarations in `DEFAULT_CAPABILITIES` with post-condition observer wiring.
- **Verification Observers (`autonomous_agent/documents/observer.py`, `autonomous_agent/application/observer.py`)**:
  - `DocumentsPostConditionObserver`: Validates existence and SHA-256 checksums of transformed files.
  - `ApplicationPostConditionObserver`: Confirms active application state and session synchronization.

### Milestone 4 (M4) — Adversarial Security Hardening
- **Adversarial Test Suites**:
  - Created `tests/test_documents_safety.py` (75 tests), `tests/test_application_safety.py` (87 tests), and `tests/test_phase4_safety.py` (14 tests).
- **Core Security Invariant Verification**:
  - Verified all 16 process-wide security invariants through unit tests, AST source code auditing, and adversarial input fuzzing.
- **Targeted Hardening**:
  - Hardened workspace path confinement to detect and reject Windows drive letters (`C:\`, `D:\`) and UNC shares cross-platform.
  - Added entrypoint path validation in `BoundedDocumentsConnector.transform`.
  - Enhanced recursive secret redaction in `redact_secret_material` to scrub sensitive dictionary keys (`api_key`, `token`, `password`, `secret`, `private_key`, `client_secret`, `authorization`, `credentials`) in addition to values.
  - Added `.pif` to blocked document extensions.

---

## 3. Capability Catalog

| Capability ID | Bound Tool | Read/Write | Risk Level | Safe Autonomous | Approval Mode | Verification Observer |
|---|---|---|---|---|---|---|
| `documents:inspect` | `documents.inspect` | Read-only | Low | Yes | Autonomous | `DocumentsPostConditionObserver` |
| `documents:extract_text` | `documents.extract_text` | Read-only | Low | Yes | Autonomous | `DocumentsPostConditionObserver` |
| `documents:extract_tables` | `documents.extract_tables` | Read-only | Low | Yes | Autonomous | `DocumentsPostConditionObserver` |
| `documents:read_page` | `documents.read_page` | Read-only | Low | Yes | Autonomous | `DocumentsPostConditionObserver` |
| `documents:transform` | `documents.transform` | Mutating (Write) | High | No | Require Approval | `DocumentsPostConditionObserver` (SHA-256 + existence) |
| `application:session.open` | `application.open_session` | Write / Lifecycle | Medium | No | Require Approval | `ApplicationPostConditionObserver` |
| `application:session.close` | `application.close_session` | Write / Lifecycle | Low | Yes | Autonomous | `ApplicationPostConditionObserver` |
| `application:observe` | `application.observe` | Read-only | Low | Yes | Autonomous | `ApplicationPostConditionObserver` |
| `application:command.execute` | `application.command.execute` | Mutating (Write) | High | No | Require Approval | `ApplicationPostConditionObserver` |

---

## 4. Security Invariants & Defensive Controls

1. **No Arbitrary Shell or Subprocess Execution**: Zero occurrences of `os.system`, `subprocess.Popen`, `subprocess.run`, `shell=True`, `eval`, or `exec` in application and document packages.
2. **Strict Workspace Path Confinement**: Relative paths only; directory traversals (`..`), absolute POSIX paths, Windows drive letters, and UNC shares are blocked before filesystem access.
3. **Resource Bound Enforcements**: Strict bounds on file sizes (25MB), text extractions (100k chars), page counts (1000), table sizes (10k rows x 200 cols), argument lengths, and session action budgets.
4. **Secret Material Redaction**: Recursive redaction strips credentials, API keys, tokens, and passwords from logs, snapshots, metadata, and capability execution evidence.
5. **Prompt Injection Containment**: Untrusted document and application content is wrapped with boundary markers (`--- BEGIN/END UNTRUSTED CONTENT ---`) and tagged as `TrustLevel.EXTERNAL`. Untrusted data cannot authorize actions or escalate privileges.
6. **Consequential Action Approval Gating**: Mutating transformations and high-impact commands fail closed unless explicit approval is granted by an authorized caller.
7. **Replay Protection**: Cryptographic SHA-256 digests identify mutating operations; identical replays within the same session state are rejected.
8. **Independent Post-Condition Verification**: Observers independently inspect real-world artifacts (e.g., checksum verification of transformed files) before issuing `VERIFIED` status.
9. **Fail-Closed Unsupported Backends**: Unconfigured environments fail closed with `BackendUnavailableError` rather than fabricating execution results.

---

## 5. Backend Realities & Environment Limitations

- **Mock Backends in CI/Testing**: Deterministic, in-memory mock backends (`MockDocumentsBackend`, `MockApplicationBackend`) are implemented for testing and CI.
- **Fail-Closed Production Default**: When running without external adapters or certified document conversion engines, `UnsupportedDocumentsBackend` and `UnsupportedApplicationBackend` fail closed.
- **No Fabricated Execution**: The system makes no simulated claims of live third-party Office/app automation unless real backends are configured and active.
