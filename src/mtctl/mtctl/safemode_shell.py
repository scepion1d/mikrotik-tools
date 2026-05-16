"""Run an interactive RouterOS shell command under ``/safe-mode``.

Why a separate module
---------------------
:class:`mtctl.ssh.SshSession` uses ``client.exec_command`` -- one
fresh channel per command, no shared state between commands. That
breaks ``/safe-mode``: the safe-mode contract is bound to the console
session that entered it, so when an ``exec_command`` channel closes,
the router instantly reverts.

This module owns the *other* paramiko primitive,
``client.invoke_shell()``, which gives us a single long-lived
interactive console -- exactly what ``/safe-mode`` expects.

Protocol summary
----------------
- Ctrl+X (0x18) toggles safe-mode in the RouterOS terminal. First
  press enters; second press commits (the "take" operation) and
  exits.
- Ctrl+D (0x04) exits safe-mode WITHOUT committing -- everything
  done since entry is undone. This is the "revert" path.
- While in safe-mode, the prompt prefixes the username with
  ``<SAFE>``: e.g. ``[admin@router] <SAFE> >``. We use that string
  as the "safe-mode is active" sentinel.
- ``/import file-name=...`` runs synchronously; we detect completion
  by waiting for the prompt to come back.
- Mid-script errors surface as ``failure: ...`` on stdout (same as
  :mod:`mtctl.importer`); a literal ``Script file loaded and
  executed successfully`` line marks success.

What this WON'T detect
----------------------
- Session drop. If our TCP socket dies mid-import, paramiko surfaces
  it as an exception (eventually). The router's 9-minute safe-mode
  auto-revert is the safety net for that case -- we just propagate
  the exception and let the caller decide whether to re-snapshot.

Public API
----------
- :func:`run_under_safe_mode` -- enter safe-mode, run one command,
  commit or revert based on the outcome.
- :class:`SafeModeError` -- raised when the shell protocol misbehaves
  (no prompt, unexpected output, timeout).
"""

from __future__ import annotations

import logging
import re
import select
import time

import paramiko


log = logging.getLogger("mtctl")


# Bytes the RouterOS terminal listens for. Documented in the MikroTik
# wiki under "Console: Keys"; verified against RouterOS 7.x in the
# field by the integration tests.
_KEY_CTRL_X = b"\x18"   # safe-mode enter / commit
_KEY_CTRL_D = b"\x04"   # safe-mode revert
_KEY_NEWLINE = b"\r"    # CR is what RouterOS expects (not LF)


# Prompt detection: RouterOS uses one of these forms, optionally
# preceded by ANSI escape sequences for cursor positioning:
#   [admin@router] >                # normal
#   [admin@router] <SAFE> >         # in safe-mode
# We're permissive about the username / hostname; the trailing `>` is
# what matters. The `<SAFE>` token is the sentinel for safe-mode
# being active.
_PROMPT_RE = re.compile(
    rb"\[(?P<user>[^@\]]+)@(?P<host>[^\]]+)\]\s*"
    rb"(?P<safe><SAFE>)?\s*>\s*$",
)

# Strip ANSI CSI sequences before pattern-matching. RouterOS sends a
# few for cursor positioning; they'd otherwise hide our markers.
_ANSI_CSI_RE = re.compile(rb"\x1b\[[\d;]*[A-Za-z]")

# Markers RouterOS emits after a successful or failed `/import`.
_IMPORT_SUCCESS_RE = re.compile(
    rb"Script file loaded and executed successfully",
)
_IMPORT_FAILURE_RE = re.compile(rb"failure:", re.IGNORECASE)


class SafeModeError(Exception):
    """Raised when the safe-mode shell protocol misbehaves.

    Distinguishable from :class:`mtctl.importer.ImportError` so the
    caller can choose how to react: an ImportError means the router
    parsed and rejected the script (so the revert path runs); a
    SafeModeError means our shell parsing went wrong (so we should
    let the channel die and rely on the 9-min auto-revert).
    """


