"""Per-property normalisation, comparison, and emission.

The differ owns the *structural* algorithm (which menus, which rows,
what op kinds); this module owns *per-property* concerns: stripping
identity / computed / default-valued keys, canonicalising IP-address
text forms, deciding what to emit in a ``set`` op vs a ``reset`` op,
and the lenient/strict knobs.

Public API:

- :data:`IDENTITY_PROPS` -- identity keys never emitted in ``set`` ops.
- :func:`normalise_value` -- single value normalisation
  (quote stripping + IP canonicalisation).
- :func:`normalise_props` -- map-form prop normalisation for equality.
- :func:`emit_props` -- prop dict ready to render in an op.
- :func:`prop_changes` -- diff two prop dicts into ``(changed, removed)``.
"""

from __future__ import annotations

import ipaddress

from .defaults import is_computed, is_default


# Props that should never appear in ``set`` operations because they
# identify the item (changing them = different item) or are read-only.
IDENTITY_PROPS: frozenset[str] = frozenset({"__selector__", "default-name"})


# Boolean values RouterOS recognises. When a property is removed AND the
# old value was one of these, we can express the removal as ``prop=no``
# (the default for these flags) rather than ``reset prop``. Reads more
# idiomatic in patches and avoids any per-menu quirks around what
# ``reset`` accepts.
_BOOLEAN_VALUES: frozenset[str] = frozenset({"yes", "no", "true", "false"})


# "Neutral" property values: when we see an *explicit* prop with one of
# these values on one side and the *same prop missing* on the other side,
# the ``lenient_defaults`` heuristic treats them as equal. The
# assumption is that RouterOS omits a prop from /export iff it equals
# its default, and these tokens are the most common defaults across the
# menu universe.
#
# RISK: if the actual default differs (e.g. some flag defaults to
# ``yes``), this hides real drift. Off by default; opt in via
# ``diff(lenient_defaults=True)`` or ``rsc.diff --lenient``. Prefer
# adding an explicit MENU_DEFAULTS entry once the true default is
# verified.
_NEUTRAL_VALUES: frozenset[str] = frozenset({
    "no", "false", "none", "0", "0s", "",
})


# --- map-form normalisation / emission --------------------------------------


def normalise_props(
    menu: str, props: dict[str, str], *, strict: bool,
) -> dict[str, str | None]:
    """Strip identity, computed (always), and defaults (unless strict).

    Returns the value-normalised dict suitable for equality comparison.
    Properties whose value matches the documented default for this menu
    are mapped to ``None`` (treated as "absent"), so source's explicit
    ``protocol-mode=rstp`` matches export's omission.
    """
    out: dict[str, str | None] = {}
    for k, v in props.items():
        if k in IDENTITY_PROPS:
            continue
        if is_computed(menu, k):
            continue
        normalised = normalise_value(v)
        if not strict and normalised is not None and is_default(menu, k, normalised):
            continue
        out[k] = normalised
    return out


def emit_props(
    menu: str, props: dict[str, str], *, strict: bool,
    identity_key: str | None = None,
) -> dict[str, str]:
    """Strip identity + computed + (unless strict) default-valued props.

    Used when emitting an ``add`` / ``set`` op so the patch doesn't
    restate values that the router would interpret as no-ops anyway.
    Returns the **raw** string values (no quote-stripping) since these
    are written back out.

    *identity_key* (when given) is the diff-op's selector key string
    (e.g. ``"name=admin"``). The matching prop is dropped from the
    output -- it would be redundant with the ``[find ...]`` selector
    the emitter will render, and worse, on built-in rows like
    ``/user admin`` it would render as
    ``set [find name=admin] name=admin password=...`` which RouterOS
    rejects (can't ``set`` an identity field).
    """
    selector_key, selector_val = _split_selector_key(identity_key)

    out: dict[str, str] = {}
    for k, v in props.items():
        if k in IDENTITY_PROPS:
            continue
        if is_computed(menu, k):
            continue
        if k == selector_key and normalise_value(v) == selector_val:
            # Already conveyed by [find KEY=VAL]; emitting it would be
            # a no-op or worse, an attempt to re-set an identity field.
            continue
        if not strict:
            normalised = normalise_value(v)
            if normalised is not None and is_default(menu, k, normalised):
                continue
        out[k] = v
    return out


def prop_changes(
    menu: str,
    old_props: dict[str, str],
    new_props: dict[str, str],
    *,
    strict: bool,
    lenient_defaults: bool = False,
) -> tuple[dict[str, str], list[str]]:
    """Compare prop dicts. Returns ``(changed_or_added, removed)``.

    Identity, computed, and (unless strict) default-valued properties
    are treated as absent on BOTH sides. Removed boolean-typed props
    (where the old value was ``yes``/``no``/``true``/``false``) are
    folded into the changed-set as ``prop=no`` rather than reported as
    removed -- a ``set`` with the default value is more idiomatic and
    avoids ``reset`` edge cases.

    Property values are compared after :func:`normalise_value` so
    authored ``comment="LAN"`` matches export-emitted ``comment=LAN``
    (RouterOS strips quotes when not needed).

    When *lenient_defaults* is True, asymmetric drift where one side
    has ``prop=NEUTRAL`` and the other side is missing ``prop`` is
    suppressed. See :data:`_NEUTRAL_VALUES` for the value set.
    """
    old_norm = normalise_props(menu, old_props, strict=strict)
    new_norm = normalise_props(menu, new_props, strict=strict)

    changed = _changed_props(
        old_norm, new_norm, new_props,
        lenient_defaults=lenient_defaults,
    )
    removed = _removed_props(
        old_norm, new_norm, changed,
        lenient_defaults=lenient_defaults,
    )
    return changed, removed


