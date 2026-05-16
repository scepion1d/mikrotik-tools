"""Tests for rsc.yaml.validate -- JSON Schema validation of YAML profiles."""

from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from rsc.yaml import (  # noqa: E402
    SchemaValidationError,
    validate,
    validate_file,
    validate_text,
)


# Minimal schema covering the bits the tests exercise. The repo's real
# schema lives at ../src/schema.json; these tests verify the validator
# wiring, not the schema content (which has its own coverage via the
# fixtures in tests/bundle/fixtures/yaml-profile).
#
# Deliberately *not* using `oneOf` for the menu type -- jsonschema would
# report one top-level oneOf failure instead of descending into the
# offending item. The real schema uses oneOf and behaves the same way;
# we test the descent behaviour here with a simpler shape.
MINIMAL_SCHEMA: dict = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "additionalProperties": {
        "type": "object",
        "additionalProperties": {
            "type": "array",
            "items": {"$ref": "#/definitions/item"},
        },
    },
    "definitions": {
        "item": {
            "type": "object",
            "properties": {
                "operation": {"type": "string", "enum": ["add", "set"]},
                "filter": {"type": "string"},
                "id": {"type": "string", "pattern": "^iac\\.[\\w.-]+$"},
                "comment": {"type": "string"},
            },
        },
    },
}


def test_valid_doc_passes_silently() -> None:
    doc = {
        "interface": {
            "list": [{"id": "iac.list.wan", "name": "iac.list.wan"}],
        },
    }
    # No exception = success.
    validate(doc, MINIMAL_SCHEMA)


def test_typo_in_operation_value_is_caught() -> None:
    """An `operation` value outside the enum trips the validator."""
    with pytest.raises(SchemaValidationError) as exc:
        validate(
            {"interface": {"list": [{"operation": "addd"}]}},
            MINIMAL_SCHEMA,
        )
    msg = str(exc.value)
    assert "schema validation failed" in msg
    assert "operation" in msg
    assert "addd" in msg


def test_bad_id_pattern_is_caught() -> None:
    """An `id` that doesn't start with `iac.` violates the pattern."""
    with pytest.raises(SchemaValidationError, match="id"):
        validate(
            {"interface": {"list": [{"id": "not-iac-prefixed"}]}},
            MINIMAL_SCHEMA,
        )


def test_multiple_errors_all_reported() -> None:
    """Validator collects every leaf failure, not just the first."""
    with pytest.raises(SchemaValidationError) as exc:
        validate(
            {"interface": {"list": [
                {"operation": "bogus", "id": "missing-prefix"},
            ]}},
            MINIMAL_SCHEMA,
        )
    msg = str(exc.value)
    # Header line counts ALL errors.
    assert "2 error(s)" in msg


def test_validate_text_attaches_line_numbers() -> None:
    """`validate_text` annotates each error with its source line."""
    yaml_text = textwrap.dedent("""\
        interface:
          list:
            - operation: addd
              id: iac.list.wan
    """)
    with pytest.raises(SchemaValidationError) as exc:
        validate_text(yaml_text, MINIMAL_SCHEMA)
    msg = str(exc.value)
    # The bad `operation` value is on line 3 of the source.
    assert "line 3" in msg


def test_validate_text_handles_yaml_parse_failure() -> None:
    """Malformed YAML surfaces as SchemaValidationError too."""
    with pytest.raises(SchemaValidationError, match="YAML parse failed"):
        validate_text("not: valid: yaml: at all", MINIMAL_SCHEMA)


def test_validate_file_includes_path_in_error(tmp_path: Path) -> None:
    """File path is prepended to the error message."""
    schema_path = tmp_path / "schema.json"
    schema_path.write_text(json.dumps(MINIMAL_SCHEMA), encoding="utf-8")

    bad = tmp_path / "broken.yaml"
    bad.write_text(
        "interface:\n  list:\n    - operation: nope\n",
        encoding="utf-8",
    )
    with pytest.raises(SchemaValidationError) as exc:
        validate_file(bad, schema_path)
    msg = str(exc.value)
    assert "broken.yaml" in msg


def test_validate_file_missing_schema(tmp_path: Path) -> None:
    bad = tmp_path / "x.yaml"
    bad.write_text("interface: {}\n", encoding="utf-8")
    with pytest.raises(SchemaValidationError, match="cannot load schema"):
        validate_file(bad, tmp_path / "no-such-schema.json")


def test_validate_against_repo_schema_with_real_fixture() -> None:
    """Smoke test against the real src/schema.json + a real profile module.

    Verifies the schema is internally consistent and accepts what the
    repo actually ships. Skips if either file isn't present (e.g. when
    the rsc package is consumed outside this monorepo).

    Path arithmetic: this file is at
    ``<repo>/tools/src/rsc/tests/yaml_subpkg/test_validate.py`` so
    ``parents[5]`` is the repo root in the iac layout.
    """
    repo_root = Path(__file__).resolve().parents[5]
    schema_path = repo_root / "src" / "schema.json"
    real_yaml = repo_root / "src" / "segmentedx3" / "10-interfaces.yaml"
    if not schema_path.is_file() or not real_yaml.is_file():
        pytest.skip("repo schema or profile module not available")
    validate_file(real_yaml, schema_path)
