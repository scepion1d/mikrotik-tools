"""Deploy entry points: single-file copy in either direction.

Two operations, symmetric in signature and behaviour:

- :func:`upload`   -- copy a local file to a remote path on the router.
- :func:`download` -- copy a remote file from the router to a local path.

In both directions the destination is **mandatory**, **created if its
parent directory does not exist**, and **overwritten** if it already
exists. Multi-file / directory walking is intentionally out of scope:
the caller composes the loop.

Pipeline (per call)
-------------------
1. Validate inputs (``src`` exists / ``dst`` non-empty).
2. Pre-create the destination's parent directory
   (:meth:`SftpClient.ensure_dir` for upload, ``Path.mkdir`` for
   download).
3. Open one :class:`SshSession` + :class:`SftpClient`; perform the
   transfer (:meth:`SftpClient.put` or :meth:`SftpClient.get`).

Public API
----------
- :func:`upload`     -- entry point for local -> remote.
- :func:`download`   -- entry point for remote -> local.
- :class:`DeployError` -- raised for orchestrator input/validation
  errors. Lower-level transport errors surface as
  :class:`mtctl.ssh.SshError` or
  :class:`mtctl.sftp.SftpError`.
"""

from __future__ import annotations

import logging
from pathlib import Path, PurePosixPath

from .config import Settings
from .ssh import SshSession


log = logging.getLogger("mtctl")


class DeployError(Exception):
    """Raised on deploy-orchestrator input/validation failures."""


def upload(
    src: str | Path,
    dst: str,
    settings: Settings,
    *,
    dry_run: bool = False,
) -> None:
    """Upload local *src* file to remote *dst* path on the router.

    *dst* is a POSIX-style path relative to flash root; missing parent
    directories are created automatically. An existing remote file at
    *dst* is overwritten.
    """
    src_path = _validate_local_src(src)
    remote = _normalize_remote(dst)
    parent = _remote_parent(remote)

    log.info(
        "upload: src=%s dst=%s host=%s dry_run=%s",
        src_path, remote, settings.host, dry_run,
    )

    if dry_run:
        if parent:
            log.info("DRY RUN: would ensure remote directory: %s", parent)
        log.info(
            "DRY RUN: would upload %s -> %s  (%d bytes)",
            src_path.name, remote, src_path.stat().st_size,
        )
        return

    with SshSession(settings) as ssh, ssh.open_sftp() as sftp:
        if parent:
            sftp.ensure_dir(parent)
        sftp.put(src_path, remote)
        log.info(
            "  + %s -> %s  (%d bytes)",
            src_path.name, remote, src_path.stat().st_size,
        )


def download(
    src: str,
    dst: str | Path,
    settings: Settings,
    *,
    dry_run: bool = False,
) -> None:
    """Download remote *src* file from the router to local *dst* path.

    *src* is a POSIX-style path relative to flash root. *dst* may include
    parent directories that don't yet exist locally; they are created.
    An existing local file at *dst* is overwritten.
    """
    remote = _normalize_remote(src)
    dst_path = _validate_local_dst(dst)

    log.info(
        "download: src=%s dst=%s host=%s dry_run=%s",
        remote, dst_path, settings.host, dry_run,
    )

    if dry_run:
        if dst_path.parent != Path("") and not dst_path.parent.exists():
            log.info("DRY RUN: would create local directory: %s", dst_path.parent)
        log.info("DRY RUN: would download %s -> %s", remote, dst_path)
        return

    dst_path.parent.mkdir(parents=True, exist_ok=True)

    with SshSession(settings) as ssh, ssh.open_sftp() as sftp:
        size = sftp.stat_size(remote)
        sftp.get(remote, dst_path)
        log.info(
            "  + %s -> %s  (%d bytes)",
            remote, dst_path, size,
        )


# --- internals --------------------------------------------------------------


def _validate_local_src(src: str | Path) -> Path:
    """Resolve and validate a local source file path."""
    p = Path(src)
    if not p.exists():
        raise DeployError(f"source path not found: {p}")
    if not p.is_file():
        raise DeployError(f"source is not a regular file: {p}")
    return p


def _validate_local_dst(dst: str | Path) -> Path:
    """Validate a local destination path. Empty rejected; existing file OK (overwrite)."""
    if not str(dst):
        raise DeployError("destination path is empty")
    p = Path(dst)
    if p.exists() and p.is_dir():
        raise DeployError(f"destination is a directory, not a file: {p}")
    return p


def _normalize_remote(name: str) -> str:
    """Normalize a user-supplied remote path: backslash -> slash, strip leading /."""
    if not name:
        raise DeployError("remote path is empty")
    n = name.replace("\\", "/").lstrip("/")
    if not n or n.endswith("/"):
        raise DeployError(f"remote path must include a filename: {name!r}")
    return n


def _remote_parent(remote_name: str) -> str:
    """Return the POSIX parent directory of *remote_name*, or '' if at root."""
    parent = PurePosixPath(remote_name).parent
    return "" if str(parent) in (".", "/") else str(parent)
