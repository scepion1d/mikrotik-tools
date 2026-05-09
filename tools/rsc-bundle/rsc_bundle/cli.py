"""Command-line entry point for rsc-bundle."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from .bundler import BundleError, bundle_file


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="rsc-bundle",
        description="Inline /import directives in a RouterOS .rsc into one file.",
    )
    parser.add_argument(
        "--mainScript",
        type=Path,
        required=True,
        help="entry .rsc file; its parent directory is the import search root",
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="output directory (created if missing); filename is auto-generated",
    )

    args = parser.parse_args(argv)

    main_script: Path = args.mainScript
    if not main_script.is_file():
        print(f"rsc-bundle: --mainScript not found: {main_script}", file=sys.stderr)
        return 2

    out_dir: Path = args.out
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"rsc-bundle: cannot create --out dir {out_dir}: {exc}", file=sys.stderr)
        return 2
    if not out_dir.is_dir():
        print(f"rsc-bundle: --out is not a directory: {out_dir}", file=sys.stderr)
        return 2

    try:
        # Search root is the main script's parent dir -- imports inside the
        # bundle resolve relative to the main script's location.
        text = bundle_file(main_script, root=main_script.parent)
    except BundleError as exc:
        print(f"rsc-bundle: {exc}", file=sys.stderr)
        return 1
    except (FileNotFoundError, NotADirectoryError, ValueError) as exc:
        print(f"rsc-bundle: {exc}", file=sys.stderr)
        return 2

    out_path = out_dir / _build_output_name(main_script)
    out_path.write_text(text, encoding="utf-8")
    print(out_path)
    return 0


def _build_output_name(main_script: Path) -> str:
    """`<stem>-<yymmdd>-<seconds-since-midnight>.rsc`."""
    now = datetime.now()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    secs = int((now - midnight).total_seconds())
    stamp = now.strftime("%y%m%d") + f"-{secs}"
    return f"{main_script.stem}-{stamp}.rsc"
