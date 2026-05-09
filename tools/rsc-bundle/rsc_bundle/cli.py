"""Command-line entry point for rsc-bundle."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .bundler import BundleError, bundle_file


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="rsc-bundle",
        description="Inline /import directives in a RouterOS .rsc into one file.",
    )
    parser.add_argument("entry", type=Path, help="entry .rsc file")
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="source root to walk for imports (defaults to entry's parent dir)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="write bundled .rsc to this file instead of stdout",
    )

    args = parser.parse_args(argv)

    if not args.entry.is_file():
        print(f"rsc-bundle: entry not found: {args.entry}", file=sys.stderr)
        return 2

    try:
        out = bundle_file(args.entry, root=args.root)
    except BundleError as exc:
        print(f"rsc-bundle: {exc}", file=sys.stderr)
        return 1
    except (FileNotFoundError, NotADirectoryError, ValueError) as exc:
        print(f"rsc-bundle: {exc}", file=sys.stderr)
        return 2

    if args.output:
        args.output.write_text(out, encoding="utf-8")
    else:
        sys.stdout.write(out)

    return 0