def _changed_props(
    old_norm: dict[str, str | None],
    new_norm: dict[str, str | None],
    new_raw: dict[str, str],
    *,
    lenient_defaults: bool,
) -> dict[str, str]:
    """Props present in *new_norm* whose value differs from *old_norm*.

    Emits the RAW value from *new_raw* (preserves authored quoting/format).
    """
    changed: dict[str, str] = {}
    for key, new_value in new_norm.items():
        old_value = old_norm.get(key)
        if old_value == new_value:
            continue
        if (
            lenient_defaults
            and old_value is None
            and new_value in _NEUTRAL_VALUES
        ):
            # Candidate has explicit neutral, live is silent -- assume default.
            continue
        changed[key] = new_raw[key]
    return changed


def _removed_props(
    old_norm: dict[str, str | None],
    new_norm: dict[str, str | None],
    changed: dict[str, str],
    *,
    lenient_defaults: bool,
) -> list[str]:
    """Props present in *old_norm* but missing from *new_norm*.

    Boolean removals (yes/no/true/false) are folded into *changed* as
    ``prop=no`` (or ``prop=yes`` if the old value was ``no``/``false``)
    -- the caller passes *changed* in by reference and we mutate it.
    Non-boolean removals are returned in the result list for the
    caller to render as ``reset prop``.
    """
    removed: list[str] = []
    for key in sorted(set(old_norm) - set(new_norm)):
        old_value = old_norm[key]
        if lenient_defaults and old_value in _NEUTRAL_VALUES:
            # Live has explicit neutral, candidate is silent -- assume default.
            continue
        if old_value in _BOOLEAN_VALUES:
            # Fold boolean removal into the `set` op as the default value.
            changed[key] = "no" if old_value in ("yes", "true") else "yes"
        else:
            removed.append(key)
    return removed


def _split_selector_key(
    identity_key: str | None,
) -> tuple[str | None, str | None]:
    """Split ``"name=admin"`` into ``("name", "admin")``.

    Returns ``(None, None)`` for missing keys, positional selectors
    (``@anon=N`` / ``@pos=N``), or anything without an ``=``. Used by
    :func:`emit_props` to detect a prop that's already conveyed by a
    ``[find KEY=VAL]`` selector.
    """
    if not identity_key or "=" not in identity_key or identity_key.startswith("@"):
        return None, None
    key, _, val = identity_key.partition("=")
    return key, val


# --- value-form normalisation (single string) -------------------------------


def normalise_value(value: str | None) -> str | None:
    """Strip surrounding quotes and canonicalise IP addresses.

    Two normalisations:

    1. **Quotes.** RouterOS quotes strings only when needed (whitespace,
       special chars). ``comment="LAN"`` and ``comment=LAN`` are
       equivalent on the router; this makes them equal during diff.
    2. **IP address text form.** Authored ``::ffff:0:0/96`` and
       /export's ``::ffff:0.0.0.0/96`` are the same prefix;
       ``2001:DB8::1`` and ``2001:db8::1`` are the same address. Each
       comma- or dash-separated token is run through :mod:`ipaddress`
       and emitted in the canonical compressed form. Tokens that don't
       parse as an address are left untouched, so port lists
       (``53,67``), interface lists, and arbitrary strings are
       unaffected.
    """
    if value is None:
        return None
    if (
        len(value) >= 2
        and value[0] == value[-1]
        and value[0] in ('"', "'")
    ):
        value = value[1:-1]
    return _canonicalise_addresses(value)


def _canonicalise_addresses(value: str) -> str:
    """Canonicalise any IPv4/IPv6 tokens inside *value*.

    Splits on ``,`` (RouterOS list separator) then on ``-`` (range
    separator). For each leaf token, attempts to parse it as an IP
    interface (with ``/prefix``) or a bare address; on success emits
    the canonical compressed form. Anything that doesn't parse is
    returned unchanged -- this keeps non-address values (ports, names,
    MACs, booleans, comments) untouched.
    """
    # Cheap pre-filter: only strings that contain a `:` or `.` could be
    # an IP at all. Avoids running ipaddress over every short token.
    if ":" not in value and "." not in value:
        return value
    if "," in value:
        return ",".join(_canonicalise_addresses(p) for p in value.split(","))
    if "-" in value:
        a, sep, b = value.partition("-")
        ca = _canonicalise_addresses(a)
        cb = _canonicalise_addresses(b)
        # Only collapse if BOTH sides parsed as addresses; otherwise the
        # `-` wasn't a range separator and we shouldn't have split.
        if ca != a or cb != b or _try_canon_one(a) is not None:
            return f"{ca}{sep}{cb}"
        return value
    canon = _try_canon_one(value)
    return canon if canon is not None else value


def _try_canon_one(token: str) -> str | None:
    """Return the canonical form of a single IP token, or None if not one.

    Uses :class:`ipaddress.ip_interface` for ``addr/prefix`` (preserves
    host bits -- crucial for ``/ip/address`` where ``192.168.1.5/24``
    must NOT be normalised to ``192.168.1.0/24``) and
    :class:`ipaddress.ip_address` for bare addresses.
    """
    try:
        if "/" in token:
            return str(ipaddress.ip_interface(token))
        return str(ipaddress.ip_address(token))
    except ValueError:
        return None
