"""Tests for ``rsc_deploy.ssh.SshSession``.

We patch ``paramiko.SSHClient`` with a small fake; no network I/O.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import paramiko  # noqa: E402
import pytest  # noqa: E402

from rsc_deploy import ssh as ssh_mod  # noqa: E402
from rsc_deploy.config import Settings  # noqa: E402
from rsc_deploy.sftp import SftpClient  # noqa: E402
from rsc_deploy.ssh import SshError, SshSession  # noqa: E402


# --- fake paramiko.SSHClient -----------------------------------------------


class FakeSshClient:
    """Records connect/close/policy calls; can fail on demand."""

    instances: list["FakeSshClient"] = []

    def __init__(self) -> None:
        self.connected = False
        self.closed = False
        self.policy: Any | None = None
        self.connect_kwargs: dict | None = None
        self.fail_connect: Exception | None = None
        self.fail_open_sftp: Exception | None = None
        self.sftp_returned: Any = object()  # sentinel
        FakeSshClient.instances.append(self)

    def set_missing_host_key_policy(self, policy: Any) -> None:
        self.policy = policy

    def connect(self, **kwargs: Any) -> None:
        if self.fail_connect:
            raise self.fail_connect
        self.connected = True
        self.connect_kwargs = kwargs

    def open_sftp(self) -> Any:
        if self.fail_open_sftp:
            raise self.fail_open_sftp
        return self.sftp_returned

    def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def patch_ssh_client(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeSshClient.instances.clear()
    monkeypatch.setattr(ssh_mod.paramiko, "SSHClient", FakeSshClient)


def _settings(**overrides: Any) -> Settings:
    base = dict(host="10.0.0.1", user="admin", password="pw", port=22, timeout=10.0)
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


# --- connect / close lifecycle ---------------------------------------------


def test_connect_calls_paramiko_with_settings() -> None:
    sess = SshSession(_settings(host="1.2.3.4", port=2222, timeout=5.0))
    sess.connect()
    fake = FakeSshClient.instances[-1]
    assert fake.connected is True
    assert fake.connect_kwargs is not None
    assert fake.connect_kwargs["hostname"] == "1.2.3.4"
    assert fake.connect_kwargs["port"] == 2222
    assert fake.connect_kwargs["username"] == "admin"
    assert fake.connect_kwargs["password"] == "pw"
    assert fake.connect_kwargs["timeout"] == 5.0
    # Hardening posture: no key auth or agent (TOFU host keys).
    assert fake.connect_kwargs["look_for_keys"] is False
    assert fake.connect_kwargs["allow_agent"] is False
    assert isinstance(fake.policy, paramiko.AutoAddPolicy)


def test_connect_is_idempotent() -> None:
    sess = SshSession(_settings())
    sess.connect()
    sess.connect()
    # Second call does NOT instantiate a new client.
    assert len(FakeSshClient.instances) == 1


def test_connect_wraps_paramiko_failure() -> None:
    sess = SshSession(_settings())

    def boom() -> None:  # noqa: ANN001 -- closure
        raise paramiko.SSHException("banner timeout")

    # Patch the next-instantiated client to fail.
    original_init = FakeSshClient.__init__

    def init(self: FakeSshClient) -> None:
        original_init(self)
        self.fail_connect = paramiko.SSHException("banner timeout")

    FakeSshClient.__init__ = init  # type: ignore[method-assign]
    try:
        with pytest.raises(SshError, match="ssh connect failed"):
            sess.connect()
    finally:
        FakeSshClient.__init__ = original_init  # type: ignore[method-assign]


def test_connect_wraps_oserror() -> None:
    """OSError (e.g. socket refused) is also wrapped."""
    sess = SshSession(_settings())
    original_init = FakeSshClient.__init__

    def init(self: FakeSshClient) -> None:
        original_init(self)
        self.fail_connect = OSError("connection refused")

    FakeSshClient.__init__ = init  # type: ignore[method-assign]
    try:
        with pytest.raises(SshError, match="ssh connect failed"):
            sess.connect()
    finally:
        FakeSshClient.__init__ = original_init  # type: ignore[method-assign]


def test_close_is_idempotent() -> None:
    sess = SshSession(_settings())
    sess.connect()
    sess.close()
    sess.close()  # second close is a no-op, doesn't raise
    fake = FakeSshClient.instances[-1]
    assert fake.closed is True


def test_close_without_connect_is_noop() -> None:
    SshSession(_settings()).close()
    assert FakeSshClient.instances == []  # nothing was created


# --- context manager --------------------------------------------------------


def test_context_manager_connects_and_closes() -> None:
    with SshSession(_settings()):
        fake = FakeSshClient.instances[-1]
        assert fake.connected is True
        assert fake.closed is False
    assert fake.closed is True


def test_context_manager_closes_on_exception() -> None:
    with pytest.raises(RuntimeError):
        with SshSession(_settings()):
            raise RuntimeError("boom")
    fake = FakeSshClient.instances[-1]
    assert fake.closed is True


# --- open_sftp --------------------------------------------------------------


def test_open_sftp_returns_wrapped_client() -> None:
    sess = SshSession(_settings())
    sess.connect()
    sftp = sess.open_sftp()
    assert isinstance(sftp, SftpClient)


def test_open_sftp_requires_active_connection() -> None:
    with pytest.raises(SshError, match="not connected"):
        SshSession(_settings()).open_sftp()


def test_open_sftp_wraps_failure() -> None:
    sess = SshSession(_settings())
    sess.connect()
    FakeSshClient.instances[-1].fail_open_sftp = paramiko.SSHException("no channel")
    with pytest.raises(SshError, match="sftp open failed"):
        sess.open_sftp()
