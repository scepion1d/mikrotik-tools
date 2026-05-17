"""``rsc schema`` -- write the bundled JSON Schema to a file or stdout.

Examples::

    rsc schema                              # print bundle to stdout
    rsc schema --out src/schema.json        # write bundle to that path
    rsc schema --out src/schema.json --check  # verify the file is in sync

The ``--check`` mode is intended for pre-commit / CI: it exits non-zero
if the on-disk bundle differs from what the in-package fragments would
produce, so a forgotten rebuild fails the pipeline instead of shipping
a stale schema.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import render


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="rsc schema",
        description=(
            "Bundle the in-package JSON Schema fragments (rsc/schema/*.json) "
            "into a single schema document. With no --out, prints to stdout."
        ),
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output path (default: stdout). Parent directories are created.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "Verify --out matches the in-package bundle and exit non-zero on "
            "mismatch. Requires --out. Intended for CI / pre-commit drift checks."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse(list(argv if argv is not None else sys.argv[1:]))

    try:
        text = render()
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        print(f"rsc schema: {exc}", file=sys.stderr)
        return 1

    if args.check:
        if args.out is None:
            print("rsc schema: --check requires --out PATH", file=sys.stderr)
            return 2
        try:
            existing = args.out.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"rsc schema --check: cannot read {args.out}: {exc}", file=sys.stderr)
            return 1
        if existing == text:
            print(f"OK: {args.out} is in sync with rsc.schema fragments")
            return 0
        print(
            f"MISMATCH: {args.out} differs from rsc.schema bundle.\n"
            f"  re-run `rsc schema --out {args.out}` to refresh.",
            file=sys.stderr,
        )
        return 1

    if args.out is None:
        sys.stdout.write(text)
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {args.out} ({len(text):,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
