"""Stable iac-namespace identifiers for parsed items, with synthetic fallback.

Background
----------
Most config items in this codebase carry an ``iac.<type>.<subtype>.<tag>``
identifier in their ``name=`` field or ``comment=`` text. That id is the
unit of stable identity across edits and is what diff/bundle use to match
items between two snapshots.

A handful of items genuinely cannot carry such an id:

  - **Singletons** (``/system/clock``, ``/disk/settings``, ...) -- one
    implicit row per menu, no ``add``, no ``comment=`` field on most.
  - **Built-in services** (``/ip/service set telnet ...``,
    ``/user set [find name=admin] ...``) -- the row exists from boot
    and is addressed by a fixed selector, not by a free-form id.
  - **Default-named hardware** (``set [find default-name=etherN] ...``)
    when the operator has not yet renamed the interface.

For these, this module derives a deterministic *synthetic* id from the
menu path and selector. The synthetic id has the same ``iac.``-prefixed
shape as user-authored ids, so downstream code can treat both uniformly.

Public API
----------
- :func:`entity_id` -- return a bare ``iac.x.y.z`` identifier for an
  :class:`~rsc_parser.model.Item`.
- :func:`is_synthetic` -- True if a given id was derived synthetically
  (vs read from the item's own ``name=`` / ``comment=``).
"""

from __future__ import annotations

from .menus import IAC_PREFIX, MENUS_ORDERED, MENUS_SINGLETON, MENUS_WITH_NAME
from .model import Item


def entity_id(item: Item, position: int = 0) -> str:
    """Return a bare ``iac.x.y.z`` identifier for *item*.

    Resolution chain (returns the FIRST match):

    1. ``name=`` value when it starts with ``iac.``.
    2. First ``iac.*`` token inside ``comment=`` (after stripping quotes).
    3. **Synthetic** derivation:

       - Singleton menu -> ``iac.<menu-dotted>``
         (e.g. ``/system/clock`` -> ``iac.system.clock``).
       - ``set [find KEY=VAL]`` -> ``iac.<menu-dotted>.<val>``
         (covers ``[find default-name=ether1]`` -> ``iac.interface.ethernet.ether1``,
         ``[find name=admin]`` -> ``iac.user.admin``, etc.).
       - ``set <token> ...`` positional selector
         -> ``iac.<menu-dotted>.<token>`` (e.g. ``set telnet ...``
         -> ``iac.ip.service.telnet``). The parser surfaces the
         positional token as ``__selector__`` so this falls under the
         same branch as ``[find ...]``.
       - ``default-name=etherN`` (no ``__selector__``)
         -> ``iac.<menu-dotted>.<default-name>``.
       - Free-standing ``name=foo`` (not iac-prefixed)
         -> ``iac.<menu-dotted>.<name>``.
       - Ordered menus with no other id -> ``iac.<menu-dotted>.<position>``.
       - Last resort -> ``iac.<menu-dotted>.@<position>``.

    *position* is the item's 0-based index within its menu and is only
    consulted by branches that fall through to positional ids. Pass 0 if
    you don't have a position handy and don't expect to hit those
    branches.
    """
    # 1. user-set name= with iac prefix
    name = item.props.get("name", "").strip().strip('"')
    if name.startswith(IAC_PREFIX):
        return name

    # 2. iac.* token inside comment=
    comment = _unquote(item.props.get("comment", ""))
    for token in comment.replace(",", " ").split():
        if token.startswith(IAC_PREFIX):
            return token.rstrip(".,;:")

    # 3. synthetic derivation
    base = _menu_to_dotted(item.menu)

    # 3a. Singleton -> iac.<menu>
    if item.menu in MENUS_SINGLETON:
        return f"{IAC_PREFIX}{base}"

    # 3b. set [find ...] selector
    selector = item.props.get("__selector__", "")
    selector_tag = _selector_tag(selector)
    if selector_tag:
        return f"{IAC_PREFIX}{base}.{selector_tag}"

    # 3c. default-name (built-in hardware not yet renamed)
    default_name = item.props.get("default-name", "")
    if default_name:
        return f"{IAC_PREFIX}{base}.{default_name}"

    # 3d. free-standing name= (not iac-prefixed)
    if name:
        return f"{IAC_PREFIX}{base}.{name}"

    # 3e. ordered menu position
    if item.menu in MENUS_ORDERED:
        return f"{IAC_PREFIX}{base}.{position}"

    # 3f. last resort
    return f"{IAC_PREFIX}{base}.@{position}"

def is_synthetic(item: Item, position: int = 0) -> bool:
    """True if :func:`entity_id` would derive the id rather than read it.

    Returns True precisely when the item carries no usable iac-namespace
    identifier in its own ``name=`` or ``comment=`` -- meaning the id we
    use for diff matching is one we synthesised from menu + selector.

    The *position* argument exists only to mirror :func:`entity_id`'s
    signature so callers can use them interchangeably; it is not
    consulted because the synthetic-vs-authored decision doesn't depend
    on row ordering.
    """
    del position  # signature parity with entity_id; not used here
    name = item.props.get("name", "").strip().strip('"')
    if name.startswith(IAC_PREFIX):
        return False
    comment = _unquote(item.props.get("comment", ""))
    for token in comment.replace(",", " ").split():
        if token.startswith(IAC_PREFIX):
            return False
    return True


# --- internals --------------------------------------------------------------


def _menu_to_dotted(menu: str) -> str:
    """``/ip/firewall/filter`` -> ``ip.firewall.filter``."""
    return menu.lstrip("/").replace("/", ".")


def _unquote(value: str) -> str:
    """Strip surrounding ``"..."`` if present."""
    if len(value) >= 2 and value[0] == '"' == value[-1]:
        return value[1:-1]
    return value


def _selector_tag(selector: str) -> str:
    """Extract a stable tag from a ``set`` row's ``__selector__`` field.

    Examples
    --------
    - ``[find default-name=ether1]``   -> ``ether1``
    - ``[find name=admin]``            -> ``admin``
    - ``telnet`` (positional)          -> ``telnet``
    - ``""``                           -> ``""``
    """
    selector = selector.strip()
    if not selector:
        return ""
    if not selector.startswith("["):
        # Positional selector like `telnet` in `set telnet disabled=yes`.
        return selector
    inner = selector.strip("[]").strip()
    # `find KEY=VAL` -- take VAL.
    if inner.startswith("find"):
        rest = inner[len("find") :].strip()
        if "=" in rest:
            _, _, val = rest.partition("=")
            return _unquote(val.strip())
        return rest
    return inner
