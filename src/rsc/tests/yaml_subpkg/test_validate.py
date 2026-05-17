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
    bundled_schema,
    validate,
    validate_file,
    validate_text,
)


# Minimal schema covering the bits the tests exercise. The real schema
# ships inside rsc.schema (see :func:`rsc.yaml.bundled_schema`); these
# tests verify the validator wiring, not the schema content (which has
# its own coverage via the fixtures in tests/bundle/fixtures/yaml-profile).
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


def test_bundled_schema_round_trips() -> None:
    """`bundled_schema()` returns a Draft-07 dict with definitions.

    Smoke test: ensures the in-memory bundle wires up correctly and
    looks like the schema we expect (so callers can rely on it without
    a separate `rsc schema --out ...` step).
    """
    schema = bundled_schema()
    assert isinstance(schema, dict)
    assert schema.get("$schema") == "http://json-schema.org/draft-07/schema#"
    defs = schema.get("definitions")
    assert isinstance(defs, dict) and defs, "bundled schema must define definitions"


def test_validate_file_defaults_to_bundled_schema(tmp_path: Path) -> None:
    """`validate_file(path)` with no schema uses the bundled schema.

    Authored against the real bundled schema so we exercise the same
    definitions production callers hit.
    """
    good = tmp_path / "interfaces.yaml"
    good.write_text(
        "interface:\n  list:\n    - {operation: add, id: iac.list.wan, name: iac.list.wan}\n",
        encoding="utf-8",
    )
    # No schema path = use rsc.schema.bundle() in-memory. Should not raise.
    validate_file(good)


def test_validate_file_bundled_catches_typo(tmp_path: Path) -> None:
    """The bundled schema catches a misspelled property on a typed item."""
    bad = tmp_path / "bad.yaml"
    # `nme` is not a known property of item_interface_list, which has
    # `additionalProperties: false`.
    bad.write_text(
        "interface:\n  list:\n    - {id: iac.list.wan, nme: iac.list.wan}\n",
        encoding="utf-8",
    )
    with pytest.raises(SchemaValidationError):
        validate_file(bad)
