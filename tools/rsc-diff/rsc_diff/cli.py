"""Command-line entry point for rsc-diff."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .differ import diff
from .emitter import emit
from .parser import parse_file


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="rsc-diff",
        description="Diff two RouterOS .rsc configs into an apply-able patch.",
    )
    parser.add_argument("old", type=Path, help="path to baseline .rsc")
    parser.add_argument("new", type=Path, help="path to target .rsc")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="write patch to this file instead of stdout",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if any operations would be emitted (suitable for CI)",
    )

    args = parser.parse_args(argv)

    if not args.old.is_file():
        print(f"rsc-diff: old file not found: {args.old}", file=sys.stderr)
        return 2
    if not args.new.is_file():
        print(f"rsc-diff: new file not found: {args.new}", file=sys.stderr)
        return 2

    old_cfg = parse_file(args.old)
    new_cfg = parse_file(args.new)
    ops = diff(old_cfg, new_cfg)

    if args.check:
        if ops:
            print(f"rsc-diff: {len(ops)} operation(s) pending", file=sys.stderr)
            return 1
        return 0

    header = f"old: {args.old}\nnew: {args.new}"
    out = emit(ops, header=header)

    if args.output:
        args.output.write_text(out, encoding="utf-8")
    else:
        sys.stdout.write(out)

    return 0
