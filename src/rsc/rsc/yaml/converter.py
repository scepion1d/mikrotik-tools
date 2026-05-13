"""YAML -> RouterOS ``.rsc`` renderer.

See the package docstring (``rsc.yaml``) and the schema reference at
``src/README.md`` for the document shapes this module accepts.

Implementation notes
--------------------
* Output is intentionally minimal but human-readable: one ``/menu/path``
  header per menu, one ``add`` / ``set`` per item, no continuation
  backslashes. The bundler's ``flatten`` + ``compact`` passes will
  re-normalise anyway; this module just needs to emit *parseable* text
  with all `:global`, `[find ...]`, and `$var` forms intact.

* Menu walk order is *insertion order* of YAML mappings (preserved by
  PyYAML's ``safe_load``). The ``_items`` sentinel key holds the
  ops that belong to the menu at its current path; child sub-menus sit
  alongside it in declaration order. Authors decide whether parent ops
  come before or after children by where they place ``_items`` in the
  mapping.

* ``set`` selectors: a ``filter`` value containing ``=`` is wrapped as
  ``[find FILTER]`` (the standard RouterOS form); a bare token (e.g.
  ``telnet``, ``ssh`` for ``/ip/service``) is emitted verbatim --
  RouterOS accepts ``set <name> ...`` for menus where rows are keyed
  by name without a `find`.

* Property values: ``{var: NAME}`` -> ``$NAME``; ``{expr: '...'}`` ->
  ``[...]``; scalars get RouterOS quoting rules applied (mirror of
  ``rsc.bundle.compact._requote``).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml


# Same set the compact emitter uses to decide whether a value needs
# quotes in /export-style output. Kept in sync deliberately.
_NEEDS_QUOTE_RE = re.compile(r'[\s\[\]{}();\\"`#$<>|&?*]')


class YamlError(Exception):
    """Raised on malformed YAML or schema violations."""


def to_rsc(yaml_text: str) -> str:
    """Render *yaml_text* (one document) to ``.rsc`` text.

    Empty / whitespace-only input renders to an empty string.

    Raises
    ------
    YamlError
        On YAML parse failure or schema violation.
    """
    try:
        doc = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        raise YamlError(f"YAML parse failed: {exc}") from exc

    if doc is None:
        return ""
    if not isinstance(doc, dict):
        raise YamlError(
            f"top-level YAML must be a mapping, got {type(doc).__name__}"
        )
    return _render_doc(doc)


def to_rsc_file(path: str | Path) -> str:
    """Read *path* and render its contents.

    Wraps :func:`to_rsc` with file-context error messages so a malformed
    file in a profile folder is easy to locate.
    """
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise YamlError(f"cannot read {p}: {exc}") from exc
    try:
        return to_rsc(text)
    except YamlError as exc:
        raise YamlError(f"{p}: {exc}") from exc


# --- top-level dispatch -----------------------------------------------------


def _render_doc(doc: dict[str, Any]) -> str:
    """Pick module vs globals shape and render."""
    out: list[str] = []
    _emit_description(doc.get("description"), out)

    if "globals" in doc:
        _render_globals(doc["globals"], out)
    else:
        _render_module(doc, out)

    text = "\n".join(out).rstrip()
    return text + "\n" if text else ""


def _emit_description(desc: Any, out: list[str]) -> None:
    """File-level ``description: |`` -> banner of ``# ...`` lines."""
    if desc is None:
        return
    if not isinstance(desc, str):
        raise YamlError(f"`description` must be a string, got {type(desc).__name__}")
    for line in desc.rstrip("\n").splitlines():
        out.append(f"# {line}" if line else "#")
    if out:
        out.append("")


# --- globals ----------------------------------------------------------------


