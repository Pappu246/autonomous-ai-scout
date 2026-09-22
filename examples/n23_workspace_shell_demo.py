from pathlib import Path
from tempfile import TemporaryDirectory

from autonomous_agent.workspace_shell import ControlledWorkspaceShell


def main() -> int:
    with TemporaryDirectory(prefix="scout-n23-") as directory:
        root = Path(directory)
        (root / "demo.txt").write_text("hello from workspace\n", encoding="utf-8")
        shell = ControlledWorkspaceShell(root)
        print("N23 Workspace / OS / Shell capability demo")
        for argv in (("pwd",), ("ls",), ("cat", "demo.txt"), ("curl", "https://example.com")):
            result = shell.run(argv)
            print(f"{argv}: success={result.success} status={result.exit_status} reason={result.reason}")
            if result.output:
                print(result.output)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
