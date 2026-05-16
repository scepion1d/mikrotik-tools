"""Render a parsed :class:`~rsc.parser.Config` back into YAML profile sources.

The inverse of :mod:`rsc.yaml.converter`. Takes a Config (typically
loaded via :func:`rsc.parser.parse_file`), groups items by their
top-level menu, and emits one YAML document per group in the
`src/<profile>/NN-<menu>.yaml` shape the converter expects.

Output shape decisions
----------------------
* **Multi-file by default.** One ``NN-<top-menu>.yaml`` per top-level
  RouterOS menu (``interface``, ``ip``, ``ipv6``, ``system``, ...). The
  ``NN-`` prefix tracks :data:`~rsc.parser.menus.MENU_ORDER` so the
  produced layout sorts to the same apply order the bundler later uses.
* **`set` rows preserved.** ``verb=='set'`` items keep ``operation: set``
  + ``filter:`` (the original ``[find ...]`` selector unwrapped).
* **`add` rows compacted.** ``operation: add`` is omitted (it's the
  converter's default).
* **iac comments split.** ``comment="iac.X -- text"`` becomes ``id: iac.X``
  + ``comment: text``. If the literal padding between id and ``--`` is
  more than one space, ``id_pad`` is emitted to preserve byte equivalence.
* **Sigils not reversed (v1).** ``$varname`` and ``[expr]`` come back as
  literal strings. Reverse-sigilization (matching values to a known
  vars.yaml and emitting ``var:`` / ``expr:``) is out of scope here -- a
  later pass can wrap the YAML if needed without losing data.

Public API
----------
- :func:`to_yaml_files`   -- write one YAML file per top-level menu
- :func:`to_yaml_docs`    -- build the ``{filename: doc}`` mapping in memory
- :func:`item_to_yaml`    -- convert a single :class:`~rsc.parser.Item`
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from rsc.parser import IAC_PREFIX, Config, Item


# Comments authored in the .rsc style use ``ID  --  TEXT``; the parser
# preserves any padding between ID and ``--`` verbatim in ``comment=``.
# This regex pulls the three pieces apart so we can re-split into the
# YAML schema's ``id`` / ``id_pad`` / ``comment`` fields.
_COMMENT_SPLIT_RE = re.compile(
    r"""
    ^
    (?P<id>iac\.[\w.\-]+)            # iac.* token
    (?P<pad>\s+)?                    # optional whitespace before separator
    (?:--\s*(?P<text>.+?))?          # optional `-- text` body
    \s*$
    """,
    re.VERBOSE | re.DOTALL,
)

# Properties the parser surfaces internally; we drop them before rendering
# because their information is reconstructed from the actual structured
# fields (``filter`` / ``id`` / ``comment``).
_INTERNAL_PROPS = frozenset({"__selector__"})


def to_yaml_files(
    cfg: Config,
    out_dir: str | Path,
    *,
    overwrite: bool = False,
) -> list[Path]:
    """Write *cfg* to one YAML file per top-level menu under *out_dir*.

    Returns the list of paths written. Creates *out_dir* if needed.

    Args:
        cfg: a parsed Config (typically from :func:`rsc.parser.parse_file`).
        out_dir: target folder; written files land here as ``NN-<menu>.yaml``.
        overwrite: when False (default), refuses to overwrite an existing
            file in *out_dir* -- raises ``FileExistsError`` instead.
    """
    root = Path(out_dir)
    root.mkdir(parents=True, exist_ok=True)
    docs = to_yaml_docs(cfg)
    written: list[Path] = []
    for filename, doc in docs.items():
        path = root / filename
        if path.exists() and not overwrite:
            raise FileExistsError(
                f"refusing to overwrite {path}; pass overwrite=True"
            )
        path.write_text(_dump_yaml(doc), encoding="utf-8")
        written.append(path)
    return written


def to_yaml_docs(cfg: Config) -> dict[str, dict[str, Any]]:
    """Build ``{filename: doc}`` mapping in memory (no I/O).

    Filenames follow ``NN-<menu>.yaml`` (e.g. ``10-interface.yaml``,
    ``30-ip.yaml``, ``99-misc.yaml`` for anything outside known ordering).
    Doc shape matches what the converter expects to round-trip.
    """
    # Group items by top-level menu segment (the bit after the leading /
    # and before the next /). e.g. /interface/list -> "interface".
    groups: dict[str, dict[str, list[Item]]] = {}
    for menu, items in cfg.items_by_menu.items():
        if not menu.startswith("/"):
            continue  # parser shouldn't produce these, defensive only
        parts = menu.lstrip("/").split("/", 1)
        top = parts[0] or "misc"
        sub = "/" + parts[1] if len(parts) > 1 else ""
        groups.setdefault(top, {}).setdefault(sub, []).extend(items)

    # NN-prefix ordering: walk MENU_ORDER for the canonical filename
    # numbers; everything unrecognised lands in 99-misc.yaml. The numeric
    # scheme matches the hand-authored src/<profile>/NN-*.yaml convention.
    nn_for_top = _build_nn_map()
    docs: dict[str, dict[str, Any]] = {}
    # Sort top-level groups by their NN prefix so the dict iterates in
    # apply order. Stable sort: alphabetic within the same NN bucket.
    for top, sub_map in sorted(
        groups.items(),
        key=lambda kv: (nn_for_top.get(kv[0], 99), kv[0]),
    ):
        nn = nn_for_top.get(top, 99)
        filename = f"{nn:02d}-{top}.yaml"
        docs[filename] = _build_module_doc(top, sub_map)
    return docs


def item_to_yaml(item: Item) -> dict[str, Any]:
    """Convert a single :class:`Item` to the YAML mapping form.

    Public so callers building docs manually can use the same logic.
    """
    out: dict[str, Any] = {}

    if item.verb == "set":
        out["operation"] = "set"
        # The parser stashes the original selector verbatim in
        # __selector__; everything inside [find ...] becomes `filter:`,
        # bare positional selectors (e.g. `set telnet ...`) pass through.
        sel = item.props.get("__selector__")
        if sel:
            out["filter"] = _unwrap_selector(sel)

    # iac.X -- text -> id / id_pad / comment split.
    raw_comment = item.props.get("comment", "")
    if raw_comment:
        unquoted = _strip_outer_quotes(raw_comment)
        m = _COMMENT_SPLIT_RE.match(unquoted)
        if m and m.group("id"):
            out["id"] = m.group("id")
            pad = m.group("pad")
            if pad is not None and len(pad) > 1:
                out["id_pad"] = len(pad)
            if m.group("text"):
                out["comment"] = m.group("text").rstrip()
        else:
            # No iac id, just preserve the raw text.
            out["comment"] = unquoted

    # Surface every other prop verbatim. Insertion order is preserved by
    # the dict so the rendered YAML mirrors the source column order.
    #
    # Selector-redundancy suppression: when a `set` row's filter is
    # `KEY=VAL` (e.g. `[find name=admin]`), the parser surfaces KEY=VAL
    # into props so identity_key() resolves stably. We don't want that
    # surfaced KEY=VAL re-emitted as a separate `key: VAL` line in the
    # YAML; it would round-trip as a phantom prop (a regression the
    # compact emitter also guards against, see test_emit_skips_redundant_
    # selector_kv_prop in test_compact.py).
    selector_kv = _parse_kv_selector(out.get("filter", ""))
    for key, value in item.props.items():
        if key in _INTERNAL_PROPS or key == "comment":
            continue
        unquoted = _strip_outer_quotes(value)
        # Suppress KEY=VAL pairs that already live inside the filter.
        if selector_kv == (key, unquoted):
            continue
        # Bare positional selector (e.g. /ip/service: `set telnet ...`):
        # the parser surfaces it as `name=`, but `filter: telnet` already
        # carries the same info.
        if (
            item.verb == "set"
            and key == "name"
            and out.get("filter") == unquoted
        ):
            continue
        out[key] = unquoted
    return out


# --- internals --------------------------------------------------------------


def _build_module_doc(top: str, sub_map: dict[str, list[Item]]) -> dict[str, Any]:
    """Build the YAML doc for one top-level menu (e.g. ``interface``).

    *sub_map* maps a sub-path (``""`` for items directly at the top, or
    ``/list/member`` etc.) to its item list. Sub-paths nest into the
    YAML tree via the same dotted -> nested rules the converter uses
    in reverse.

    Special cases:

    * If the top menu has its OWN items AND child sub-menus, the
      own-items go under ``_items`` (the schema's sentinel for "items
      at this level alongside named children"). Same trick the
      converter uses in the opposite direction.
    * If the top menu has ONLY own-items (no sub-menus), collapse the
      wrapper so ``user:`` maps directly to a list -- matches the
      hand-authored ``src/<profile>/60-system.yaml`` shape.
    * Sub-paths nest into chained dicts; the final segment carries the
      item list.
    """
    has_own_items = "" in sub_map
    sub_paths = [s for s in sub_map if s]

    if has_own_items and not sub_paths:
        # Single-menu file: collapse the wrapper.
        return {top: [item_to_yaml(i) for i in sub_map[""]]}

    doc: dict[str, Any] = {top: {}}
    root: dict[str, Any] = doc[top]

    if has_own_items:
        # Own items + sub-menus: use the _items sentinel.
        root["_items"] = [item_to_yaml(i) for i in sub_map[""]]

    for sub in sub_paths:
        parts = sub.strip("/").split("/")
        node = root
        # Walk every segment except the last, creating dicts as we go.
        # If a segment is already an item-list (because a deeper menu
        # has its own items AND children, like /interface/bridge with
        # /port + /vlan children), upgrade it to a dict with _items.
        for part in parts[:-1]:
            existing = node.get(part)
            if isinstance(existing, list):
                node[part] = {"_items": existing}
            node = node.setdefault(part, {})
        # Terminal segment: either a fresh list, or merge into existing
        # mapping at this leaf (if a deeper menu landed there first).
        leaf = parts[-1]
        existing = node.get(leaf)
        new_items = [item_to_yaml(i) for i in sub_map[sub]]
        if isinstance(existing, dict):
            existing["_items"] = new_items
        else:
            node[leaf] = new_items
    return doc


def _build_nn_map() -> dict[str, int]:
    """``top-menu -> NN`` mapping derived from :data:`MENU_ORDER`.

    Numbering scheme mirrors the existing src/<profile>/ files:
      interface -> 10, ipv6 -> 40 (wait next to ip's firewall), etc.

    Items not in MENU_ORDER get bucket 99 (``99-misc.yaml``).
    """
    return {
        "interface": 10,
        "ip": 30,
        "ipv6": 40,
        "tool": 50,
        "system": 60,
        "user": 60,
        "disk": 60,
    }


def _unwrap_selector(sel: str) -> str:
    """``[find KEY=VAL]`` -> ``KEY=VAL``; bare tokens unchanged."""
    s = sel.strip()
    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1].strip()
        if inner.startswith("find "):
            return inner[5:].strip()
        if inner == "find":
            return ""
    return s


def _parse_kv_selector(filter_str: str) -> tuple[str, str] | None:
    """``KEY=VAL`` -> ``(KEY, VAL)`` (unquoted); anything else -> None.

    Used to detect when a filter already carries a KEY=VAL pair that
    the parser also surfaced into props (e.g. ``[find name=admin]`` ->
    ``filter: name=admin`` + ``props['name'] = 'admin'``). The
    duplicate would round-trip as a phantom prop.
    """
    if "=" not in filter_str:
        return None
    key, _, val = filter_str.partition("=")
    key = key.strip()
    val = val.strip()
    # Trip on selectors with extra clauses (`name=admin disabled=no`) --
    # we can only safely dedup the simple single-KV form.
    if " " in val:
        return None
    val = _strip_outer_quotes(val)
    if not key:
        return None
    return (key, val)


def _strip_outer_quotes(value: str) -> str:
    """``"foo bar"`` -> ``foo bar``; bare values untouched."""
    if (
        len(value) >= 2
        and value[0] == value[-1]
        and value[0] in '"\''
    ):
        return value[1:-1]
    return value


def _dump_yaml(doc: dict[str, Any]) -> str:
    """PyYAML dump tuned for this schema.

    - ``default_flow_style=False`` -> block form by default (matches the
      majority of items in src/; the inline-flow forms in the human-edited
      files are an authoring optimisation we don't reproduce here).
    - ``sort_keys=False`` -> preserve insertion order from
      :func:`item_to_yaml` so column ordering mirrors the source .rsc.
    - 2-space indent and unlimited line width to match repo style.
    """
    return yaml.safe_dump(
        doc,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
        width=2**31,  # effectively no wrapping
        indent=2,
    )