def _render_globals(entries: Any, out: list[str]) -> None:
    """``globals:`` list -> ``:global NAME "VALUE"`` lines."""
    if not isinstance(entries, list):
        raise YamlError(
            f"`globals` must be a list, got {type(entries).__name__}"
        )
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise YamlError(f"globals[{i}] must be a mapping")
        try:
            name = entry["name"]
        except KeyError:
            raise YamlError(f"globals[{i}] is missing `name`") from None
        if not isinstance(name, str) or not name:
            raise YamlError(f"globals[{i}].name must be a non-empty string")
        value = entry.get("value", "")
        if not isinstance(value, (str, int, float)):
            raise YamlError(
                f"globals[{i}].value must be a scalar, got {type(value).__name__}"
            )

        # Per-entry `description: |` becomes a comment block immediately
        # before the :global line. Mirrors the prose layout of vars.rsc.
        desc = entry.get("description")
        if desc is not None:
            if not isinstance(desc, str):
                raise YamlError(f"globals[{i}].description must be a string")
            if out and out[-1] != "":
                out.append("")
            for line in desc.rstrip("\n").splitlines():
                out.append(f"# {line}" if line else "#")

        # `:global NAME "VALUE"` -- always quote the value so empty
        # strings, spaces, and special chars all serialise unambiguously.
        # The RouterOS scripting layer treats backslashes specially, so
        # we forbid embedded `"` rather than try to escape (no fixture
        # has ever needed it).
        sval = str(value)
        if '"' in sval:
            raise YamlError(
                f"globals[{i}].value contains an embedded \" "
                "which the renderer doesn't know how to escape"
            )
        out.append(f':global {name} "{sval}"')


# --- module: menus + items --------------------------------------------------


def _render_module(doc: dict[str, Any], out: list[str]) -> None:
    """Walk the menu tree at the top level of *doc*."""
    for key, child in doc.items():
        if key == "description":
            continue
        if not isinstance(key, str) or not key:
            raise YamlError(f"top-level key must be a non-empty string, got {key!r}")
        _walk(child, f"/{key}", out)


def _walk(node: Any, menu_path: str, out: list[str]) -> None:
    """Render *node* at *menu_path*.

    A list is the menu's items (one operation each). A mapping is a
    tree: ``_items`` (when present) is this menu's items, every other
    key is a child sub-menu name appended to ``menu_path``.
    """
    if isinstance(node, list):
        _emit_menu(menu_path, node, out)
        return
    if not isinstance(node, dict):
        raise YamlError(
            f"{menu_path}: expected list of items or mapping of sub-menus, "
            f"got {type(node).__name__}"
        )
    for key, child in node.items():
        if key == "description":
            continue
        if key == "_items":
            if not isinstance(child, list):
                raise YamlError(f"{menu_path}._items must be a list")
            _emit_menu(menu_path, child, out)
            continue
        if not isinstance(key, str) or not key:
            raise YamlError(
                f"{menu_path}: sub-menu key must be a non-empty string, got {key!r}"
            )
        _walk(child, f"{menu_path}/{key}", out)


def _emit_menu(menu_path: str, items: list[Any], out: list[str]) -> None:
    """Emit ``/menu/path`` header + one line per item.

    Empty *items* list is silently skipped; callers occasionally leave
    a placeholder list while authoring.
    """
    if not items:
        return
    if out and out[-1] != "":
        out.append("")
    out.append(menu_path)
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise YamlError(f"{menu_path}[{i}] must be a mapping")
        out.append("    " + _render_item(menu_path, i, item))


# --- one operation line -----------------------------------------------------


# Reserved item-level keys that don't render as RouterOS properties.
_ITEM_RESERVED = frozenset({"operation", "filter", "id", "id_pad", "comment"})


