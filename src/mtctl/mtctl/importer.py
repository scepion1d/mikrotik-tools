"""Trigger ``/import file-name=...`` on the router for a remote .rsc.

Wraps a single RouterOS ``/import`` command. The file must already exist
on the router's flash (use :func:`mtctl.deployer.upload` first if it
doesn't). RouterOS executes each line of the script in order; failures
are typically reported on **stdout** as ``failure: ...`` rather than via
a non-zero exit status.

Why a separate module
---------------------
``backup`` writes to flash, ``upload``/``download`` move bytes; this is
the third leg -- *applying* a previously-uploaded script. Keeping it
distinct from ``deployer`` keeps each entry point single-purpose and
test-isolated (no mock SFTP needed here).

Public API
----------
- :func:`run_import` -- execute ``/import file-name=<remote>`` and
  return the captured router output (stdout + stderr).
- :class:`ImportError` -- raised when the router rejects the import
  (non-zero exit status, ``failure:`` on stdout, or no such file).
"""

from __future__ import annotations

import logging

from .config import Settings
from .ssh import SshSession


log = logging.getLogger("mtctl")


class ImportError(Exception):  # noqa: A001 -- intentional override
    """Raised when /import fails on the router.

    Shadows the builtin ``ImportError`` deliberately: this is the
    public exception of the :mod:`mtctl.importer` module and callers
    should catch it via ``from mtctl.importer import ImportError``.
    """


def run_import(
    remote_path: str,
    settings: Settings,
    *,
    verbose: bool = True,
    dry_run: bool = False,
) -> str:
    """Run ``/import file-name=<remote_path>`` on the router.

    *remote_path* is a POSIX path relative to flash root (the router
    resolves it the same way ``/file print`` does). ``verbose=True`` adds
    ``verbose=yes`` so the router echoes each script line -- helpful for
    diagnosing mid-script failures.

    *dry_run* prints the command that *would* run without opening an SSH
    connection, mirroring the convention in :func:`mtctl.deployer.upload`
    and :func:`mtctl.backup.create_backup`.

    Returns the combined stdout+stderr captured from the router (useful
    for surfacing the line-by-line ``verbose=yes`` output).

    RouterOS surfaces most script errors as ``failure: <message>`` on
    stdout; both that and any non-zero exit status raise
    :class:`ImportError`.
    """
    if not remote_path:
        raise ImportError("remote_path must not be empty")

    command = _import_command(remote_path, verbose=verbose)

    log.info(
        "import: file=%s host=%s verbose=%s dry_run=%s",
        remote_path, settings.host, verbose, dry_run,
    )

    if dry_run:
        log.info("DRY RUN: would run: %s", command)
        return ""

    with SshSession(settings) as ssh:
        status, out, err = ssh.exec(command)

    combined = (out + err).strip()
    if status != 0:
        raise ImportError(
            f"import failed (exit {status}): {combined or '<no output>'}"
        )
    if "failure:" in combined.lower():
        raise ImportError(f"import failed: {combined}")

    if combined:
        log.info(combined)
    log.info("  + imported %s", remote_path)
    return combined


# --- internals --------------------------------------------------------------


def _import_command(remote_path: str, *, verbose: bool) -> str:
    """Build the ``/import`` command line.

    The path is wrapped in double quotes to allow folders with special
    chars (timestamps, dashes). Embedded ``"`` is rejected up-front --
    our deploy pipeline never produces such paths and quoting them
    would obscure command-injection-style breakage.
    """
    if '"' in remote_path:
        raise ImportError('remote_path may not contain double quotes')
    suffix = " verbose=yes" if verbose else ""
    return f'/import file-name="{remote_path}"{suffix}'
