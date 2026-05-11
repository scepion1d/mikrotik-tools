"""CLI smoke tests for rsc-ctl: argparse plumbing + dispatch.

The orchestrator and ``load_env`` are stubbed; the network is not touched.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from rsc_ctl import cli as cli_mod  # noqa: E402
from rsc_ctl.config import EnvError, Settings  # noqa: E402
from rsc_ctl.deployer import DeployError  # noqa: E402
from rsc_ctl.sftp import SftpError  # noqa: E402
from rsc_ctl.ssh import SshError  # noqa: E402
from rsc_ctl.backup import BackupError  # noqa: E402

from rsc_ctl.cli import main as cli_main  # noqa: E402


# --- shared fixtures --------------------------------------------------------


def _stub_settings() -> Settings:
    return Settings(host="10.0.0.1", user="admin", password="pw", port=22, timeout=10.0)


@pytest.fixture
def stub_env(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """Replace ``load_env`` with a recorder that returns a fixed Settings."""
    seen: list[Path] = []

    def fake(path: Path | str) -> Settings:
        seen.append(Path(path))
        return _stub_settings()

    monkeypatch.setattr(cli_mod, "load_env", fake)
    return seen


@pytest.fixture
def stub_upload(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    calls: list[dict] = []

    def fake(src, dst, settings, *, dry_run=False) -> None:  # noqa: ANN001
        calls.append(dict(src=src, dst=dst, settings=settings, dry_run=dry_run))

    monkeypatch.setattr(cli_mod, "upload", fake)
    return calls


@pytest.fixture
def stub_download(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    calls: list[dict] = []

    def fake(src, dst, settings, *, dry_run=False) -> None:  # noqa: ANN001
        calls.append(dict(src=src, dst=dst, settings=settings, dry_run=dry_run))

    monkeypatch.setattr(cli_mod, "download", fake)
    return calls


@pytest.fixture
def stub_backup(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    calls: list[dict] = []

    def fake(settings, *, password=None, dry_run=False) -> str:  # noqa: ANN001
        calls.append(dict(settings=settings, password=password, dry_run=dry_run))
        return "backups/STAMP"

    monkeypatch.setattr(cli_mod, "create_backup", fake)
    return calls


# --- top-level dispatch -----------------------------------------------------


def test_no_subcommand_returns_2(capsys: pytest.CaptureFixture[str]) -> None:
    rc = cli_main([])
    assert rc == 2
    err = capsys.readouterr().err
    assert "missing subcommand" in err


def test_unknown_subcommand_argparse_exits(capsys: pytest.CaptureFixture[str]) -> None:
    """argparse rejects unknown choice with its own SystemExit(2)."""
    with pytest.raises(SystemExit) as exc:
        cli_main(["frobnicate"])
    assert exc.value.code == 2


# --- upload subcommand ------------------------------------------------------


def test_upload_requires_src_and_dst(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli_main(["upload"])
    assert exc.value.code == 2  # argparse usage error


def test_upload_dispatches(
    tmp_path: Path,
    stub_env: list[Path],
    stub_upload: list[dict],
) -> None:
    src = tmp_path / "down.rsc"
    src.write_bytes(b"x")
    rc = cli_main([
        "upload",
        "--src", str(src),
        "--dst", "staged/apply.rsc",
        "--env", str(tmp_path / ".env"),
    ])
    assert rc == 0
    assert len(stub_upload) == 1
    call = stub_upload[0]
    assert call["src"] == src
    assert call["dst"] == "staged/apply.rsc"
    assert call["dry_run"] is False
    assert call["settings"] == _stub_settings()
    # --env is forwarded.
    assert stub_env == [tmp_path / ".env"]


def test_upload_dry_run_flag_propagates(
    tmp_path: Path, stub_env: list[Path], stub_upload: list[dict],
) -> None:
    cli_main([
        "upload",
        "--src", str(tmp_path / "x.rsc"),
        "--dst", "x.rsc",
        "--env", str(tmp_path / ".env"),
        "--dry-run",
    ])
    assert stub_upload[0]["dry_run"] is True


def test_upload_returns_1_on_deploy_error(
    tmp_path: Path,
    stub_env: list[Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def boom(*_args: Any, **_kw: Any) -> None:
        raise DeployError("validation failed")

    monkeypatch.setattr(cli_mod, "upload", boom)
    rc = cli_main([
        "upload",
        "--src", str(tmp_path / "x.rsc"),
        "--dst", "x.rsc",
        "--env", str(tmp_path / ".env"),
    ])
    assert rc == 1
    assert "validation failed" in capsys.readouterr().err


def test_upload_returns_1_on_ssh_error(
    tmp_path: Path,
    stub_env: list[Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def boom(*_args: Any, **_kw: Any) -> None:
        raise SshError("ssh connect failed: boom")

    monkeypatch.setattr(cli_mod, "upload", boom)
    rc = cli_main([
        "upload",
        "--src", str(tmp_path / "x.rsc"),
        "--dst", "x.rsc",
        "--env", str(tmp_path / ".env"),
    ])
    assert rc == 1
    assert "ssh connect failed" in capsys.readouterr().err


def test_upload_returns_1_on_sftp_error(
    tmp_path: Path,
    stub_env: list[Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def boom(*_args: Any, **_kw: Any) -> None:
        raise SftpError("put failed: boom")

    monkeypatch.setattr(cli_mod, "upload", boom)
    rc = cli_main([
        "upload",
        "--src", str(tmp_path / "x.rsc"),
        "--dst", "x.rsc",
        "--env", str(tmp_path / ".env"),
    ])
    assert rc == 1
    assert "put failed" in capsys.readouterr().err


# --- download subcommand ----------------------------------------------------


def test_download_requires_src_and_dst() -> None:
    with pytest.raises(SystemExit) as exc:
        cli_main(["download"])
    assert exc.value.code == 2


def test_download_dispatches(
    tmp_path: Path,
    stub_env: list[Path],
    stub_download: list[dict],
) -> None:
    rc = cli_main([
        "download",
        "--src", "staged/apply.rsc",
        "--dst", str(tmp_path / "out.rsc"),
        "--env", str(tmp_path / ".env"),
    ])
    assert rc == 0
    assert len(stub_download) == 1
    call = stub_download[0]
    assert call["src"] == "staged/apply.rsc"
    assert call["dst"] == tmp_path / "out.rsc"
    assert call["dry_run"] is False


def test_download_dry_run_flag_propagates(
    tmp_path: Path, stub_env: list[Path], stub_download: list[dict],
) -> None:
    cli_main([
        "download",
        "--src", "x.rsc",
        "--dst", str(tmp_path / "out.rsc"),
        "--env", str(tmp_path / ".env"),
        "--dry-run",
    ])
    assert stub_download[0]["dry_run"] is True


# --- env loading ------------------------------------------------------------


def test_env_default_walks_up(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stub_upload: list[dict],
) -> None:
    """Without --env, the CLI walks up from cwd looking for .env / .gitignore."""
    (tmp_path / ".env").write_text("ROUTER_HOST=h\nROUTER_USER=u\nROUTER_PASSWORD=p\n")
    src = tmp_path / "x.rsc"
    src.write_bytes(b"x")
    monkeypatch.chdir(tmp_path)
    rc = cli_main(["upload", "--src", str(src), "--dst", "x.rsc"])
    assert rc == 0
    assert stub_upload[0]["settings"].host == "h"


def test_env_load_failure_returns_2(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fake_load(_path: Path | str) -> Settings:
        raise EnvError(".env missing required key(s): ROUTER_HOST")

    monkeypatch.setattr(cli_mod, "load_env", fake_load)
    rc = cli_main([
        "upload",
        "--src", str(tmp_path / "x.rsc"),
        "--dst", "x.rsc",
        "--env", str(tmp_path / ".env"),
    ])
    assert rc == 2
    assert "ROUTER_HOST" in capsys.readouterr().err


# --- backup subcommand ------------------------------------------------------


def test_backup_dispatches_unencrypted_by_default(
    tmp_path: Path,
    stub_env: list[Path],
    stub_backup: list[dict],
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = cli_main(["backup", "--env", str(tmp_path / ".env")])
    assert rc == 0
    assert len(stub_backup) == 1
    call = stub_backup[0]
    assert call["password"] is None
    assert call["dry_run"] is False
    assert call["settings"] == _stub_settings()
    # Folder is printed on stdout for chaining.
    assert capsys.readouterr().out.strip() == "backups/STAMP"


def test_backup_password_propagates(
    tmp_path: Path, stub_env: list[Path], stub_backup: list[dict],
) -> None:
    cli_main([
        "backup",
        "--env", str(tmp_path / ".env"),
        "--password", "s3cret",
    ])
    assert stub_backup[0]["password"] == "s3cret"


def test_backup_no_encrypt_overrides_password_to_none(
    tmp_path: Path, stub_env: list[Path], stub_backup: list[dict],
) -> None:
    cli_main([
        "backup",
        "--env", str(tmp_path / ".env"),
        "--no-encrypt",
    ])
    assert stub_backup[0]["password"] is None


def test_backup_password_and_no_encrypt_mutually_exclusive(
    tmp_path: Path,
) -> None:
    with pytest.raises(SystemExit) as exc:
        cli_main([
            "backup",
            "--env", str(tmp_path / ".env"),
            "--password", "x",
            "--no-encrypt",
        ])
    assert exc.value.code == 2


def test_backup_dry_run_propagates(
    tmp_path: Path, stub_env: list[Path], stub_backup: list[dict],
) -> None:
    cli_main([
        "backup",
        "--env", str(tmp_path / ".env"),
        "--dry-run",
    ])
    assert stub_backup[0]["dry_run"] is True


def test_backup_returns_1_on_backup_error(
    tmp_path: Path,
    stub_env: list[Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def boom(*_args: Any, **_kw: Any) -> str:
        raise BackupError("backup save failed: boom")

    monkeypatch.setattr(cli_mod, "create_backup", boom)
    rc = cli_main(["backup", "--env", str(tmp_path / ".env")])
    assert rc == 1
    assert "backup save failed" in capsys.readouterr().err
