from __future__ import annotations

import argparse
import json
from pathlib import Path

from .engineering_pipeline import build_local_coding_pipeline, summarize_approval


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the bounded AI coding pipeline against a local checkout."
    )
    parser.add_argument("--root", default=".")
    parser.add_argument("--project", required=True)
    parser.add_argument("--severity", default="medium")
    parser.add_argument("--title", required=True)
    parser.add_argument("--detail", required=True)
    parser.add_argument("--recommendation", required=True)
    parser.add_argument("--affected", nargs="*", default=())
    parser.add_argument("--confidence", type=float, default=0.8)
    parser.add_argument("--allow-paid", action="store_true")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--max-revisions", type=int, default=2)
    args = parser.parse_args(argv)

    pipeline = build_local_coding_pipeline(
        Path(args.root),
        allow_paid=args.allow_paid,
        timeout_seconds=args.timeout,
        max_revisions=args.max_revisions,
    )
    run = pipeline.run_finding(
        project=args.project,
        severity=args.severity,
        title=args.title,
        detail=args.detail,
        recommendation=args.recommendation,
        affected_area=tuple(args.affected),
        confidence=max(0.0, min(1.0, args.confidence)),
    )

    print(json.dumps(summarize_approval(run), indent=2, sort_keys=True))
    print(f"status={run.status.value}")
    return 0 if run.candidate is not None and run.review is not None and run.approval_required else 1


if __name__ == "__main__":
    raise SystemExit(main())
