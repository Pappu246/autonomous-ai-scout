from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from .provider_router import providers_from_env


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


def run_checks(root: str | Path = ".") -> tuple[Check, ...]:
    workspace = Path(root).resolve()
    checks: list[Check] = []

    checks.append(
        Check(
            "python",
            sys.version_info >= (3, 11),
            f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        )
    )
    checks.append(
        Check(
            "workspace",
            workspace.is_dir(),
            str(workspace),
        )
    )
    checks.append(
        Check(
            "ci workflow",
            (workspace / ".github" / "workflows" / "ci.yml").is_file(),
            ".github/workflows/ci.yml present",
        )
    )

    providers = providers_from_env()
    configured = len(providers)
    checks.append(
        Check(
            "coding providers",
            configured > 0,
            f"{configured} provider route(s) configured; credentials are checked by the router at execution time",
        )
    )

    token_env = os.getenv("GITHUB_TOKEN_ENV", "GITHUB_TOKEN").strip() or "GITHUB_TOKEN"
    token_present = bool(os.getenv(token_env, "").strip())
    checks.append(
        Check(
            "github credential",
            token_present,
            f"{token_env} is {'present' if token_present else 'missing'}",
        )
    )

    if os.getenv("ENABLE_FREE_BENCHMARKS", "false").lower() == "true":
        checks.append(
            Check(
                "free benchmark policy",
                os.getenv("MAX_FREE_BENCHMARKS", "2").strip().isdigit()
                and 0 <= int(os.getenv("MAX_FREE_BENCHMARKS", "2").strip()) <= 10,
                "ENABLE_FREE_BENCHMARKS=true; benchmark cap is bounded to 0..10",
            )
        )
    else:
        checks.append(
            Check(
                "free benchmark policy",
                True,
                "free benchmarking disabled",
            )
        )

    return tuple(checks)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Check Autonomous AI Scout runtime prerequisites")
    parser.add_argument("--root", default=".")
    args = parser.parse_args(argv)

    checks = run_checks(args.root)
    for item in checks:
        print(f"[{'OK' if item.ok else 'MISSING'}] {item.name}: {item.detail}")
    return 0 if all(item.ok for item in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
