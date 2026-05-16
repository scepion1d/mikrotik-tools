"""Command-line entry point for mtctl.

Six subcommands::

    mtctl upload   --src LOCAL  --dst REMOTE  [--env ENV] [--dry-run] [-v]
    mtctl download --src REMOTE --dst LOCAL   [--env ENV] [--dry-run] [-v]
    mtctl backup   [--password PW | --no-encrypt] [--env ENV] [--dry-run] [-v]
    mtctl export   --dst LOCAL  [--no-sensitive] [--env ENV] [--dry-run] [-v]
    mtctl import   --src REMOTE [--quiet|--validate] [--env ENV] [--dry-run] [-v]
    mtctl rm       --path REMOTE              [--env ENV] [--dry-run] [-v]

``upload`` and ``download`` require ``--src`` and ``--dst``. The
destination's parent directory is created if missing; an existing file
at the destination is overwritten. ``backup`` triggers a router-side
snapshot under ``backups/<timestamp>/`` and prints the folder path on
stdout. ``export`` is a lightweight read-only alternative -- it streams
``/export show-sensitive`` to a local file via SSH stdout without
touching the router's flash. ``import`` runs
``/import file-name=<src>`` on the router against a previously-uploaded
``.rsc`` script; ``--validate`` probes the file (existence, size,
optional ``:parse`` on small files) without executing it. ``rm``
deletes one remote file via SFTP (the cleanup half of the
upload/probe/cleanup chain ``deploy.ps1 -DryRun`` uses).
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .backup import BackupError, create_backup
from .config import EnvError, load_env
from .deployer import DeployError, download, upload
from .export import ExportError, export_config
from .importer import ImportError as RouterImportError, run_import
from .remove import RemoveError, remove_remote
from .sftp import SftpError
from .ssh import SshError


# Exceptions surfaced by subcommand handlers as the user-visible exit-1.
# Anything else propagates as an uncaught traceback (programmer error).
_HANDLED_ERRORS = (
    DeployError, BackupError, ExportError, RouterImportError,
    RemoveError, SshError, SftpError,
)


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
        print(
            "mtctl: missing subcommand (upload|download|backup|export|import|rm); use -h",
            file=sys.stderr,
        )
        return 2

    _configure_logging(args.verbose)

    env_path = args.env or (_find_repo_root(Path.cwd()) / ".env")
    try:
        settings = load_env(env_path)
    except EnvError as exc:
        print(f"mtctl: {exc}", file=sys.stderr)
        return 2

    try:
        return _dispatch(args, settings)
    except _HANDLED_ERRORS as exc:
        print(f"mtctl: {exc}", file=sys.stderr)
        return 1


# --- subcommand dispatch ----------------------------------------------------


def _dispatch(args: argparse.Namespace, settings) -> int:
    """Run the chosen subcommand and return the process exit code."""
    if args.command == "upload":
        upload(args.src, args.dst, settings, dry_run=args.dry_run)
        return 0

    if args.command == "download":
        download(args.src, args.dst, settings, dry_run=args.dry_run)
        return 0

    if args.command == "backup":
        password = None if args.no_encrypt else args.password
        folder = create_backup(settings, password=password, dry_run=args.dry_run)
        # Print the folder so it's machine-readable for chained pipelines
        # (e.g. `for /f %f in ('mtctl backup ...') do ...`).
        print(folder)
        return 0

    if args.command == "export":
        path = export_config(
            settings, args.dst,
            sensitive=not args.no_sensitive, dry_run=args.dry_run,
        )
        # Print the local destination so callers can pipe straight to
        # `rsc bundle`, `rsc diff`, etc.
        print(path)
        return 0

    if args.command == "import":
        run_import(
            args.src, settings,
            verbose=not args.quiet,
            dry_run=args.dry_run,
            validate=args.validate,
            safe_mode=args.safe_mode,
        )
        return 0

    if args.command == "rm":
        remove_remote(args.path, settings, dry_run=args.dry_run)
        return 0
        return 0

    # pragma: no cover -- argparse keeps choices honest
    print(f"mtctl: unknown subcommand: {args.command}", file=sys.stderr)
    return 2


# --- argparse wiring --------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mtctl",
        description=(
            "SSH/SFTP control plane for a RouterOS device. Subcommands: "
            "upload (local -> router), download (router -> local), backup "
            "(trigger a router-side snapshot under backups/<timestamp>/), "
            "import (run /import file-name=... on the router)."
        ),
    )
    sub = parser.add_subparsers(
        dest="command",
        metavar="{upload,download,backup,export,import,rm}",
    )

    _add_upload_parser(sub)
    _add_download_parser(sub)
    _add_backup_parser(sub)
    _add_export_parser(sub)
    _add_import_parser(sub)
    _add_rm_parser(sub)

    return parser


def _add_upload_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "upload",
        help="copy a local file to a remote path on the router",
        description=(
            "Upload one local file to the router via SFTP. Missing remote "
            "directories under --dst are created. An existing remote file "
            "at --dst is overwritten."
        ),
    )
    p.add_argument("--src", type=Path, required=True, help="local source file path")
    p.add_argument(
        "--dst", required=True,
        help="remote destination path (POSIX, relative to flash root)",
    )
    _add_common_flags(p)


def _add_download_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "download",
        help="copy a remote file from the router to a local path",
        description=(
            "Download one remote file from the router via SFTP. Missing "
            "local directories under --dst are created. An existing local "
            "file at --dst is overwritten."
        ),
    )
    p.add_argument(
        "--src", required=True,
        help="remote source path (POSIX, relative to flash root)",
    )
    p.add_argument(
        "--dst", type=Path, required=True,
        help="local destination file path",
    )
    _add_common_flags(p)


def _add_backup_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "backup",
        help="trigger a router-side backup snapshot under backups/<timestamp>/",
        description=(
            "Run /system/backup save and /export show-sensitive on the "
            "router. Both files land in a fresh backups/<timestamp>/ "
            "folder on flash (live.backup + live.rsc). Prints the folder "
            "path on stdout."
        ),
    )
    enc = p.add_mutually_exclusive_group()
    enc.add_argument(
        "--password", default=None,
        help="encrypt the .backup file with this password (off by default)",
    )
    enc.add_argument(
        "--no-encrypt", action="store_true",
        help="explicitly request an unencrypted .backup (the default already)",
    )
    _add_common_flags(p)


def _add_export_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "export",
        help="stream /export to a local file (lightweight; no router-side write)",
        description=(
            "Run /export show-sensitive on the router and capture stdout "
            "into a local file via SSH exec. Unlike `mtctl backup`, this "
            "writes nothing to the router's flash -- suitable for cron / "
            "scheduled drift checks. Prints the local path on stdout."
        ),
    )
    p.add_argument(
        "--dst", type=Path, required=True,
        help="local destination file path (typically .rsc)",
    )
    p.add_argument(
        "--no-sensitive", action="store_true",
        help=(
            "omit `show-sensitive` so PSKs / passwords come back as "
            "placeholders (safe to attach to bug reports etc.)"
        ),
    )
    _add_common_flags(p)


def _add_import_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "import",
        help="run /import file-name=<src> on the router",
        description=(
            "Execute /import file-name=<src> on the router against a "
            "previously-uploaded .rsc script. The file must already exist "
            "on flash; use `mtctl upload` first if it doesn't. RouterOS "
            "reports script errors on stdout as `failure: ...`; any such "
            "line (or a non-zero exit status) makes this command exit 1."
        ),
    )
    p.add_argument(
        "--src", required=True,
        help="remote .rsc path (POSIX, relative to flash root)",
    )
    p.add_argument(
        "--quiet", action="store_true",
        help="omit verbose=yes (router won't echo each script line)",
    )
    p.add_argument(
        "--validate", action="store_true",
        help=(
            "probe the file on the router (exists / size; :parse for "
            "small files) without running /import. Mutually exclusive "
            "with --dry-run; intended for `deploy.ps1 -DryRun`."
        ),
    )
    p.add_argument(
        "--safe-mode", action="store_true", dest="safe_mode",
        help=(
            "wrap the /import in RouterOS /safe-mode: commit on success, "
            "revert on failure, and 9-minute auto-revert if the SSH "
            "session drops mid-script. Uses an interactive shell. "
            "Mutually exclusive with --dry-run and --validate."
        ),
    )
    _add_common_flags(p)


def _add_rm_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "rm",
        help="delete a file on the router (SFTP unlink)",
        description=(
            "Delete a single remote file via SFTP. Used by "
            "`deploy.ps1 -DryRun` to clean up the probe upload after "
            "`mtctl import --validate`. Not recursive; for that, drop "
            "into SSH and use /file remove."
        ),
    )
    p.add_argument(
        "--path", required=True,
        help="remote file path (POSIX, relative to flash root)",
    )
    _add_common_flags(p)


def _add_common_flags(p: argparse.ArgumentParser) -> None:
    """Flags shared by every subcommand."""
    p.add_argument(
        "--env", type=Path, default=None,
        help="path to .env file (default: walk up from cwd looking for .env)",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="report what would happen without touching the router",
    )
    p.add_argument(
        "-v", "--verbose", action="count", default=0,
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
    logging.getLogger("mtctl").setLevel(
        logging.INFO if verbose == 0 else level
    )
