"""Top-level CLI dispatcher: ``rsc bundle ...`` / ``rsc diff ...``.

Each subcommand is a thin pass-through to the existing per-package CLI
(:func:`rsc.bundle.cli.main`, :func:`rsc.diff.cli.main`). Both
sub-CLIs keep their own argparser and ``--help`` output -- this dispatcher
just routes ``argv[1]`` to the right entry point and prints a usage line
when no subcommand is given.

This deliberately avoids merging argparse subparsers at the top level: it
preserves the per-subcommand ``--help`` exactly, and keeps each
sub-CLI usable in isolation (e.g. directly via
``python -m rsc.bundle.cli`` if ever needed).
"""

from __future__ import annotations

import sys


# Map subcommand -> "import path : function" of the subcommand's main().
_SUBCOMMANDS = {
    "bundle":  ("rsc.bundle.cli",       "main"),
    "diff":    ("rsc.diff.cli",         "main"),
    "reverse": ("rsc.yaml.reverse_cli", "main"),
    "lint":    ("rsc.lint_cli",         "main"),
    "schema":  ("rsc.schema.cli",       "main"),
}


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])

    if not args:
        _print_usage(stream=sys.stderr)
        return 2
    if args[0] in ("-h", "--help"):
        _print_usage()
        return 0

    cmd, rest = args[0], args[1:]
    if cmd not in _SUBCOMMANDS:
        print(f"rsc: unknown subcommand: {cmd!r}", file=sys.stderr)
        _print_usage(stream=sys.stderr)
        return 2

    module_name, func_name = _SUBCOMMANDS[cmd]
    # Lazy import so a typo in `rsc bundle` doesn't pay the cost of
    # importing rsc.diff (and vice versa).
    module = __import__(module_name, fromlist=[func_name])
    return getattr(module, func_name)(rest)


def _print_usage(stream=None) -> None:
    out = stream or sys.stdout
    print(
        "usage: rsc {bundle,diff,reverse,lint,schema} ...\n"
        "\n"
        "  bundle   merge a flat RouterOS profile folder into one minimal .rsc\n"
        "  diff     diff two .rsc configs into a runnable patch\n"
        "  reverse  convert an .rsc back to YAML profile sources (src/<profile>/)\n"
        "  lint     check a .rsc or profile for duplicate ids / dangling refs\n"
        "  schema   write the bundled YAML profile JSON Schema to a file or stdout\n"
        "\n"
        "Run 'rsc <subcommand> --help' for subcommand options.",
        file=out,
    )
