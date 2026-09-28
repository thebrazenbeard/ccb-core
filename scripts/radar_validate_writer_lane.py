#!/usr/bin/env python3
"""Fail closed when a writer-lane push is not an allowed mailbox transition."""

from __future__ import annotations

import argparse
from pathlib import Path

from radar.writer_lanes import WriterLaneError, validate_writer_lane_commit


ROOT = Path(__file__).resolve().parents[1]
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-dir", default=".")
    parser.add_argument("--ref", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--base")
    parser.add_argument(
        "--topology",
        required=True,
        help="Path to the deployment-supplied private writer-lane topology.",
    )
    parser.add_argument(
        "--cutover",
        required=True,
        help="Path to the deployment-supplied private writer-lane cutover state.",
    )
    parser.add_argument("--bootstrap-main")
    args = parser.parse_args(argv)

    try:
        identity = validate_writer_lane_commit(
            args.repo_dir,
            args.ref,
            args.head,
            args.topology,
            base=args.base,
            cutover_path=args.cutover,
            bootstrap_main=args.bootstrap_main,
        )
    except WriterLaneError as exc:
        print(str(exc))
        return 1

    print(f"WRITER_LANE_VALID:{identity}:{args.head}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
