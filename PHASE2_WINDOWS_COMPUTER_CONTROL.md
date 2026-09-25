# Phase 2 — Bounded Windows Computer Control

## Overview

Phase 2 activates the previously reserved `computer` capability domain and integrates bounded Windows computer control into the universal digital agent architecture established in Phase 1.

The canonical execution lifecycle remains authoritative:
```text
User Goal
   → Intent Understanding
   → Task Planning
   → Capability / Tool Selection
   → Authorization + Risk Evaluation
   → Execution
   → Observation
   → Verification
   → Recovery / Retry / Resume
   → Final Result
```

No second runtime or secondary authorization path is introduced. Computer control operates strictly under the existing registry, sandbox, authorization broker, audit log, and checkpoint stores.

---

## Registered Computer Capabilities (11 Capabilities)

All 11 computer capabilities are registered in the central `ToolRegistry` and bound to the sandbox execution layer:

| Capability ID | Tool Name | Mode | Risk | Safe Autonomous | Approval | Description |
|---|---|---|---|---|---|---|
| `computer:screen.capture` | `computer.screen.capture` | Read-only | Low | Yes | None | Capture screen content or sub-region bitmap metadata |
| `computer:window.list` | `computer.window.list` | Read-only | Low | Yes | None | Enumerate visible top-level windows with handles and bounds |
| `computer:window.active` | `computer.window.active` | Read-only | Low | Yes | None | Retrieve the active foreground window |
| `computer:window.focus` | `computer.window.focus` | Read-only | Low | Yes | None | Activate and bring a targeted window to foreground |
| `computer:app.launch` | `computer.app.launch` | Controlled Write | High | No | Explicit | Launch an allowlisted executable with replay protection |
| `computer:mouse.move` | `computer.mouse.move` | Read-only | Low | Yes | None | Move mouse pointer to valid screen coordinates |
| `computer:mouse.click` | `computer.mouse.click` | Controlled Write | High | No | Explicit | Click mouse button at coordinates with action budget |
| `computer:keyboard.type` | `computer.keyboard.type` | Controlled Write | High | No | Explicit | Type text with credential inspection and replay protection |
| `computer:keyboard.hotkey` | `computer.keyboard.hotkey` | Controlled Write | High | No | Explicit | Trigger approved key combinations |
| `computer:clipboard.read` | `computer.clipboard.read` | Read-only | Low | Yes | None | Read clipboard text with automatic credential redaction |
| `computer:clipboard.write` | `computer.clipboard.write` | Controlled Write | High | No | Explicit | Write text to clipboard with size bounds |

---

## Windows Backend Implementation

The Windows backend (`WindowsBackend`) is implemented purely using Python standard library `ctypes` (`user32.dll`, `gdi32.dll`, `kernel32.dll`).
- **Zero external Windows automation dependencies**: No third-party packages (e.g. pywin32, pyautogui, pywinauto) required.
- **Process Launching**:
  - Uses strictly list/argv invocation.
  - `shell=False` exclusively.
  - Only one single `subprocess.Popen` call site in production code.
  - No `os.system` or `subprocess.run`.
  - No shell command string concatenation.

---

## Authorization & Consequence Policy

- **Authoritative Process Broker**: Capabilities cannot authorize themselves. The central `ToolRegistry` and `ConsequenceAwareApprovalPolicy` evaluate all steps.
- **Side Effect Gating**: All mutating computer actions (`app.launch`, `mouse.click`, `keyboard.type`, `keyboard.hotkey`, `clipboard.write`) are classified as `CONTROLLED_WRITE` / `High Risk` and require explicit human approval before execution.
- **Grant Narrowing**: The `computer` capability must be explicitly granted; ungranted computer actions fail closed.
- **Untrusted Origin Defense**: Requests originating from untrusted contexts (`EXTERNAL`, `TOOL_RESULT`, `MEMORY`) cannot trigger mutating side effects.

---

## Platform Behavior

