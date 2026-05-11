"""Trigger a router-side backup snapshot in ``backups/<timestamp>/``.

Produces two files on the router's flash, side by side, in a fresh
timestamped folder:

- ``backups/<timestamp>/live.backup`` -- binary backup
  (``/system/backup save``).
- ``backups/<timestamp>/live.rsc``    -- text export with sensitive
  values (``/export show-sensitive file=...``).

Why both
--------
The ``.backup`` file is the only thing RouterOS can fully restore in
place via ``/system/backup load`` -- it preserves things ``/export``
can't (e.g. RouterBOOT-bound state, encrypted secrets). The ``.rsc`` is
the human-readable, diff-able snapshot used by ``rsc-diff``. Producing
them together keeps the pair in lockstep.

Security
--------
``live.rsc`` is generated with ``show-sensitive`` -- it contains
plaintext PSKs, admin passwords, and any other secrets RouterOS
tracks. Treat the folder it lands in (and any later download of it)
as secret material.

Public API
----------
- :func:`create_backup` -- orchestrator entry point.
- :class:`BackupError`  -- raised when the router rejects either the
  backup-save or export command (typically reported on stdout as
  ``failure: ...``; we surface it as a wrapped exception).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from .config import Settings
from .ssh import SshSession


log = logging.getLogger("rsc_ctl")

# Stem used for both files (RouterOS appends `.backup` and `.rsc`).
_STEM = "live"
# Parent folder on flash root.
_BACKUPS_ROOT = "backups"


class BackupError(Exception):
    """Raised when the router rejects /system/backup save or /export."""


def create_backup(
    settings: Settings,
    *,
    password: str | None = None,
    dry_run: bool = False,
    timestamp: str | None = None,
) -> str:
    """Snapshot the router into ``backups/<timestamp>/``.

    *password* encrypts the binary backup; if omitted, ``dont-encrypt=yes``
    is used (the file is unencrypted on flash). The ``.rsc`` export is
    always plaintext on flash.

    *timestamp* lets callers pin the folder name (mostly for tests);
    defaults to ``YYYYMMDD-HHMMSS`` in UTC.

    Returns the remote folder path (e.g. ``"backups/20260511-123456"``).
    """
    ts = timestamp or _now_utc_stamp()
    folder = f"{_BACKUPS_ROOT}/{ts}"
    backup_name = f"{folder}/{_STEM}"  # RouterOS appends .backup
    export_name = f"{folder}/{_STEM}"  # RouterOS appends .rsc

    # Validate password BEFORE opening a connection -- avoids burning an
    # SSH handshake on input we already know we'll reject.
    backup_cmd = _backup_save_command(backup_name, password)
    export_cmd = _export_command(export_name)

    log.info(
        "backup: folder=%s host=%s encrypted=%s dry_run=%s",
        folder, settings.host, password is not None, dry_run,
    )

    if dry_run:
        log.info("DRY RUN: would ensure remote directory: %s", folder)
        log.info("DRY RUN: would run: %s", backup_cmd)
        log.info("DRY RUN: would run: %s", export_cmd)
        log.info(
            "DRY RUN: would produce: %s/live.backup, %s/live.rsc",
            folder, folder,
        )
        return folder

    with SshSession(settings) as ssh:
        with ssh.open_sftp() as sftp:
            sftp.ensure_dir(folder)

        _run(ssh, backup_cmd, what="backup save")
        _run(ssh, export_cmd, what="export")

    log.info("  + %s/live.backup", folder)
    log.info("  + %s/live.rsc", folder)
    return folder


# --- internals --------------------------------------------------------------


def _now_utc_stamp() -> str:
    """``YYYYMMDD-HHMMSS`` in UTC. Compact, sortable, filesystem-safe."""
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _backup_save_command(name: str, password: str | None) -> str:
    """Build the ``/system/backup save`` command line.

    With *password*: encrypted backup. Without: ``dont-encrypt=yes``,
    which RouterOS 7 requires when no password is given.
    """
    if password is not None:
        # Quote the password to allow specials. RouterOS strings use
        # double quotes; embedded `"` would need escaping but our
        # secrets pipeline doesn't generate them. We assert that to
        # avoid silent command-injection-style breakage.
        if '"' in password:
            raise BackupError("backup password may not contain double quotes")
        return f'/system/backup save name="{name}" password="{password}"'
    return f'/system/backup save name="{name}" dont-encrypt=yes'


def _export_command(name: str) -> str:
    """``/export show-sensitive file=<name>`` -- writes ``<name>.rsc``."""
    return f'/export show-sensitive file="{name}"'


def _run(ssh: SshSession, command: str, *, what: str) -> None:
    """Execute *command*; raise :class:`BackupError` on failure.

    RouterOS reports most errors on stdout as ``failure: <message>``;
    a non-zero exit status is also possible for syntax errors. Both
    paths land in :class:`BackupError`.
    """
    status, out, err = ssh.exec(command)
    combined = (out + err).strip()
    if status != 0:
        raise BackupError(
            f"{what} failed (exit {status}): {combined or '<no output>'}"
        )
    if "failure:" in combined.lower():
        raise BackupError(f"{what} failed: {combined}")
    if combined:
        log.debug("%s output: %s", what, combined)
