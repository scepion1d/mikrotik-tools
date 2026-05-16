"""Command-line entry point for ``rsc reverse``.

Converts a ``.rsc`` file (typically ``mtctl backup``'s ``live.rsc``)
into YAML profile sources under ``src/<profile>/`` form. The inverse
of ``rsc bundle --yaml``.

Usage
-----
::

    rsc reverse --src live.rsc -o src/myrouter/
    rsc reverse --src live.rsc -o src/myrouter/ --overwrite

Output is one ``NN-<top-menu>.yaml`` file per top-level RouterOS menu
(``interface``, ``ip``, ``ipv6``, ``system``, ...) using the same
``NN-`` numbering as the hand-authored profiles in ``src/<profile>/``.

This bootstraps a new profile from an existing router: snapshot, reverse,
commit, then iterate. Round-trip back to ``.rsc`` via ``rsc bundle --yaml``
won't byte-match the input (the input may have RouterOS quoting / column
alignment that the converter normalises), but the resulting *parsed*
config will be equivalent -- verify with ``rsc diff --check``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rsc.parser import parse_file
from rsc.yaml.reverse import to_yaml_files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="rsc reverse",
        description=(
            "Convert a RouterOS .rsc file (typically mtctl backup's "
            "live.rsc) into YAML profile sources under src/<profile>/."
        ),
    )
    parser.add_argument(
        "--src",
        type=Path,
        required=True,
        help=(
            "input .rsc file. Usually a /export show-sensitive output, "
            "or any rsc file the parser can read."
        ),
    )
    parser.add_argument(
        "-o", "--out",
        type=Path,
        required=True,
        help=(
            "output folder. One NN-<top-menu>.yaml file is written per "
            "top-level RouterOS menu. Folder is created if missing."
        ),
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help=(
            "overwrite existing files in the output folder. Without "
            "this flag, an existing file causes the command to abort "
            "before writing anything."
        ),
    )

    args = parser.parse_args(argv)

    if not args.src.is_file():
        print(f"rsc reverse: source not found: {args.src}", file=sys.stderr)
        return 2

    try:
        cfg = parse_file(args.src)
    except OSError as exc:
        print(f"rsc reverse: cannot read {args.src}: {exc}", file=sys.stderr)
        return 2

    if not cfg.items_by_menu:
        print(
            f"rsc reverse: {args.src} parsed to an empty config "
            "(no recognisable items); nothing to write.",
            file=sys.stderr,
        )
        return 2

    try:
        written = to_yaml_files(cfg, args.out, overwrite=args.overwrite)
    except FileExistsError as exc:
        print(f"rsc reverse: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"rsc reverse: write failed: {exc}", file=sys.stderr)
        return 2

    # Print each written path, one per line, so the operator (or a
    # wrapping script) can pipe straight into `code` / `git add`.
    for path in written:
        print(path)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
