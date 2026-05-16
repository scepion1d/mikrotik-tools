"""Command-line entry point for ``rsc lint``.

Run semantic checks against a parsed config and report issues:

::

    rsc lint --profile src/segmentedx3 --yaml      # bundle YAML, then lint
    rsc lint --profile rsc/basic                   # bundle .rsc, then lint
    rsc lint --src live.rsc                        # lint a single .rsc file

Exit codes:

- ``0`` -- no issues, or only warnings.
- ``1`` -- at least one error-severity issue.
- ``2`` -- setup error (bad path, bundle failure).

Suppresses the bundler's stdout (the output `.rsc` path) so the lint
report is the only thing on stdout. Errors / progress go to stderr.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rsc.lint import Severity, format_issues, lint
from rsc.parser import parse_file, parse_text


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    # Pick the source: either a single .rsc file (--src) or a profile
    # folder (--profile, optionally --yaml). Exactly one is required;
    # argparse's mutually_exclusive_group enforces it.
    try:
        cfg_text = _load_source(args)
    except _LoadError as exc:
        print(f"rsc lint: {exc}", file=sys.stderr)
        return 2

    cfg = parse_text(cfg_text)
    if not cfg.items_by_menu:
        print(
            "rsc lint: parsed an empty config (no recognisable items); "
            "nothing to check.",
            file=sys.stderr,
        )
        return 2

    issues = lint(cfg)
    # Report on stderr -- stdout stays clean so callers can pipe
    # `rsc lint ... && echo OK`.
    if issues:
        print(format_issues(issues), file=sys.stderr)
    else:
        # Quiet success unless -v -- mirrors `make -s` style.
        if args.verbose:
            print(format_issues(issues), file=sys.stderr)

    has_error = any(i.severity is Severity.ERROR for i in issues)
    return 1 if has_error else 0


# --- argparse ----------------------------------------------------------------


class _LoadError(Exception):
    """Failed to produce a Config for linting (file missing, bundle failed)."""


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="rsc lint",
        description=(
            "Semantic lint for RouterOS configs. Checks for duplicate "
            "iac.* ids, dangling references, and orphan pool wiring. "
            "Operates on a single .rsc file or a full profile folder "
            "(YAML or .rsc); the bundle pipeline runs implicitly when "
            "linting a profile."
        ),
    )
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--src",
        type=Path,
        help=(
            "single .rsc file to lint (typically mtctl backup's "
            "live.rsc or a /export dump)"
        ),
    )
    source.add_argument(
        "--profile",
        type=Path,
        help=(
            "profile folder (rsc/<name> or src/<name>); contents are "
            "bundled in-memory before linting"
        ),
    )
    p.add_argument(
        "--yaml",
        action="store_true",
        help="when used with --profile, treat the folder as YAML sources",
    )
    p.add_argument(
        "--vars",
        type=Path,
        default=None,
        help=(
            "vars folder for bundle (only used with --profile; defaults "
            "to <profile-parent>)"
        ),
    )
    p.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="print the clean-result line even when there are no issues",
    )
    return p


def _load_source(args: argparse.Namespace) -> str:
    """Resolve --src vs --profile into raw .rsc text the parser can read.

    Raises :class:`_LoadError` on any I/O / bundle problem.
    """
    if args.src is not None:
        if not args.src.is_file():
            raise _LoadError(f"source not found: {args.src}")
        try:
            # parse_file returns a Config; for consistency with the
            # profile branch (which has to bundle to text first) just
            # read the file ourselves.
            return args.src.read_text(encoding="utf-8")
        except OSError as exc:
            raise _LoadError(f"cannot read {args.src}: {exc}") from exc

    # --profile branch: bundle in-memory.
    if not args.profile.is_dir():
        raise _LoadError(f"profile folder not found: {args.profile}")

    # Local import: keeps lint's import path light when only --src is used.
    from rsc.bundle import bundle
    try:
        return bundle(
            args.profile,
            vars_dir=args.vars,
            yaml=args.yaml,
            flatten_output=True,
        )
    except Exception as exc:  # noqa: BLE001 -- bundle layer raises many types
        raise _LoadError(f"bundle failed: {exc}") from exc


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
