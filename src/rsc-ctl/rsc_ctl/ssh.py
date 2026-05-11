"""SSH session wrapper around :class:`paramiko.SSHClient`.

Owns the lifecycle of one SSH connection to a RouterOS device. Today the
only thing layered on top is SFTP (see :class:`rsc_ctl.sftp.SftpClient`),
but the same session is the natural place to grow ``exec_command`` (e.g.
to trigger ``/import file-name=...`` after upload) or interactive shells.

Public API
----------
- :class:`SshSession` -- context manager opening one ``paramiko.SSHClient``.
- :class:`SshError`   -- raised on connect / channel-open failures.

Security posture
----------------
Host-key verification is TOFU via :class:`paramiko.AutoAddPolicy` -- the
first connect to a host pins nothing, subsequent connects accept any key.
Production hardening (``known_hosts``) is on the roadmap; until then,
prefer trusted networks.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import paramiko

from .config import Settings


if TYPE_CHECKING:
    from .sftp import SftpClient


log = logging.getLogger("rsc_ctl")


class SshError(Exception):
    """Raised on SSH connect or channel-open failures."""


class SshSession:
    """Context manager owning one :class:`paramiko.SSHClient` connection.

    Usage::

        with SshSession(settings) as ssh:
            with ssh.open_sftp() as sftp:
                sftp.put(...)

    Connect happens on ``__enter__``; disconnect on ``__exit__`` regardless
    of exception. ``open_sftp`` may be called multiple times during one
    session, though one channel is the typical pattern.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: paramiko.SSHClient | None = None

    # --- context-manager plumbing ------------------------------------------

    def __enter__(self) -> "SshSession":
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    # --- public API ---------------------------------------------------------

    def connect(self) -> None:
        """Open the SSH connection. Idempotent: no-op if already connected."""
        if self._client is not None:
            return

        s = self._settings
        log.info("connect: %s@%s:%d", s.user, s.host, s.port)

        client = paramiko.SSHClient()
        # MVP: TOFU. See module docstring -> "Security posture".
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(
                hostname=s.host,
                port=s.port,
                username=s.user,
                password=s.password,
                timeout=s.timeout,
                look_for_keys=False,
                allow_agent=False,
            )
        except (paramiko.SSHException, OSError) as exc:
            raise SshError(f"ssh connect failed: {exc}") from exc

        self._client = client

    def close(self) -> None:
        """Close the SSH connection. Idempotent."""
        if self._client is None:
            return
        try:
            self._client.close()
        finally:
            self._client = None

    def open_sftp(self) -> "SftpClient":
        """Open an SFTP channel over this session.

        Returns a :class:`rsc_ctl.sftp.SftpClient` wrapper around
        ``paramiko.SFTPClient``. The wrapper is a context manager and
        should be closed independently of the SSH session.
        """
        # Local import keeps the ssh<->sftp module pair acyclic at import
        # time while still letting the public surface flow through here.
        from .sftp import SftpClient

        client = self._require_connected()
        try:
            sftp = client.open_sftp()
        except paramiko.SSHException as exc:
            raise SshError(f"sftp open failed: {exc}") from exc
        return SftpClient(sftp)

    def exec(
        self, command: str, *, timeout: float | None = None
    ) -> tuple[int, str, str]:
        """Execute *command* on the remote and wait for it to finish.

        Returns ``(exit_status, stdout, stderr)`` as decoded UTF-8 strings.
        ``timeout`` (seconds) caps how long we wait for completion.

        RouterOS-specific note: most failures are reported on **stdout**
        as ``failure: <message>`` rather than via a non-zero exit status
        or ``stderr``. Callers should scan the captured ``stdout`` for
        ``failure:`` markers in addition to checking the exit status.
        """
        client = self._require_connected()
        log.info("exec: %s", command)
        try:
            stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
            stdin.close()
            # Read everything before fetching the exit status -- the channel
            # is only flagged "exit_status_ready" once the remote side closes
            # its end, which happens after stdout/stderr are drained.
            out = stdout.read().decode("utf-8", errors="replace")
            err = stderr.read().decode("utf-8", errors="replace")
            status = stdout.channel.recv_exit_status()
        except (paramiko.SSHException, OSError) as exc:
            raise SshError(f"exec failed: {exc}") from exc
        return status, out, err

    # --- internals ----------------------------------------------------------

    def _require_connected(self) -> paramiko.SSHClient:
        if self._client is None:
            raise SshError("ssh session is not connected")
        return self._client
