"""Stream ``/export show-sensitive`` over SSH to a local file.

Lightweight alternative to :mod:`mtctl.backup` for read-only inspection
(drift detection, ad-hoc audits): runs ``/export`` and captures stdout
without writing anything to the router's flash.

``mtctl backup`` is still the right call when you want a recoverable
snapshot (its ``live.backup`` pair is the only thing RouterOS can fully
restore via ``/system/backup load``). Use ``export`` when:

- Running on a schedule (cron / scheduled task) and don't want a
  forever-growing ``backups/<ts>/`` tree on flash.
- Comparing live vs candidate (``drift.ps1``) without altering router
  state.
- Pulling a one-off config view for a manual diff.

Security
--------
Same as :mod:`mtctl.backup`: ``show-sensitive`` includes plaintext
PSKs, admin passwords, etc. The local file written here is unprotected;
keep it on a trusted disk and treat it as secret material.

Public API
----------
- :func:`export_config` -- run ``/export`` on the router, capture
  stdout, write to a local file.
- :class:`ExportError`  -- raised on SSH-side failure.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .config import Settings
from .ssh import SshSession


log = logging.getLogger("mtctl")


class ExportError(Exception):
    """Raised when the router rejects or fails the /export command."""


def export_config(
    settings: Settings,
    dst: str | Path,
    *,
    sensitive: bool = True,
    dry_run: bool = False,
) -> Path:
    """Run ``/export [show-sensitive]`` on the router; write stdout to *dst*.

    Args:
        settings: parsed connection settings (host/user/password/...).
        dst: local file path to write. Parent directory is created if missing.
        sensitive: when True (default), use ``/export show-sensitive``.
            When False, omit the flag (PSKs, passwords come back as
            placeholders -- safe to attach to bug reports etc.).
        dry_run: log what would happen without connecting.

    Returns:
        The resolved *dst* path.

    Raises:
        ExportError: if SSH exec returns non-zero or RouterOS emits
            ``failure: ...`` on stdout.
    """
    dst_path = Path(dst)
    cmd = "/export show-sensitive" if sensitive else "/export"

    log.info(
        "export: host=%s sensitive=%s dst=%s dry_run=%s",
        settings.host, sensitive, dst_path, dry_run,
    )

    if dry_run:
        log.info("DRY RUN: would run: %s", cmd)
        log.info("DRY RUN: would write captured stdout to: %s", dst_path)
        return dst_path

    with SshSession(settings) as ssh:
        status, out, err = ssh.exec(cmd)

    combined = (err or "").strip()
    if status != 0:
        raise ExportError(
            f"export failed (exit {status}): "
            f"{combined or '<no stderr>'}"
        )
    if "failure:" in (out or "").lower()[:1024]:
        # RouterOS reports a few classes of error on stdout; sniff only
        # the first 1KB to avoid pathological-config slowdowns. Real
        # failures always surface in the first command echo.
        raise ExportError(f"export failed: {out.strip()[:500]}")

    dst_path.parent.mkdir(parents=True, exist_ok=True)
    # RouterOS may emit CRLF line endings over SSH; normalise to LF so
    # the captured file matches what `mtctl download backups/X/live.rsc`
    # would produce (the parser is tolerant of either, but the captured
    # bytes should be portable across both paths).
    text = (out or "").replace("\r\n", "\n").replace("\r", "\n")
    dst_path.write_text(text, encoding="utf-8")

    log.info("  + %s (%d bytes)", dst_path, len(text))
    return dst_path
