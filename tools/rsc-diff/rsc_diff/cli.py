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
    parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "disable per-menu defaults + computed-property normalisation. "
            "Use this for the FIRST diff against an unfamiliar router so "
            "any defaults-table miscalibration surfaces as visible drift."
        ),
    )
    parser.add_argument(
        "--lenient",
        action="store_true",
        help=(
            "suppress asymmetric drift where one side has an explicit neutral "
            "value (no/false/none/0/0s/empty) and the other side is silent. "
            "Useful for diffing authored configs against /export output that "
            "omits default-valued props. RISK: hides real drift if the actual "
            "default is non-neutral. Prefer extending defaults.py once verified."
        ),
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
    ops = diff(old_cfg, new_cfg, strict=args.strict, lenient_defaults=args.lenient)

    if args.check:
        if ops:
            print(f"rsc-diff: {len(ops)} operation(s) pending", file=sys.stderr)
            return 1
        return 0

    header_lines = [f"old: {args.old}", f"new: {args.new}"]
    if args.strict:
        header_lines.append("strict mode: defaults + computed normalisation OFF")
    if args.lenient:
        header_lines.append("lenient mode: explicit-neutral vs missing suppressed")
    header = "\n".join(header_lines)
    out = emit(ops, header=header)

    if args.output:
        args.output.write_text(out, encoding="utf-8")
    else:
        sys.stdout.write(out)

    return 0
