"""Trigger ``/import file-name=...`` on the router for a remote .rsc.

Wraps a single RouterOS ``/import`` command. The file must already exist
on the router's flash (use :func:`mtctl.deployer.upload` first if it
doesn't). RouterOS executes each line of the script in order; failures
are typically reported on **stdout** as ``failure: ...`` rather than via
a non-zero exit status.

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


class ImportError(Exception):  # noqa: A001 -- shadows builtin intentionally
    """Raised when /import fails on the router."""


def run_import(
    remote_path: str,
    settings: Settings,
    *,
    verbose: bool = True,
    dry_run: bool = False,
    validate: bool = False,
) -> str:
    """Run ``/import file-name=<remote_path>`` on the router.

    *remote_path* is a POSIX path relative to flash root (the router
    resolves it the same way ``/file print`` does). ``verbose=True`` adds
    ``verbose=yes`` so the router echoes each script line -- helpful for
    diagnosing mid-script failures.

    *dry_run* prints the command that *would* run without opening an SSH
    connection, mirroring the convention in :func:`mtctl.deployer.upload`
    and :func:`mtctl.backup.create_backup`.

    *validate* opens the SSH connection and probes the file (verifies
    it exists on flash; if small enough, also ``:parse``s its contents
    to catch syntax errors) but DOES NOT run ``/import``. Use this in
    `deploy.ps1 -DryRun` to confirm the router accepts the upload
    before committing to the apply. Mutually exclusive with *dry_run*
    (which short-circuits before SSH).

    Returns the combined stdout+stderr captured from the router (useful
    for surfacing the line-by-line ``verbose=yes`` output). Under
    *dry_run* / *validate*, returns the validation summary instead.

    RouterOS surfaces most script errors as ``failure: <message>`` on
    stdout; both that and any non-zero exit status raise
    :class:`ImportError`.
    """
    if not remote_path:
        raise ImportError("remote_path must not be empty")
    if dry_run and validate:
        raise ImportError(
            "--dry-run and --validate are mutually exclusive "
            "(--dry-run is no-SSH; --validate requires SSH)"
        )

    command = _import_command(remote_path, verbose=verbose)

    log.info(
        "import: file=%s host=%s verbose=%s dry_run=%s validate=%s",
        remote_path, settings.host, verbose, dry_run, validate,
    )

    if dry_run:
        log.info("DRY RUN: would run: %s", command)
        return ""

    if validate:
        return _validate_remote_script(remote_path, settings)

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


# Threshold (bytes) below which we also try a `:parse` round-trip on the
# router to catch syntax errors. RouterOS's :parse takes its source as a
# command-line string, so the whole script has to fit in one console
# command -- the limit varies by RouterOS version but ~3 KB is the
# safe sweet spot. For larger scripts (our typical 16-22 KB bundles)
# we only validate file existence + size; syntax errors would surface
# at /import time during the real apply.
_PARSE_PROBE_LIMIT = 3500


def _validate_remote_script(remote_path: str, settings: Settings) -> str:
    """Probe *remote_path* on the router; raise :class:`ImportError` on issue.

    Validation chain:

    1. SSH connect (catches auth / network problems).
    2. ``/file print where name=<path>`` -- confirms the file is on flash
       and surfaces its size. Missing file -> ImportError.
    3. If size < ~3 KB: ``:parse [/file get name=<path> contents]`` --
       lets the router's own parser verify syntax. For our typical
       bundle sizes this branch rarely fires; transport validation
       (steps 1+2) is the main value.

    Returns a one-line summary suitable for logging / stdout. The
    caller logs it via :func:`log.info`; the CLI also echoes it on
    stdout so chained scripts can capture it.
    """
    if '"' in remote_path:
        raise ImportError('remote_path may not contain double quotes')

    with SshSession(settings) as ssh:
        # Step 2: file metadata. `count-only` would give just "N" but we
        # also want the size, so use a structured query.
        check_cmd = f':put [/file find where name="{remote_path}"]'
        status, out, err = ssh.exec(check_cmd)
        if status != 0:
            raise ImportError(
                f"file probe failed (exit {status}): "
                f"{(out + err).strip() or '<no output>'}"
            )
        # /file find returns an internal id like "*1" if the file exists,
        # an empty string if not. (`:put` writes the value followed by
        # a newline.)
        if not out.strip():
            raise ImportError(
                f"file not found on router: {remote_path}"
            )

        # Get the file size for the summary line.
        size_cmd = f':put [/file get [/file find where name="{remote_path}"] size]'
        status, size_out, size_err = ssh.exec(size_cmd)
        if status != 0:
            raise ImportError(
                f"file size probe failed: {(size_out + size_err).strip()}"
            )
        try:
            size = int(size_out.strip())
        except ValueError:
            size = -1

        summary = f"validated: {remote_path} ({size} bytes on flash)"

        # Step 3: optional syntax probe via :parse (small files only).
        if 0 < size < _PARSE_PROBE_LIMIT:
            parse_cmd = (
                ':do {'
                f' :parse [/file get [/file find where name="{remote_path}"] contents];'
                ' :put "parse-ok";'
                '} on-error={'
                ' :put "parse-failed";'
                '}'
            )
            status, parse_out, parse_err = ssh.exec(parse_cmd)
            if status == 0 and "parse-ok" in parse_out:
                summary += "; :parse OK"
            elif status == 0 and "parse-failed" in parse_out:
                raise ImportError(
                    f"{remote_path}: :parse failed on the router "
                    "(syntax error in the script)"
                )
            # Else: probe itself failed (uncommon); silently skip --
            # the file-exists check still passed, which is the main
            # transport validation.
        else:
            summary += "; :parse skipped (file too large for command-line probe)"

        log.info(summary)
        return summary
