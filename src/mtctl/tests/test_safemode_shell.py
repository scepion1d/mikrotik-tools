"""Tests for ``mtctl.safemode_shell.run_under_safe_mode``.

We can't talk to a real router from the test suite, but the entire
protocol is deterministic byte-stream interaction with paramiko's
Channel. So we mock the channel with a tiny scriptable fake that
returns programmed responses and records sends. That verifies:

  - the right bytes get sent in the right order (Ctrl+X, command,
    Ctrl+X / Ctrl+D, etc.)
  - prompt parsing handles both forms (with and without <SAFE>)
  - success / failure / timeout paths route to commit / revert
    correctly

What it does NOT verify: that RouterOS actually behaves the way we
expect when it sees Ctrl+X / Ctrl+D / the prompt sequence. That's
manual-verification territory (the two scripts in `out/` are for
that).
"""

from __future__ import annotations

import select
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mtctl import safemode_shell as sm_mod  # noqa: E402
from mtctl.safemode_shell import (  # noqa: E402
    SafeModeError,
    run_under_safe_mode,
)


# --- fakes -----------------------------------------------------------------


class FakeChannel:
    """In-memory paramiko.Channel replacement.

    Tests prime `recv_script` with the sequence of byte chunks the
    router would emit. Each .recv() call consumes one chunk (or
    raises if exhausted while .send() expects more).

    Sends are recorded in `sent` for assertions.
    """

    def __init__(self) -> None:
        self.recv_script: list[bytes] = []
        self.sent: bytearray = bytearray()
        self.closed = False

    def recv(self, size: int) -> bytes:
        if not self.recv_script:
            return b""  # channel closed -> EOF
        chunk = self.recv_script.pop(0)
        # Honour the requested size; chunks can be smaller.
        return chunk[:size]

    def send(self, data: bytes) -> int:
        self.sent.extend(data)
        return len(data)

    def close(self) -> None:
        self.closed = True


class FakeClient:
    """Minimal paramiko.SSHClient replacement."""

    def __init__(self, channel: FakeChannel) -> None:
        self._channel = channel
        self.invoke_shell_kwargs: dict[str, Any] = {}

    def invoke_shell(self, **kwargs: Any) -> FakeChannel:
        self.invoke_shell_kwargs = kwargs
        return self._channel