def run_under_safe_mode(
    client: paramiko.SSHClient,
    command: str,
    *,
    enter_timeout: float = 10.0,
    command_timeout: float = 120.0,
    success_marker: re.Pattern[bytes] = _IMPORT_SUCCESS_RE,
    failure_marker: re.Pattern[bytes] = _IMPORT_FAILURE_RE,
) -> tuple[bool, str]:
    """Enter safe-mode, run *command*, commit on success / revert on failure.

    Args:
        client: an already-connected :class:`paramiko.SSHClient`.
        command: the RouterOS command to execute (typically ``/import
            file-name="..."  verbose=yes``). One line; we append CR.
        enter_timeout: how long to wait for the ``<SAFE>`` prompt
            after pressing Ctrl+X.
        command_timeout: how long to wait for *command* to finish
            (i.e. for a success/failure marker AND the next prompt).
        success_marker: regex that, when present in the command's
            output, means "commit the change" (Ctrl+X).
        failure_marker: regex that, when present, means "revert"
            (Ctrl+D). If neither marker fires before *command_timeout*,
            we revert (assume the worst).

    Returns:
        ``(success, captured_output)`` -- *success* is True iff the
        success marker was seen and the commit succeeded; *captured_output*
        is the full byte stream from the channel decoded as utf-8
        (with replacement for stray bytes).

    Raises:
        SafeModeError: shell protocol problem (no prompt, no SAFE
            prefix after Ctrl+X, etc.) -- channel is closed; the
            router's 9-minute auto-revert applies.
    """
    chan = client.invoke_shell(term="vt100", width=200, height=50)
    try:
        # Read past the login banner / initial prompt. RouterOS dumps a
        # block of banner then sits at the prompt.
        _wait_for_prompt(chan, timeout=enter_timeout, expect_safe=False)
        log.info("safe-mode: shell ready, entering safe-mode")

        # Enter safe-mode. The prompt changes to include `<SAFE>`.
        chan.send(_KEY_CTRL_X)
        _wait_for_prompt(chan, timeout=enter_timeout, expect_safe=True)
        log.info("safe-mode: active")

        # Send the actual command (CR + RouterOS executes synchronously
        # for /import; output streams until completion + new prompt).
        log.info("safe-mode: sending command: %s", command)
        chan.send(command.encode("utf-8") + _KEY_NEWLINE)

        captured = _drain_until_outcome(
            chan,
            timeout=command_timeout,
            success_re=success_marker,
            failure_re=failure_marker,
        )

        if captured.outcome == "success":
            log.info("safe-mode: command succeeded; committing (Ctrl+X)")
            chan.send(_KEY_CTRL_X)
            # Wait for the prompt to return to non-SAFE state (commit done).
            _wait_for_prompt(chan, timeout=enter_timeout, expect_safe=False)
            return (True, captured.output)
        else:
            log.warning(
                "safe-mode: command %s; reverting (Ctrl+D)",
                captured.outcome,
            )
            chan.send(_KEY_CTRL_D)
            # Best-effort wait for prompt return; don't raise if it
            # doesn't come back -- the revert is the important part.
            try:
                _wait_for_prompt(chan, timeout=enter_timeout, expect_safe=False)
            except SafeModeError:
                log.warning(
                    "safe-mode: no prompt after revert; "
                    "channel closing anyway"
                )
            return (False, captured.output)
    finally:
        try:
            chan.close()
        except Exception:  # pragma: no cover -- close failures are noise
            pass


# --- internals --------------------------------------------------------------


class _Capture:
    """Result of :func:`_drain_until_outcome`.

    ``outcome`` is one of:
        "success" -- success marker matched
        "failure" -- failure marker matched
        "timeout" -- neither marker matched before the timeout
    """

    __slots__ = ("outcome", "output")

    def __init__(self, outcome: str, output: str) -> None:
        self.outcome = outcome
        self.output = output


def _drain_until_outcome(
    chan: paramiko.Channel,
    *,
    timeout: float,
    success_re: re.Pattern[bytes],
    failure_re: re.Pattern[bytes],
) -> _Capture:
    """Read from *chan* until success_re or failure_re matches, or *timeout*.

    Streams in 4 KiB chunks; checks the markers after each read so a
    quick failure short-circuits the timeout. Returns the full
    accumulated bytes (ANSI-stripped) decoded as utf-8.
    """
    buf = bytearray()
    deadline = time.monotonic() + timeout
    outcome = "timeout"
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        rl, _, _ = select.select([chan], [], [], min(remaining, 0.5))
        if chan in rl:
            chunk = chan.recv(4096)
            if not chunk:
                # Channel closed unexpectedly -- treat as timeout, but
                # report what we have.
                break
            buf.extend(chunk)
            clean = _ANSI_CSI_RE.sub(b"", bytes(buf))
            if failure_re.search(clean):
                outcome = "failure"
                break
            if success_re.search(clean):
                outcome = "success"
                break
    clean = _ANSI_CSI_RE.sub(b"", bytes(buf))
    return _Capture(outcome, clean.decode("utf-8", errors="replace"))


def _wait_for_prompt(
    chan: paramiko.Channel,
    *,
    timeout: float,
    expect_safe: bool,
) -> None:
    """Block until the RouterOS prompt appears, with or without ``<SAFE>``.

    Raises :class:`SafeModeError` if the prompt doesn't show up in
    *timeout* seconds, or if the SAFE-expectation doesn't match.
    """
    buf = bytearray()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        rl, _, _ = select.select([chan], [], [], min(remaining, 0.5))
        if chan in rl:
            chunk = chan.recv(4096)
            if not chunk:
                raise SafeModeError(
                    "shell channel closed before prompt appeared"
                )
            buf.extend(chunk)
            clean = _ANSI_CSI_RE.sub(b"", bytes(buf))
            # Only look at the LAST line for the prompt -- earlier
            # text may contain literal `>` chars in script output.
            last_line = clean.rsplit(b"\n", 1)[-1]
            m = _PROMPT_RE.search(last_line)
            if m:
                got_safe = m.group("safe") is not None
                if got_safe != expect_safe:
                    raise SafeModeError(
                        f"prompt SAFE state mismatch: "
                        f"expected_safe={expect_safe}, got_safe={got_safe}"
                    )
                return
    raise SafeModeError(
        f"timed out after {timeout}s waiting for "
        f"{'<SAFE> ' if expect_safe else ''}prompt"
    )
