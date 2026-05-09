"""Tests for the .env parser. Network code is intentionally untested at MVP."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rsc_deploy.config import EnvError, Settings, load_env, parse_env_text  # noqa: E402


def _write(tmp: Path, name: str, body: str) -> Path:
    p = tmp / name
    p.write_text(body, encoding="utf-8")
    return p


def test_parse_basic() -> None:
    out = parse_env_text("A=1\nB=two\n")
    assert out == {"A": "1", "B": "two"}


def test_parse_quoted_values() -> None:
    out = parse_env_text('A="hello world"\nB=\'single\'\n')
    assert out == {"A": "hello world", "B": "single"}


def test_parse_skips_blank_and_comments() -> None:
    out = parse_env_text("# top\n\nA=1\n# inline section\nB=2\n")
    assert out == {"A": "1", "B": "2"}


def test_inline_comment_after_bare_value() -> None:
    out = parse_env_text("A=1 # remark\n")
    assert out == {"A": "1"}


def test_inline_hash_inside_quotes_kept() -> None:
    out = parse_env_text('A="p#assword"\n')
    assert out == {"A": "p#assword"}


def test_missing_equals_raises() -> None:
    try:
        parse_env_text("BROKEN\n")
    except EnvError as exc:
        assert "missing '='" in str(exc)
        return
    raise AssertionError("expected EnvError")


def test_load_env_full(tmp_path: Path = Path(".")) -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = _write(
            Path(td),
            ".env",
            "ROUTER_HOST=10.0.0.1\nROUTER_USER=admin\nROUTER_PASSWORD=pw\nROUTER_PORT=2222\nROUTER_TIMEOUT=15\n",
        )
        settings = load_env(p)
        assert settings == Settings(
            host="10.0.0.1",
            user="admin",
            password="pw",
            port=2222,
            timeout=15.0,
        )


def test_load_env_defaults_port_and_timeout() -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = _write(
            Path(td),
            ".env",
            "ROUTER_HOST=10.0.0.1\nROUTER_USER=admin\nROUTER_PASSWORD=pw\n",
        )
        settings = load_env(p)
        assert settings.port == 22
        assert settings.timeout == 10.0


def test_load_env_missing_required_raises() -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = _write(Path(td), ".env", "ROUTER_HOST=10.0.0.1\n")
        try:
            load_env(p)
        except EnvError as exc:
            assert "ROUTER_USER" in str(exc)
            assert "ROUTER_PASSWORD" in str(exc)
            return
        raise AssertionError("expected EnvError")


def test_load_env_missing_file_raises() -> None:
    try:
        load_env("nonexistent.env")
    except EnvError as exc:
        assert "not found" in str(exc)
        return
    raise AssertionError("expected EnvError")


def test_load_env_bad_port_raises() -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = _write(
            Path(td),
            ".env",
            "ROUTER_HOST=10.0.0.1\nROUTER_USER=u\nROUTER_PASSWORD=p\nROUTER_PORT=notanint\n",
        )
        try:
            load_env(p)
        except EnvError as exc:
            assert "ROUTER_PORT" in str(exc)
            return
        raise AssertionError("expected EnvError")


if __name__ == "__main__":
    test_parse_basic()
    test_parse_quoted_values()
    test_parse_skips_blank_and_comments()
    test_inline_comment_after_bare_value()
    test_inline_hash_inside_quotes_kept()
    test_missing_equals_raises()
    test_load_env_full()
    test_load_env_defaults_port_and_timeout()
    test_load_env_missing_required_raises()
    test_load_env_missing_file_raises()
    test_load_env_bad_port_raises()
    print("ok")
