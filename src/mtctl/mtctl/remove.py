"""Delete a remote file on the router via SFTP.

Cleanup helper for the upload-probe-cleanup chain that
``deploy.ps1 -DryRun`` uses (upload to a probe path, run
``mtctl import --validate``, ``mtctl rm``). Also useful standalone
when an operator wants to scrub a stale upload from flash.

Public API
----------
- :func:`remove_remote` -- delete one file via SFTP.
- :class:`RemoveError`  -- raised on SFTP-side failures.
"""

from __future__ import annotations

import logging

from .config import Settings
from .sftp import SftpError
from .ssh import SshSession


log = logging.getLogger("mtctl")


class RemoveError(Exception):
    """Raised when the SFTP unlink fails."""


def remove_remote(
    path: str,
    settings: Settings,
    *,
    dry_run: bool = False,
) -> None:
    """Delete the file at *path* on the router.

    Args:
        path: POSIX path on the router, relative to flash root.
        settings: parsed connection settings.
        dry_run: log what would happen without connecting.

    Raises:
        RemoveError: if the SFTP layer rejects the unlink (file
            missing, permission denied, etc.).
    """
    if not path:
        raise RemoveError("path must not be empty")

    log.info("rm: path=%s host=%s dry_run=%s", path, settings.host, dry_run)

    if dry_run:
        log.info("DRY RUN: would remove: %s", path)
        return

    try:
        with SshSession(settings) as ssh:
            with ssh.open_sftp() as sftp:
                sftp.remove(path)
    except SftpError as exc:
        raise RemoveError(str(exc)) from exc

    log.info("  - removed %s", path)
