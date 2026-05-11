"""Command-line entry point for rsc-ctl.

Three subcommands::

    rsc-ctl upload   --src LOCAL  --dst REMOTE  [--env ENV] [--dry-run] [-v]
    rsc-ctl download --src REMOTE --dst LOCAL   [--env ENV] [--dry-run] [-v]
    rsc-ctl backup   [--password PW | --no-encrypt] [--env ENV] [--dry-run] [-v]

``upload`` and ``download`` require ``--src`` and ``--dst``. The
destination's parent directory is created if missing; an existing file
at the destination is overwritten. ``backup`` triggers a router-side
snapshot under ``backups/<timestamp>/`` and prints the folder path on
stdout.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .backup import BackupError, create_backup
from .config import EnvError, load_env
from .deployer import DeployError, download, upload
from .sftp import SftpError
from .ssh import SshError


def _find_repo_root(start: Path) -> Path:
    """Walk up from *start* looking for an ``.env`` (or repo marker)."""
    for d in (start, *start.parents):
        if (d / ".env").is_file() or (d / ".gitignore").is_file():
            return d
    return start


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)

    if not args.command:
        # argparse with required=False on subparsers prints nothing on
        # bare invocation; make the failure visible.
        print("rsc-ctl: missing subcommand (upload|download); use -h", file=sys.stderr)
        return 2

    _configure_logging(args.verbose)

    env_path = args.env or (_find_repo_root(Path.cwd()) / ".env")
    try:
        settings = load_env(env_path)
    except EnvError as exc:
        print(f"rsc-ctl: {exc}", file=sys.stderr)
        return 2

    try:
        if args.command == "upload":
            upload(args.src, args.dst, settings, dry_run=args.dry_run)
        elif args.command == "download":
            download(args.src, args.dst, settings, dry_run=args.dry_run)
        elif args.command == "backup":
            password = None if args.no_encrypt else args.password
            folder = create_backup(
                settings,
                password=password,
                dry_run=args.dry_run,
            )
            # Print the folder so it's machine-readable for chained pipelines
            # (e.g. `for /f %f in ('rsc-ctl backup ...') do ...`).
            print(folder)
        else:  # pragma: no cover -- argparse keeps choices honest
            print(f"rsc-ctl: unknown subcommand: {args.command}", file=sys.stderr)
            return 2
    except (DeployError, BackupError, SshError, SftpError) as exc:
        print(f"rsc-ctl: {exc}", file=sys.stderr)
        return 1

    return 0


# --- internals --------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rsc-ctl",
        description=(
            "SSH/SFTP control plane for a RouterOS device. Subcommands: "
            "upload (local -> router), download (router -> local), backup "
            "(trigger a router-side snapshot under backups/<timestamp>/)."
        ),
    )
    sub = parser.add_subparsers(dest="command", metavar="{upload,download,backup}")

    up = sub.add_parser(
        "upload",
        help="copy a local file to a remote path on the router",
        description=(
            "Upload one local file to the router via SFTP. Missing remote "
            "directories under --dst are created. An existing remote file "
            "at --dst is overwritten."
        ),
    )
    up.add_argument("--src", type=Path, required=True, help="local source file path")
    up.add_argument(
        "--dst",
        required=True,
        help="remote destination path (POSIX, relative to flash root)",
    )
    _add_common_flags(up)

    down = sub.add_parser(
        "download",
        help="copy a remote file from the router to a local path",
        description=(
            "Download one remote file from the router via SFTP. Missing "
            "local directories under --dst are created. An existing local "
            "file at --dst is overwritten."
        ),
    )
    down.add_argument(
        "--src",
        required=True,
        help="remote source path (POSIX, relative to flash root)",
    )
    down.add_argument("--dst", type=Path, required=True, help="local destination file path")
    _add_common_flags(down)

    bk = sub.add_parser(
        "backup",
        help="trigger a router-side backup snapshot under backups/<timestamp>/",
        description=(
            "Run /system/backup save and /export show-sensitive on the "
            "router. Both files land in a fresh backups/<timestamp>/ "
            "folder on flash (live.backup + live.rsc). Prints the folder "
            "path on stdout."
        ),
    )
    enc = bk.add_mutually_exclusive_group()
    enc.add_argument(
        "--password",
        default=None,
        help="encrypt the .backup file with this password (off by default)",
    )
    enc.add_argument(
        "--no-encrypt",
        action="store_true",
        help="explicitly request an unencrypted .backup (the default already)",
    )
    _add_common_flags(bk)

    return parser


def _add_common_flags(p: argparse.ArgumentParser) -> None:
    """Flags shared by every subcommand."""
    p.add_argument(
        "--env",
        type=Path,
        default=None,
        help="path to .env file (default: walk up from cwd looking for .env)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would happen without touching the router",
    )
    p.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="-v INFO logs (default WARNING); -vv DEBUG",
    )


def _configure_logging(verbose: int) -> None:
    """Mirror sibling tools: package logger defaults to INFO; -v adds debug."""
    level = logging.WARNING - 10 * verbose
    logging.basicConfig(
        level=max(level, logging.DEBUG),
        format="%(message)s",
        stream=sys.stderr,
    )
    # Default to INFO for our package even without -v -- one-liner
    # transfers benefit from progress feedback.
    logging.getLogger("rsc_ctl").setLevel(
        logging.INFO if verbose == 0 else level
    )