def _render_item(menu_path: str, idx: int, item: dict[str, Any]) -> str:
    """``{operation, filter?, id?, comment?, <props>}`` -> one .rsc line."""
    try:
        verb = item["operation"]
    except KeyError:
        raise YamlError(f"{menu_path}[{idx}] is missing `operation`") from None
    if not isinstance(verb, str) or not verb:
        raise YamlError(f"{menu_path}[{idx}].operation must be a non-empty string")

    parts: list[str] = [verb]

    selector = item.get("filter")
    if selector is not None:
        if not isinstance(selector, str) or not selector:
            raise YamlError(
                f"{menu_path}[{idx}].filter must be a non-empty string"
            )
        # `filter: name=admin` -> `[find name=admin]`.
        # `filter: telnet`     -> bare token (e.g. /ip/service rows).
        # The `=` heuristic distinguishes the two reliably for our schema.
        if "=" in selector:
            parts.append(f"[find {selector}]")
        else:
            parts.append(selector)

    # Reconstruct the original `comment="ID -- TEXT"` form when both
    # halves are present, or emit just one half if only one is.
    #
    # `id_pad` (optional, default 1) is the literal space count between
    # the id and the `--` separator. The original .rsc files use
    # multi-space padding to column-align `--` markers across
    # neighbouring items; preserving that padding keeps bundles
    # byte-equivalent to what was authored / deployed (so `rsc diff` doesn't
    # see cosmetic-only drift). Default 1 -> `"<id> -- <text>"`.
    iac_id = item.get("id")
    comment = item.get("comment")
    id_pad = item.get("id_pad", 1)
    if iac_id is not None and not isinstance(iac_id, str):
        raise YamlError(f"{menu_path}[{idx}].id must be a string")
    if comment is not None and not isinstance(comment, str):
        raise YamlError(f"{menu_path}[{idx}].comment must be a string")
    if not isinstance(id_pad, int) or isinstance(id_pad, bool) or id_pad < 1:
        raise YamlError(
            f"{menu_path}[{idx}].id_pad must be an integer >= 1"
        )
    if iac_id is not None and comment is not None:
        sep = " " * id_pad + "-- "
        parts.append(f'comment="{iac_id}{sep}{comment}"')
    elif iac_id is not None:
        parts.append(f'comment="{iac_id}"')
    elif comment is not None:
        parts.append(f'comment="{comment}"')

    # Everything else is a RouterOS property. Insertion order of the
    # YAML mapping is preserved -- authors control the column order in
    # the rendered .rsc by the order they write keys.
    for key, value in item.items():
        if key in _ITEM_RESERVED:
            continue
        if not isinstance(key, str) or not key:
            raise YamlError(
                f"{menu_path}[{idx}]: property key must be a non-empty string"
            )
        parts.append(f"{key}={_render_value(menu_path, idx, key, value)}")

    return " ".join(parts)


# --- value rendering --------------------------------------------------------


def _render_value(menu_path: str, idx: int, key: str, value: Any) -> str:
    """Render a single property value.

    Recognises the two structured forms from the schema:
    ``{var: NAME}``  -> ``$NAME``  (resolved by the bundler's flatten pass)
    ``{expr: '...'}``-> ``[...]``  (RouterOS bracket expression)

    Plain scalars go through :func:`_quote`.
    """
    if isinstance(value, dict):
        if "var" in value:
            name = value["var"]
            if not isinstance(name, str) or not name:
                raise YamlError(
                    f"{menu_path}[{idx}].{key}.var must be a non-empty string"
                )
            return f"${name}"
        if "expr" in value:
            expr = value["expr"]
            if not isinstance(expr, str) or not expr:
                raise YamlError(
                    f"{menu_path}[{idx}].{key}.expr must be a non-empty string"
                )
            return f"[{expr}]"
        raise YamlError(
            f"{menu_path}[{idx}].{key} mapping must have `var` or `expr` key, "
            f"got keys: {sorted(value.keys())}"
        )
    if isinstance(value, bool):
        # Defensive: YAML 1.1 booleans should be quoted (`'yes'`/`'no'`)
        # in the source, but if a True/False slips through, render the
        # RouterOS form so the bundler still parses it.
        return "yes" if value else "no"
    if value is None:
        return '""'
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return _quote(value)
    raise YamlError(
        f"{menu_path}[{idx}].{key}: unsupported value type {type(value).__name__}"
    )


def _quote(value: str) -> str:
    """Quote *value* iff RouterOS /export-style requires it.

    Mirrors :func:`rsc.bundle.compact._requote`. Bracket expressions
    pass through unquoted (handled separately via the ``expr:`` form
    above; this branch defends against an authored literal like
    ``ranges: '[ ... ]'`` that's already a string).
    """
    if value == "":
        return '""'
    if len(value) >= 2 and value[0] == "[" and value[-1] == "]":
        return value
    if _NEEDS_QUOTE_RE.search(value):
        if '"' in value:
            raise YamlError(
                f"value {value!r} needs quoting but contains an embedded "
                "\" the renderer can't escape"
            )
        return f'"{value}"'
    return value
