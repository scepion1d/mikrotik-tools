"""Tests for the .env parser."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from rsc_ctl.config import EnvError, Settings, load_env, parse_env_text  # noqa: E402


# --- parse_env_text ---------------------------------------------------------


def test_parse_basic() -> None:
    assert parse_env_text("A=1\nB=two\n") == {"A": "1", "B": "two"}


def test_parse_quoted_values() -> None:
    out = parse_env_text('A="hello world"\nB=\'single\'\n')
    assert out == {"A": "hello world", "B": "single"}


def test_parse_skips_blank_and_comments() -> None:
    out = parse_env_text("# top\n\nA=1\n# inline section\nB=2\n")
    assert out == {"A": "1", "B": "2"}


def test_inline_comment_after_bare_value() -> None:
    assert parse_env_text("A=1 # remark\n") == {"A": "1"}


def test_inline_hash_inside_quotes_kept() -> None:
    """`#` inside a quoted value is part of the value (e.g. passwords)."""
    assert parse_env_text('A="p#assword"\n') == {"A": "p#assword"}


def test_last_writer_wins() -> None:
    assert parse_env_text("A=1\nA=2\n") == {"A": "2"}


def test_leading_whitespace_tolerated() -> None:
    assert parse_env_text("   A=1\n") == {"A": "1"}


def test_missing_equals_raises() -> None:
    with pytest.raises(EnvError, match="missing '='"):
        parse_env_text("BROKEN\n")


def test_empty_key_raises() -> None:
    with pytest.raises(EnvError, match="empty key"):
        parse_env_text("=value\n")


# --- load_env ---------------------------------------------------------------


def _write_env(tmp_path: Path, body: str) -> Path:
    p = tmp_path / ".env"
    p.write_text(body, encoding="utf-8")
    return p


def test_load_env_full(tmp_path: Path) -> None:
    p = _write_env(
        tmp_path,
        "ROUTER_HOST=10.0.0.1\nROUTER_USER=admin\nROUTER_PASSWORD=pw\n"
        "ROUTER_PORT=2222\nROUTER_TIMEOUT=15\n",
    )
    assert load_env(p) == Settings(
        host="10.0.0.1",
        user="admin",
        password="pw",
        port=2222,
        timeout=15.0,
    )


def test_load_env_defaults_port_and_timeout(tmp_path: Path) -> None:
    p = _write_env(
        tmp_path,
        "ROUTER_HOST=10.0.0.1\nROUTER_USER=admin\nROUTER_PASSWORD=pw\n",
    )
    settings = load_env(p)
    assert settings.port == 22
    assert settings.timeout == 10.0


def test_load_env_accepts_str_path(tmp_path: Path) -> None:
    """`load_env` documents `str | Path`; both should work."""
    p = _write_env(
        tmp_path,
        "ROUTER_HOST=10.0.0.1\nROUTER_USER=admin\nROUTER_PASSWORD=pw\n",
    )
    settings = load_env(str(p))
    assert settings.host == "10.0.0.1"


def test_load_env_missing_required_raises(tmp_path: Path) -> None:
    p = _write_env(tmp_path, "ROUTER_HOST=10.0.0.1\n")
    with pytest.raises(EnvError) as exc_info:
        load_env(p)
    msg = str(exc_info.value)
    assert "ROUTER_USER" in msg
    assert "ROUTER_PASSWORD" in msg


def test_load_env_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(EnvError, match="not found"):
        load_env(tmp_path / "nonexistent.env")


def test_load_env_bad_port_raises(tmp_path: Path) -> None:
    p = _write_env(
        tmp_path,
        "ROUTER_HOST=10.0.0.1\nROUTER_USER=u\nROUTER_PASSWORD=p\n"
        "ROUTER_PORT=notanint\n",
    )
    with pytest.raises(EnvError, match="ROUTER_PORT"):
        load_env(p)


def test_load_env_bad_timeout_raises(tmp_path: Path) -> None:
    p = _write_env(
        tmp_path,
        "ROUTER_HOST=10.0.0.1\nROUTER_USER=u\nROUTER_PASSWORD=p\n"
        "ROUTER_TIMEOUT=NaaN\n",
    )
    with pytest.raises(EnvError, match="ROUTER_TIMEOUT"):
        load_env(p)
