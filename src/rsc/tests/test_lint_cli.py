"""CLI smoke tests for ``rsc lint``."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rsc.lint_cli import main as lint_main  # noqa: E402


def test_lint_src_clean_returns_0(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    src = tmp_path / "clean.rsc"
    src.write_text(textwrap.dedent("""\
        /interface/list
            add comment="iac.list.wan -- WAN" name=iac.list.wan
    """), encoding="utf-8")
    rc = lint_main(["--src", str(src)])
    assert rc == 0
    # Quiet success: stderr should be empty unless -v.
    err = capsys.readouterr().err
    assert err == ""


def test_lint_src_verbose_prints_clean_message(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    src = tmp_path / "clean.rsc"
    src.write_text(
        '/interface/list\n    add comment="iac.list.wan -- WAN" name=iac.list.wan\n',
        encoding="utf-8",
    )
    rc = lint_main(["--src", str(src), "-v"])
    assert rc == 0
    assert "clean" in capsys.readouterr().err


def test_lint_src_with_errors_returns_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    src = tmp_path / "broken.rsc"
    src.write_text(textwrap.dedent("""\
        /interface/list
            add comment="iac.list.wan -- A" name=iac.list.wan
            add comment="iac.list.wan -- B" name=iac.list.wan
    """), encoding="utf-8")
    rc = lint_main(["--src", str(src)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "LINT001" in err
    assert "iac.list.wan" in err


def test_lint_missing_src_returns_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    rc = lint_main(["--src", str(tmp_path / "nope.rsc")])
    assert rc == 2
    assert "source not found" in capsys.readouterr().err


def test_lint_requires_src_or_profile(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """argparse's required-mutex-group enforces exactly one source."""
    with pytest.raises(SystemExit) as exc:
        lint_main([])
    assert exc.value.code == 2


def test_lint_profile_missing_dir_returns_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    rc = lint_main(["--profile", str(tmp_path / "no-such-profile")])
    assert rc == 2
    assert "profile folder not found" in capsys.readouterr().err


def test_lint_profile_yaml_routes_through_bundle(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """--profile --yaml: bundle in-memory then lint the result."""
    profile = tmp_path / "p"
    profile.mkdir()
    (profile / "10-x.yaml").write_text(textwrap.dedent("""\
        interface:
          list:
            - id: iac.list.wan
              name: iac.list.wan
    """), encoding="utf-8")
    rc = lint_main(["--profile", str(profile), "--yaml"])
    assert rc == 0


def test_lint_empty_config_returns_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """Linting an empty source isn't a clean run; surface as setup error."""
    src = tmp_path / "empty.rsc"
    src.write_text("# just a comment\n", encoding="utf-8")
    rc = lint_main(["--src", str(src)])
    assert rc == 2
    assert "empty config" in capsys.readouterr().err
