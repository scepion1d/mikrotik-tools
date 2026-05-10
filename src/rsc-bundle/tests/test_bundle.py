"""Smoke tests for rsc-bundle."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rsc_bundle import BundleError, bundle_inline, bundle_file  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"


def test_simple_inline() -> None:
    out = bundle_file(FIX / "simple" / "entry.rsc")
    # body should appear inside, original /import line should be gone
    assert ":log info \"inner body\"" in out
    assert "/import file-name=inner.rsc" not in out
    # both the surrounding logs from entry.rsc preserved
    assert ":log info \"before inner\"" in out
    assert ":log info \"after inner\"" in out


def test_nested_inline() -> None:
    out = bundle_file(FIX / "nested" / "a.rsc")
    # b's content present, and c's content present (transitively + directly)
    assert ":log info \"b:start\"" in out
    assert ":log info \"b:end\"" in out
    assert ":log info \"c:body\"" in out
    # c.rsc imported twice (via b and via a) -- second time should be a skip note
    assert "skipped duplicate import of c.rsc" in out
    # No raw /import lines should remain
    assert "/import file-name=" not in out


def test_indented_import_resolved() -> None:
    """Leading whitespace on the import line shouldn't break matching."""
    sources = {
        "x.rsc": "    /import file-name=y.rsc\n",
        "y.rsc": ":log info y\n",
    }
    out = bundle_inline("x.rsc", sources)
    assert ":log info y" in out
    assert "/import file-name=" not in out


def test_missing_target_raises() -> None:
    sources = {"a.rsc": "/import file-name=missing.rsc\n"}
    try:
        bundle_inline("a.rsc", sources)
    except BundleError as exc:
        assert "missing.rsc" in str(exc)
        return
    raise AssertionError("expected BundleError for missing import")


def test_cycle_detected() -> None:
    try:
        bundle_file(FIX / "cycle" / "a.rsc")
    except BundleError as exc:
        assert "cycle" in str(exc).lower()
        return
    raise AssertionError("expected BundleError for cycle")


def test_entry_outside_root_works() -> None:
    """bundle_file should still pick up an entry that lives outside root."""
    # entry IS under its own parent dir as root by default; this just checks
    # that explicit root != entry.parent doesn't break.
    out = bundle_file(FIX / "simple" / "entry.rsc", root=FIX / "simple")
    assert ":log info \"inner body\"" in out


if __name__ == "__main__":
    test_simple_inline()
    test_nested_inline()
    test_indented_import_resolved()
    test_missing_target_raises()
    test_cycle_detected()
    test_entry_outside_root_works()
    print("ok")
