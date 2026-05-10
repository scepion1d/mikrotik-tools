"""Command-line entry point for rsc-bundle.

Bundles a flat RouterOS profile folder into a single deploy-ready ``.rsc``.

A *profile* is a complete, named router configuration variant for the
same physical device (e.g. ``basic`` vs ``segmented``). One profile
folder = one apply-able config.

Usage::

    rsc-bundle --profile <folder> [-o OUT] [--keep-comments] [--no-flatten]

Pipeline (defaults)
-------------------
1. :func:`rsc_bundle.loader.load_profile` -- enumerate ``*.rsc`` in the
   profile folder, load ``secrets.rsc`` + ``vars.rsc`` first so their
   ``:global`` assignments are visible to all later modules.
2. :func:`rsc_bundle.flatten.flatten` -- substitute every ``$var`` reference
   with its literal value, strip RouterOS scripting wrappers, normalise
   property quoting.
3. :func:`rsc_parser.parse_text` -- parse the cleaned text into a
   :class:`~rsc_parser.Config`.
4. :func:`rsc_bundle.compact.emit` -- render one line per operation,
   minify ``comment=`` properties to bare ``iac.id`` tokens (drop the
   human-readable suffix; drop comments without an iac token).

The result is the smallest faithful ``.rsc`` we can ship while preserving
the identity tokens that ``rsc-diff`` needs to match items between
snapshots.

Output path
-----------
- ``-o <file>``  (extension makes it a file path)
- ``-o <dir>``   write to ``<dir>/<profile>-<yymmdd>-<secs>.rsc``
- omitted        write to ``./out/<profile>-<yymmdd>-<secs>.rsc``

Escape hatches
--------------
- ``--keep-comments`` -- preserve the original ``comment=`` text verbatim
  (still bare-quoted via the standard /export-style normaliser).
- ``--no-flatten``    -- skip flatten + parse + compact entirely; emit the
  raw concatenated source. Useful for debugging the loader and for
  bundles that must keep ``$var`` references for runtime substitution.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from rsc_parser import parse_text

from .compact import emit as compact_emit
from .flatten import flatten
from .loader import LoaderError, concat, load_profile


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="rsc-bundle",
        description=(
            "Bundle a flat RouterOS .rsc profile folder into one minimal "
            "deploy-ready file. By default, $var references are substituted, "
            "scripting wrappers are stripped, and `comment=` properties are "
            "minified to their iac.id tokens."
        ),
    )
    parser.add_argument(
        "--profile",
        type=Path,
        required=True,
        help=(
            "profile folder containing the .rsc modules (a named router "
            "configuration variant, e.g. rsc/basic or rsc/segmented). "
            "Loaded order: secrets.rsc, vars.rsc, then every other *.rsc "
            "alphabetically."
        ),
    )
    parser.add_argument(
        "-o", "--out",
        type=Path,
        default=None,
        help=(
            "output path. If a directory (or omitted -> ./out/), the "
            "filename is auto-generated as <profile>-<yymmdd>-<secs>.rsc. "
            "If a file path, used as-is."
        ),
    )
    parser.add_argument(
        "--keep-comments",
        action="store_true",
        help=(
            "preserve the full `comment=\"...\"` text verbatim instead of "
            "minifying to the bare iac.id token. Useful when you want the "
            "bundle to read like the source."
        ),
    )
    parser.add_argument(
        "--no-flatten",
        action="store_true",
        help=(
            "skip flatten + parse + compact. Emit the raw concatenated "
            "source (with file-banner comments). RouterOS will resolve "
            "`:global $vars` at /import time. Useful for debugging the "
            "loader or for two-stage deploys."
        ),
    )

    args = parser.parse_args(argv)

    profile: Path = args.profile
    if not profile.is_dir():
        print(f"rsc-bundle: profile folder not found: {profile}", file=sys.stderr)
        return 2

    try:
        files = load_profile(profile)
    except LoaderError as exc:
        print(f"rsc-bundle: {exc}", file=sys.stderr)
        return 2

    raw = concat(files)

    if args.no_flatten:
        # Bypass flatten/compact entirely. The raw concat is what gets
        # written; the operator (or RouterOS at import time) handles
        # variables and scripting.
        text = raw
    else:
        # Default pipeline: substitute vars + strip scripting + parse +
        # re-emit one-line-per-op.
        flat = flatten(raw)
        cfg = parse_text(flat)
        text = compact_emit(cfg, minify_comments=not args.keep_comments)

    out_path = _resolve_out_path(args.out, profile)
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(
            f"rsc-bundle: cannot create output dir {out_path.parent}: {exc}",
            file=sys.stderr,
        )
        return 2

    out_path.write_text(text, encoding="utf-8")
    print(out_path)
    return 0


def _resolve_out_path(out: Path | None, profile: Path) -> Path:
    """Decide where to write the bundle.

    Rules
    -----
    - ``out is None``                 -> ``./out/<profile>-<stamp>.rsc``
    - ``out`` exists as a directory   -> ``<out>/<profile>-<stamp>.rsc``
    - ``out.suffix`` is empty AND it
      doesn't exist                   -> treat as a directory; same as above
    - otherwise                       -> ``out`` used verbatim as a file path
    """
    profile_name = profile.resolve().name
    stamp_name = _build_output_name(profile_name)

    if out is None:
        return Path("out") / stamp_name

    if out.is_dir():
        return out / stamp_name

    if out.suffix == "" and not out.exists():
        # Bare name like `--out builds` with no existing file: treat as a
        # directory. This matches the previous CLI's --out-as-dir behaviour
        # and avoids accidentally writing a file with no extension.
        return out / stamp_name

    return out


def _build_output_name(profile_name: str) -> str:
    """``<profile>-<yymmdd>-<seconds-since-midnight>.rsc``."""
    now = datetime.now()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    secs = int((now - midnight).total_seconds())
    stamp = now.strftime("%y%m%d") + f"-{secs}"
    return f"{profile_name}-{stamp}.rsc"