"""Validate a parsed YAML doc against the rsc JSON Schema.

Public surface
--------------
- :func:`validate`            -- validate a parsed mapping (dict).
- :func:`validate_text`       -- parse YAML text + validate, line numbers in errors.
- :func:`validate_file`       -- read a file + validate, file path in errors.
- :func:`bundled_schema`      -- return the schema bundled inside :mod:`rsc.schema`.
- :class:`SchemaValidationError` -- raised on any schema violation.

The schema itself ships *inside* this package -- see :mod:`rsc.schema`
for the fragment set and :func:`rsc.schema.bundle`. Pass ``None`` as
the schema path to :func:`validate_file` (or call :func:`bundled_schema`
directly) to use the in-memory bundle without touching the disk. Pass
an explicit path to override (e.g. a downstream repo that ships its
own schema variant).

Why a separate module
---------------------
The converter (:mod:`rsc.yaml.converter`) already does light structural
checks (missing `operation`, bad value types, etc.) at *render time*.
Schema validation is the broader, earlier net: catches typos in keys
(``oprator`` instead of ``operation``), wrong value enums, missing
required props -- *before* the converter even sees the doc. Two layers,
on purpose; the converter's checks are tighter (it knows render
semantics) and stay as the last line of defence.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml


class SchemaValidationError(Exception):
    """Raised when a YAML doc fails JSON Schema validation.

    Message format: one human-readable summary line, followed by one
    line per individual error with the JSON-pointer-like path into the
    doc, and (when available) the source line number from the original
    YAML text.
    """


def validate(doc: Any, schema: dict[str, Any]) -> None:
    """Validate *doc* (already parsed) against *schema*.

    Collects every error and raises a single
    :class:`SchemaValidationError` with one line per problem -- so the
    caller sees all issues at once, not one-at-a-time.

    Raises
    ------
    SchemaValidationError
        If the doc has any schema violations.
    """
    errors = _collect_errors(doc, schema)
    if errors:
        raise SchemaValidationError(_format_errors(errors))


def validate_text(yaml_text: str, schema: dict[str, Any]) -> None:
    """Parse *yaml_text* then validate.

    Re-loads with ``yaml.compose`` first so we can attach line numbers
    to errors. The parsed value (dict) is the same as
    :func:`yaml.safe_load` would return.
    """
    # Compose returns a node tree with start/end marks; safe_load alone
    # discards that. The two-pass parse is cheap (YAML is small).
    try:
        node = yaml.compose(yaml_text)
        doc = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        # Surface as a SchemaValidationError so callers handle one type.
        raise SchemaValidationError(f"YAML parse failed: {exc}") from exc

    line_map = _build_line_map(node) if node is not None else {}
    errors = _collect_errors(doc, schema)
    if errors:
        raise SchemaValidationError(_format_errors(errors, line_map))


def bundled_schema() -> dict[str, Any]:
    """Return the JSON Schema bundled inside :mod:`rsc.schema`.

    Built in-memory from the fragment set shipped with the wheel --
    no disk I/O against the consumer repo. Lets callers validate
    without first running ``rsc schema --out ...``.
    """
    # Local import to keep `rsc.yaml.validate` import-light for callers
    # that never validate (e.g. plain `rsc bundle` without --validate).
    from rsc.schema import bundle as _bundle

    return _bundle()


def validate_file(path: str | Path, schema_path: str | Path | None = None) -> None:
    """Read a YAML file and validate against a JSON Schema.

    *schema_path* selects the schema source:

    - ``None``  -- use :func:`bundled_schema` (the in-memory bundle
                   from :mod:`rsc.schema`). This is the default.
    - a path    -- load and use the schema at that path verbatim.

    Both the YAML path and (when set) the schema path are reported in
    error messages so the caller can locate the problem without
    piecing the context together.
    """
    p = Path(path)
    try:
        yaml_text = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise SchemaValidationError(f"cannot read {p}: {exc}") from exc
    if schema_path is None:
        schema = bundled_schema()
    else:
        sp = Path(schema_path)
        try:
            schema = json.loads(sp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SchemaValidationError(f"cannot load schema {sp}: {exc}") from exc
    try:
        validate_text(yaml_text, schema)
    except SchemaValidationError as exc:
        # Prepend the file path so the error stack is easy to read.
        raise SchemaValidationError(f"{p}: {exc}") from exc


# --- internals --------------------------------------------------------------


def _collect_errors(doc: Any, schema: dict[str, Any]) -> list[Any]:
    """Run jsonschema's Draft7 validator and collect all errors."""
    # Local import keeps the dep cost off the import path of every
    # rsc.yaml consumer -- only callers that actually validate pay it.
    from jsonschema import Draft7Validator

    validator = Draft7Validator(schema)
    # `iter_errors` walks the whole tree -- gives every leaf failure
    # at once. Sort by JSON-path so output is deterministic.
    return sorted(
        validator.iter_errors(doc),
        key=lambda e: (list(e.absolute_path), e.message),
    )


def _format_errors(errors: list[Any], line_map: dict[tuple, int] | None = None) -> str:
    """Render a multi-line error message with paths + line numbers."""
    lines = [f"schema validation failed: {len(errors)} error(s)"]
    for err in errors:
        path = "/" + "/".join(str(p) for p in err.absolute_path) if err.absolute_path else "/"
        line_no = None
        if line_map is not None:
            # Try the full path, then walk up until we find a known node.
            key = tuple(err.absolute_path)
            while key and key not in line_map:
                key = key[:-1]
            line_no = line_map.get(key)
        prefix = f"  {path}"
        if line_no is not None:
            prefix += f" (line {line_no})"
        lines.append(f"{prefix}: {err.message}")
    return "\n".join(lines)


def _build_line_map(node: Any) -> dict[tuple, int]:
    """Map JSON-pointer paths (as tuples) to 1-based line numbers.

    Walks the PyYAML node tree, recording where each scalar/mapping/
    sequence starts in the source text. Keys reach values via the same
    path the jsonschema validator uses (``absolute_path``), so error
    locations line up exactly.
    """
    out: dict[tuple, int] = {}

    def walk(n: Any, path: tuple) -> None:
        # mark+1 because PyYAML uses 0-based line indices but humans
        # (and most editors) use 1-based.
        out[path] = n.start_mark.line + 1
        if isinstance(n, yaml.MappingNode):
            for key_node, val_node in n.value:
                # Use the scalar key value (not the node) for the path.
                key = key_node.value if isinstance(key_node, yaml.ScalarNode) else None
                if key is not None:
                    walk(val_node, path + (key,))
        elif isinstance(n, yaml.SequenceNode):
            for i, child in enumerate(n.value):
                walk(child, path + (i,))

    walk(node, ())
    return out
