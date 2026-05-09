"""Command-line entry point for rsc-deploy."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .config import EnvError, load_env
from .deployer import DeployError, deploy


def _find_repo_root(start: Path) -> Path:
    """Walk up from *start* looking for an ``.env`` (or repo marker)."""
    for d in (start, *start.parents):
        if (d / ".env").is_file() or (d / ".gitignore").is_file():
            return d
    return start


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="rsc-deploy",
        description="Upload RouterOS .rsc files over SSH/SFTP.",
    )
    parser.add_argument(
        "--src",
        type=Path,
        required=True,
        help="source path (a .rsc file or a directory walked recursively)",
    )
    parser.add_argument(
        "--env",
        type=Path,
        default=None,
        help="path to .env file (default: walk up from cwd looking for .env)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would happen without touching the router",
    )
    parser.add_argument(
        "--no-clean",
        dest="clean",
        action="store_false",
        help="skip deleting existing *.rsc on flash before upload",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="-v INFO logs (default WARNING); -vv DEBUG",
    )

    args = parser.parse_args(argv)

    level = logging.WARNING - 10 * args.verbose
    logging.basicConfig(
        level=max(level, logging.DEBUG),
        format="%(message)s",
        stream=sys.stderr,
    )
    # Default to INFO for our package even without -v -- one-liner deploys
    # benefit from progress feedback. Use --quiet (future) to silence.
    logging.getLogger("rsc_deploy").setLevel(
        logging.INFO if args.verbose == 0 else level
    )

    env_path = args.env or (_find_repo_root(Path.cwd()) / ".env")

    try:
        settings = load_env(env_path)
    except EnvError as exc:
        print(f"rsc-deploy: {exc}", file=sys.stderr)
        return 2

    try:
        deploy(args.src, settings, dry_run=args.dry_run, clean=args.clean)
    except DeployError as exc:
        print(f"rsc-deploy: {exc}", file=sys.stderr)
        return 1

    return 0
