"""Tests for the :foreach -> array-binding unfolder."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rsc_bundle.unfold import unfold  # noqa: E402


def test_no_bindings_passthrough() -> None:
    text = ":log info x\n"
    assert unfold(text) == text


def test_unrolls_foreach_over_local_array() -> None:
    text = (
        ':local files { "a.rsc"; "b.rsc"; "c.rsc" }\n'
        ":foreach f in=$files do={\n"
        "    /import file-name=$f\n"
        "}\n"
    )
    out = unfold(text)
    assert out.count('/import file-name="a.rsc"') == 1
    assert out.count('/import file-name="b.rsc"') == 1
    assert out.count('/import file-name="c.rsc"') == 1
    # original :foreach gone
    assert ":foreach f in=$files" not in out


def test_unrolls_inside_if_block() -> None:
    """Nested foreach inside :if/:else should still unroll."""
    text = (
        ':local mods { "m1.rsc"; "m2.rsc" }\n'
        ":if ($flag) do={\n"
        "    :foreach f in=$mods do={\n"
        "        /import file-name=$f\n"
        "    }\n"
        "} else={\n"
        '    :log info "skip"\n'
        "}\n"
    )
    out = unfold(text)
    assert out.count('/import file-name="m1.rsc"') == 1
    assert out.count('/import file-name="m2.rsc"') == 1
    assert ":if ($flag)" in out
    assert "else=" in out


def test_keeps_unknown_array_foreach() -> None:
    text = (
        ":foreach x in=$unknown do={\n"
        "    :log info $x\n"
        "}\n"
    )
    assert unfold(text) == text


def test_global_array_binding_recognised() -> None:
    text = (
        ':global gFiles { "a.rsc"; "b.rsc" }\n'
        ":foreach f in=$gFiles do={\n"
        "    /import file-name=$f\n"
        "}\n"
    )
    out = unfold(text)
    assert '/import file-name="a.rsc"' in out
    assert '/import file-name="b.rsc"' in out


def test_substitutes_in_string_concat() -> None:
    text = (
        ':local arr { "x.rsc" }\n'
        ":foreach f in=$arr do={\n"
        '    $iacLogInfo ("apply.import: " . $f)\n'
        "    /import file-name=$f\n"
        "}\n"
    )
    out = unfold(text)
    assert '("apply.import: " . "x.rsc")' in out
    assert '/import file-name="x.rsc"' in out


def test_word_boundary_no_overrun() -> None:
    """$f shouldn't substitute into $foo."""
    text = (
        ':local arr { "x" }\n'
        ":foreach f in=$arr do={\n"
        "    :put $foo\n"
        "    :put $f\n"
        "}\n"
    )
    out = unfold(text)
    assert ":put $foo" in out
    assert ':put "x"' in out


def test_non_string_array_skipped() -> None:
    """Mixed/non-string arrays should be left alone."""
    text = (
        ":local arr { 1; 2; 3 }\n"
        ":foreach n in=$arr do={\n"
        "    :put $n\n"
        "}\n"
    )
    # Should leave the foreach intact since arr isn't a string list.
    assert ":foreach n in=$arr" in unfold(text)


if __name__ == "__main__":
    test_no_bindings_passthrough()
    test_unrolls_foreach_over_local_array()
    test_unrolls_inside_if_block()
    test_keeps_unknown_array_foreach()
    test_global_array_binding_recognised()
    test_substitutes_in_string_concat()
    test_word_boundary_no_overrun()
    test_non_string_array_skipped()
    print("ok")
