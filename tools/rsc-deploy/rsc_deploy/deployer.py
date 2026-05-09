"""SSH/SFTP deployer for RouterOS .rsc files."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable

import paramiko

from .config import Settings


log = logging.getLogger("rsc_deploy")

RSC_SUFFIX = ".rsc"


class DeployError(Exception):
    """Raised on connection or transfer failures."""


def deploy(
    src: str | Path,
    settings: Settings,
    *,
    dry_run: bool = False,
    clean: bool = True,
) -> None:
    """Connect to *settings.host* and deploy ``.rsc`` files from *src*.

    *src* may be a single ``.rsc`` file or a directory walked recursively.
    If *clean* is True (default), every existing ``*.rsc`` on the router's
    flash root is deleted before upload (matches the flat-flash convention).
    *dry_run* prints what would happen without touching the router.
    """
    files = list(_collect_local_files(Path(src)))
    if not files:
        raise DeployError(f"no .rsc files found under {src}")

    log.info(
        "deploy: src=%s files=%d host=%s clean=%s dry_run=%s",
        src, len(files), settings.host, clean, dry_run,
    )

    if dry_run:
        _dry_run_report(files, clean=clean)
        return

    with _connect(settings) as sftp:
        if clean:
            _clean_remote(sftp)
        _upload_files(sftp, files)


# --- internals --------------------------------------------------------------


def _collect_local_files(src: Path) -> Iterable[Path]:
    """Yield .rsc files under *src* (or *src* itself if it's a single file)."""
    if not src.exists():
        raise DeployError(f"source path not found: {src}")
    if src.is_file():
        if src.suffix.lower() != RSC_SUFFIX:
            raise DeployError(f"source file is not a {RSC_SUFFIX}: {src}")
        yield src
        return
    if src.is_dir():
        seen: set[str] = set()
        for path in sorted(src.rglob(f"*{RSC_SUFFIX}")):
            if not path.is_file():
                continue
            if path.name in seen:
                raise DeployError(
                    f"duplicate basename {path.name!r} -- "
                    f"flat upload would lose one of them"
                )
            seen.add(path.name)
            yield path
        return
    raise DeployError(f"source is neither file nor directory: {src}")


def _dry_run_report(files: list[Path], *, clean: bool) -> None:
    if clean:
        log.info("DRY RUN: would delete every *.rsc on flash root")
    log.info("DRY RUN: would upload %d file(s):", len(files))
    for f in files:
        log.info("  %s  (%d bytes)", f.name, f.stat().st_size)


def _connect(settings: Settings) -> "_SFTPSession":
    """Open SSH + SFTP and return a context-manager wrapping both."""
    log.info("connect: %s@%s:%d", settings.user, settings.host, settings.port)
    client = paramiko.SSHClient()
    # MVP: TOFU. Production hardening (known_hosts) is on the roadmap.
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            hostname=settings.host,
            port=settings.port,
            username=settings.user,
            password=settings.password,
            timeout=settings.timeout,
            look_for_keys=False,
            allow_agent=False,
        )
    except (paramiko.SSHException, OSError) as exc:
        raise DeployError(f"ssh connect failed: {exc}") from exc

    try:
        sftp = client.open_sftp()
    except paramiko.SSHException as exc:
        client.close()
        raise DeployError(f"sftp open failed: {exc}") from exc

    return _SFTPSession(client, sftp)


def _clean_remote(sftp: paramiko.SFTPClient) -> None:
    """Delete every *.rsc at flash root on the router."""
    log.info("clean: listing flash root for *.rsc")
    try:
        names = sftp.listdir(".")
    except IOError as exc:
        raise DeployError(f"listdir failed: {exc}") from exc

    targets = [n for n in names if n.lower().endswith(RSC_SUFFIX)]
    if not targets:
        log.info("clean: nothing to delete")
        return

    log.info("clean: deleting %d file(s)", len(targets))
    for name in sorted(targets):
        try:
            sftp.remove(name)
        except IOError as exc:
            raise DeployError(f"delete failed for {name!r}: {exc}") from exc
        log.info("  - %s", name)


def _upload_files(sftp: paramiko.SFTPClient, files: list[Path]) -> None:
    """Upload each *.rsc to flash root using its basename."""
    log.info("upload: %d file(s)", len(files))
    for path in files:
        try:
            sftp.put(str(path), path.name)
        except (IOError, paramiko.SSHException) as exc:
            raise DeployError(f"upload failed for {path}: {exc}") from exc
        log.info("  + %s  (%d bytes)", path.name, path.stat().st_size)


class _SFTPSession:
    """Context manager that closes both the SFTP channel and the SSH client."""

    def __init__(self, client: paramiko.SSHClient, sftp: paramiko.SFTPClient):
        self._client = client
        self._sftp = sftp

    def __enter__(self) -> paramiko.SFTPClient:
        return self._sftp

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        try:
            self._sftp.close()
        finally:
            self._client.close()
