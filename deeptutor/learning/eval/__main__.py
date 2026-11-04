"""Run with ``python -m deeptutor.learning.eval DATASET [--save REPORT]``."""

from __future__ import annotations

import argparse
from pathlib import Path

from .dataset import load_dataset
from .runner import evaluate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Offline next-assessment evaluation; no LLM calls or application database access."
    )
    parser.add_argument(
        "dataset", type=Path, help="JSON/JSONL dataset with anonymized attempt records"
    )
    parser.add_argument("--save", type=Path, help="write a full JSON report to a new local file")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--minimum-history", type=int, default=1)
    parser.add_argument("--delayed-days", type=float, default=1.0)
    parser.add_argument("--bins", type=int, default=10, help="number of calibration bins (1..100)")
    args = parser.parse_args(argv)
    try:
        dataset = load_dataset(args.dataset)
        report = evaluate(
            dataset,
            minimum_history=args.minimum_history,
            delayed_days=args.delayed_days,
            bins=args.bins,
        )
        if args.save is not None:
            # Exclusive creation also prevents accidentally overwriting the
            # input, an earlier report, or an application data file.
            with args.save.open("x", encoding="utf-8") as stream:
                stream.write(report.to_json() + "\n")
        print(report.to_json() if args.format == "json" else "\n".join(report.summary_lines()))
        return 0
    except (OSError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
