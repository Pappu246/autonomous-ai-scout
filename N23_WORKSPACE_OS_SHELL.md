# N23 — Workspace / OS / Shell Agent

## Objective

Provide a controlled workspace shell boundary for safe local inspection and validation without exposing unrestricted CMD/PowerShell execution.

## Capability

Supported shell-style operations:

- `pwd` — report the approved workspace root;
- `ls` / `dir` — bounded directory listing;
- `cat` / `type` — bounded workspace-relative file read;
- `python -m py_compile <file>` — isolated syntax validation when network-isolated subprocess execution is available.

## Safety boundary

- No `shell=True` or shell-string parsing is used.
- Shell metacharacters are rejected.
- Paths are resolved and must remain inside the approved root.
- The shell is represented by a dedicated `WORKSPACE_SHELL` capability rather than sharing the filesystem capability.
- Commands are allowlisted by executable and argument shape.
- Network-isolated Python subprocess execution fails closed when the required isolation primitive is unavailable.
- The canonical execution engine routes `workspace.shell` through a dedicated sandbox operation.
- Writes and destructive/admin operations remain outside this shell path and continue through existing approval-gated tools.

## Capability proof

Runnable test:

```bash
pytest -q tests/test_workspace_shell.py
```

The tests demonstrate root-bound listing/reading, path-escape blocking, metacharacter blocking, arbitrary-command rejection, and safe routing of shell requests.

## Limitations after N23

Still deferred:

- authenticated browser sessions and credential brokering (N28);
- hostile-content / prompt-injection defense (N29);
- consequence-aware approval policy expansion (N30);
- rich desktop GUI interaction and screen automation.

## Acceptance gate

N23 is VERIFIED only when workspace shell tests and full CI are green and no unrestricted command execution path is introduced.