- **Unsupported Platforms Fail Closed**: On Linux, macOS, or other non-Windows platforms, the backend initializes as `UnsupportedPlatformBackend`, which raises `PlatformNotSupportedError` on any attempt to execute OS interactions.
- **Catalog Availability**: When running outside Windows (and without an injected mock connector), computer capabilities report `CapabilityAvailability.DISABLED`, and `domain_status` honestly reports `usable = False`. Linux CI never fabricates Windows success.
- **Integration Tests**: Windows-specific integration tests are marked with `@pytest.mark.skipif(not is_windows(), ...)` and skip on non-Windows environments.

---

## Target Validation & Bounds

1. **Process Launching Security**:
   - Application basenames are checked against an exhaustive denylist: `cmd.exe`, `powershell.exe`, `pwsh.exe`, `bash`, `sh`, `wsl.exe`, `cscript.exe`, `wscript.exe`, `mshta.exe`, `certutil.exe`, `reg.exe`, `regedit.exe`, `rundll32.exe`, `installutil.exe`, `bitsadmin.exe`, `vssadmin.exe`, `format.com`, `diskpart.exe`, `curl.exe`, `wget.exe`, `shutdown.exe`, etc.
   - Rejection of shell metacharacters: `;`, `&`, `|`, `` ` ``, `$`, `>`, `<`, `\n`, `\r`, `\x00`.
   - Maximum 50 arguments; maximum 2048 characters per argument.
2. **Coordinate & Display Bounds**:
   - Coordinates `(x, y)` are strictly validated against display metrics (`0 <= x <= display.width`, `0 <= y <= display.height`).
   - Negative coordinates and off-screen coordinates fail closed with `ComputerSecurityError`, preventing blind clicking.
3. **Action Budget**:
   - `ActionBudget` enforces a configurable action limit (default 50 units per run) across clicks, moves, keystrokes, and launches.
   - Prevents infinite loops or uncontrolled desktop flooding.
   - Consumption correctly allows the final permitted action (`used + count <= limit`).

---

## Verification & Observable State

- **Observable Evidence Mandatory**: Input event dispatch alone (e.g. mouse click dispatched, key event sent) **never** produces `VERIFIED`.
- `ComputerPostConditionObserver` verifies:
  - `app.launch`: Observes the new process ID or matching window in the active window list.
  - `window.focus`: Verifies that `window_active()` matches the targeted window handle or title.
  - `clipboard.write`: Performs an independent read-back from the clipboard to confirm byte-for-byte content match.
  - `mouse.click` / `keyboard.type`: Requires observable UI state change evidence before returning a verified observation.

---

## Replay Protection

State-mutating computer actions implement strict replay protection:
- `computer.app.launch`: Checks if an instance of the target executable or window title is already running or was already launched under the given idempotency key. If so, reuses the existing instance without spawning duplicate processes on resume.
- `computer.keyboard.type`: Caches completed typing steps keyed by idempotency key and target window. On resume, re-executing the step returns the verified record without re-typing text into the UI.

---

## Credential Safety

- **Typed Text Inspection**: `validate_typed_text` inspects input strings for API keys, passwords, bearer tokens, and secrets. Detected secrets cause immediate rejection with `ComputerSecurityError`.
- **Clipboard Sanitization**: `clipboard_write` inspects and rejects raw credentials; `clipboard_read` automatically sanitizes output using `redact_text`.
- **Audit Redaction**: Secret material is redacted before appending to the hash-chained execution audit trail.

---

## Prompt Injection Handling

- Text extracted from the desktop (window titles, OCR/screen text, clipboard) is treated as untrusted external content (`TrustLevel.EXTERNAL`).
- Content is safely wrapped using `PromptInjectionGuard` in `<UNTRUSTED_DATA>` blocks.
- `PromptInjectionGuard` detects and blocks instruction override attempts (including "ignore previous instructions", "ignore all previous instructions", "disregard instructions").

---

## Known Limitations

1. **Operating System Restriction**: Real computer automation is supported only on Microsoft Windows. Linux CI fails closed and reports the domain as disabled.
2. **Interactive Desktop Requirement**: Full live execution requires an active, interactive desktop session with an unlocked screen.
3. **Elevated Privileges (UAC)**: Actions requiring User Account Control (UAC) elevation are not supported and are blocked by the process denylist.
4. **Phase 3 Boundary**: Phase 3 (document processing, application adapters, cross-domain macros) was **not** started and remains reserved.
