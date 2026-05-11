"""SFTP wrapper around :class:`paramiko.SFTPClient` with router conventions.

Owns one SFTP channel and exposes the small, opinionated set of remote
file operations the deployer needs:

- ``listdir``     -- list a remote directory (defaults to flash root).
- ``remove``      -- delete a single file.
- ``ensure_dir``  -- ``mkdir -p`` semantics on the remote.
- ``put``         -- upload one local file to a remote path (overwrites).
- ``get``         -- download one remote file to a local path (overwrites).
- ``stat_size``   -- byte size of a remote file.

Public API
----------
- :class:`SftpClient` -- context manager wrapping the SFTP channel.
- :class:`SftpError`  -- raised on any underlying SFTP/IO failure.

Path conventions
----------------
All remote paths are POSIX strings relative to flash root (``"."``).
The wrapper does NOT normalize input -- the deployer validates user-
supplied paths before they reach this layer.
"""

from __future__ import annotations

import logging
from pathlib import Path, PurePosixPath

import paramiko


log = logging.getLogger("mtctl")


class SftpError(Exception):
    """Raised on SFTP transfer or remote-FS failures."""


class SftpClient:
    """Context manager wrapping one ``paramiko.SFTPClient`` channel.

    Constructed by :meth:`mtctl.ssh.SshSession.open_sftp`. The
    channel is closed on ``__exit__`` (or via :meth:`close`); the parent
    SSH session is left alone -- that's the SshSession's responsibility.
    """

    def __init__(self, sftp: paramiko.SFTPClient) -> None:
        self._sftp = sftp

    # --- context-manager plumbing ------------------------------------------

    def __enter__(self) -> "SftpClient":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def close(self) -> None:
        """Close the SFTP channel. Idempotent (paramiko tolerates it)."""
        self._sftp.close()

    # --- remote FS ops ------------------------------------------------------

    def listdir(self, path: str = ".") -> list[str]:
        """Return entries of *path* (default flash root). Names only, unsorted."""
        try:
            return self._sftp.listdir(path)
        except IOError as exc:
            raise SftpError(f"listdir failed for {path!r}: {exc}") from exc

    def remove(self, path: str) -> None:
        """Delete the remote file at *path*."""
        try:
            self._sftp.remove(path)
        except IOError as exc:
            raise SftpError(f"remove failed for {path!r}: {exc}") from exc

    def ensure_dir(self, path: str) -> None:
        """Create *path* on the remote, recursively, idempotently.

        Walks each path segment; for each missing one issues ``mkdir``.
        Existing directories are left alone (verified via ``stat``).
        """
        cur = ""
        for part in PurePosixPath(path).parts:
            cur = part if not cur else f"{cur}/{part}"
            try:
                self._sftp.stat(cur)
            except FileNotFoundError:
                log.info("mkdir: %s", cur)
                try:
                    self._sftp.mkdir(cur)
                except IOError as exc:
                    raise SftpError(f"mkdir failed for {cur!r}: {exc}") from exc
            except IOError as exc:
                raise SftpError(f"stat failed for {cur!r}: {exc}") from exc

    def put(self, local: Path, remote: str) -> None:
        """Upload *local* to *remote*. Parent directory must already exist.

        Overwrites *remote* if it already exists (paramiko default).
        """
        try:
            self._sftp.put(str(local), remote)
        except (IOError, paramiko.SSHException) as exc:
            raise SftpError(f"put failed for {local} -> {remote}: {exc}") from exc

    def get(self, remote: str, local: Path) -> None:
        """Download *remote* to *local*. Local parent directory must already exist.

        Overwrites *local* if it already exists (paramiko default).
        """
        try:
            self._sftp.get(remote, str(local))
        except (IOError, paramiko.SSHException) as exc:
            raise SftpError(f"get failed for {remote} -> {local}: {exc}") from exc

    def stat_size(self, remote: str) -> int:
        """Return the byte size of *remote*. Raises :class:`SftpError` if missing."""
        try:
            attrs = self._sftp.stat(remote)
        except (IOError, paramiko.SSHException) as exc:
            raise SftpError(f"stat failed for {remote!r}: {exc}") from exc
        return int(attrs.st_size or 0)