@pytest.fixture(autouse=True)
def fake_select(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock select() so it only reports the channel as readable when
    there's a scripted chunk available. When the script is exhausted,
    select() returns "not ready" -- mirroring real socket behaviour
    where reads block until data is available or the timeout fires.

    Without this, _wait_for_prompt would burn through all script chunks
    then get b"" from recv() and (correctly) treat that as channel
    closed -- which isn't what most tests want to simulate.
    """
    def _ready(rlist, wlist, xlist, timeout):  # noqa: ANN001
        # Each fake channel exposes its remaining script via
        # `.recv_script`. Report readable only when there's at least
        # one chunk queued.
        ready = [c for c in rlist if getattr(c, "recv_script", None)]
        return (ready, [], [])

    monkeypatch.setattr(select, "select", _ready)


# --- happy path -------------------------------------------------------------


PROMPT = b"[admin@router] > "
SAFE_PROMPT = b"[admin@router] <SAFE> > "
SUCCESS_LINE = b"Script file loaded and executed successfully\r\n"
FAILURE_LINE = b"failure: line 5: invalid value\r\n"


def test_happy_path_enters_runs_commits() -> None:
    """Login prompt -> Ctrl+X -> SAFE prompt -> command -> success -> Ctrl+X commits -> prompt."""
    chan = FakeChannel()
    chan.recv_script = [
        b"MikroTik banner\r\n" + PROMPT,         # initial prompt
        SAFE_PROMPT,                              # after Ctrl+X
        b"Opening script file...\r\n" + SUCCESS_LINE + SAFE_PROMPT,  # import done
        PROMPT,                                   # after commit Ctrl+X
    ]
    client = FakeClient(chan)
    success, output = run_under_safe_mode(client, "/import file-name=x.rsc")
    assert success is True
    assert "Script file loaded and executed successfully" in output
    # Bytes sent: Ctrl+X (enter), command+CR, Ctrl+X (commit).
    assert chan.sent.startswith(b"\x18")
    assert b"/import file-name=x.rsc\r" in bytes(chan.sent)
    # Two Ctrl+X presses total (enter + commit), no Ctrl+D.
    assert bytes(chan.sent).count(b"\x18") == 2
    assert b"\x04" not in bytes(chan.sent)
    assert chan.closed is True


def test_failure_in_import_output_triggers_revert() -> None:
    """`failure:` line -> Ctrl+D revert, returns success=False."""
    chan = FakeChannel()
    chan.recv_script = [
        PROMPT,
        SAFE_PROMPT,
        b"Opening script file...\r\n" + FAILURE_LINE + SAFE_PROMPT,
        PROMPT,                                   # after Ctrl+D revert
    ]
    client = FakeClient(chan)
    success, output = run_under_safe_mode(client, "/import file-name=bad.rsc")
    assert success is False
    assert "failure:" in output
    # Exactly one Ctrl+X (enter) + one Ctrl+D (revert). No commit Ctrl+X.
    assert bytes(chan.sent).count(b"\x18") == 1
    assert bytes(chan.sent).count(b"\x04") == 1
    assert chan.closed is True


def test_initial_login_uses_vt100_term() -> None:
    """invoke_shell is called with vt100 terminal type so ANSI codes
    are predictable."""
    chan = FakeChannel()
    chan.recv_script = [PROMPT, SAFE_PROMPT, SUCCESS_LINE + SAFE_PROMPT, PROMPT]
    client = FakeClient(chan)
    run_under_safe_mode(client, "/x")
    assert client.invoke_shell_kwargs.get("term") == "vt100"


def test_ansi_escape_codes_are_stripped_before_matching() -> None:
    """RouterOS sends ANSI cursor positioning sequences mixed into
    output. Markers must still match through them."""
    chan = FakeChannel()
    chan.recv_script = [
        PROMPT,
        SAFE_PROMPT,
        # ANSI codes inserted around the prompt + success line.
        b"\x1b[2K\x1b[GScript output\r\n" + SUCCESS_LINE + SAFE_PROMPT,
        PROMPT,
    ]
    client = FakeClient(chan)
    success, _ = run_under_safe_mode(client, "/x")
    assert success is True


# --- error paths ------------------------------------------------------------


def test_no_initial_prompt_raises() -> None:
    """Banner without a prompt within timeout -> SafeModeError."""
    chan = FakeChannel()
    chan.recv_script = [b"MikroTik banner with no prompt\r\n"]
    client = FakeClient(chan)
    with pytest.raises(SafeModeError, match="timed out"):
        run_under_safe_mode(
            client, "/x", enter_timeout=0.1, command_timeout=0.1,
        )


def test_safe_prompt_not_appearing_after_ctrlx_raises() -> None:
    """Ctrl+X was sent but the prompt is still non-SAFE -> protocol error."""
    chan = FakeChannel()
    chan.recv_script = [
        PROMPT,
        PROMPT,    # WRONG: should be SAFE_PROMPT after Ctrl+X
    ]
    client = FakeClient(chan)
    with pytest.raises(SafeModeError, match="SAFE state mismatch"):
        run_under_safe_mode(
            client, "/x", enter_timeout=0.1, command_timeout=0.1,
        )


def test_timeout_during_import_reverts() -> None:
    """Command runs forever, neither success nor failure marker seen
    within command_timeout -> revert (treat as failure)."""
    chan = FakeChannel()
    chan.recv_script = [
        PROMPT,
        SAFE_PROMPT,
        b"still running...\r\n",  # never emits a marker
        # After Ctrl+D the channel needs to return to a normal prompt.
        PROMPT,
    ]
    client = FakeClient(chan)
    success, _ = run_under_safe_mode(
        client, "/x", command_timeout=0.2,
    )
    assert success is False
    # Should have sent Ctrl+D, not committing Ctrl+X.
    assert bytes(chan.sent).count(b"\x04") == 1


def test_channel_closes_during_drain_returns_what_we_have() -> None:
    """Empty recv mid-drain = channel closed; treat as timeout, revert."""
    chan = FakeChannel()
    chan.recv_script = [
        PROMPT,
        SAFE_PROMPT,
        # No more chunks scripted -> .recv returns b"" -> EOF mid-drain.
        # After revert we want a clean prompt (channel.recv returns b"").
    ]
    client = FakeClient(chan)
    success, _ = run_under_safe_mode(
        client, "/x", command_timeout=0.2,
    )
    # No outcome marker fired -> revert path -> success False.
    assert success is False
    assert chan.closed is True


def test_revert_failure_to_get_prompt_is_swallowed() -> None:
    """If the prompt doesn't come back after a revert, we don't raise --
    the revert itself is what matters; the channel close is in
    finally."""
    chan = FakeChannel()
    chan.recv_script = [
        PROMPT,
        SAFE_PROMPT,
        FAILURE_LINE + SAFE_PROMPT,
        # No prompt after Ctrl+D -- the wait will time out but we
        # should still return cleanly (the revert has been sent).
    ]
    client = FakeClient(chan)
    success, _ = run_under_safe_mode(
        client, "/x", enter_timeout=0.1, command_timeout=0.3,
    )
    assert success is False
    assert chan.closed is True


# --- importer integration ---------------------------------------------------


def test_importer_safe_mode_routes_through_shell_wrapper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`run_import(..., safe_mode=True)` calls into safemode_shell."""
    from mtctl import importer as importer_mod
    from mtctl.config import Settings
    from mtctl.importer import run_import

    settings = Settings(
        host="10.0.0.1", user="admin", password="pw",
        port=22, timeout=10.0,
    )

    calls: dict[str, Any] = {}

    def fake_run_under_safe_mode(client, command, **kwargs):  # noqa: ANN001
        calls["command"] = command
        return (True, "fake success output")

    # Patch the lazy-import target inside _import_under_safe_mode.
    monkeypatch.setattr(
        sm_mod, "run_under_safe_mode", fake_run_under_safe_mode,
    )

    # Patch the SshSession context manager so .__enter__ returns
    # something with _require_connected().
    class FakeSshSession:
        def __init__(self, _settings): pass
        def __enter__(self):
            return self
        def __exit__(self, *args): return None
        def _require_connected(self):
            return object()  # placeholder paramiko client

    monkeypatch.setattr(importer_mod, "SshSession", FakeSshSession)

    out = run_import("deployment/x/up.rsc", settings, safe_mode=True)
    assert "fake success output" in out
    assert "/import" in calls["command"]
    assert "deployment/x/up.rsc" in calls["command"]


def test_importer_safe_mode_mutex_with_dry_run() -> None:
    """--safe-mode and --dry-run together raise ImportError."""
    from mtctl.config import Settings
    from mtctl.importer import ImportError, run_import

    with pytest.raises(ImportError, match="mutually exclusive"):
        run_import(
            "x.rsc",
            Settings(host="x", user="u", password="p", port=22, timeout=10.0),
            dry_run=True, safe_mode=True,
        )


def test_importer_safe_mode_mutex_with_validate() -> None:
    """--safe-mode and --validate together raise ImportError."""
    from mtctl.config import Settings
    from mtctl.importer import ImportError, run_import

    with pytest.raises(ImportError, match="mutually exclusive"):
        run_import(
            "x.rsc",
            Settings(host="x", user="u", password="p", port=22, timeout=10.0),
            validate=True, safe_mode=True,
        )
