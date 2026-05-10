"""Back-compat shim: re-exports the parser from :mod:`rsc_parser`.

The parser moved out of ``rsc_diff`` into the shared :mod:`rsc_parser`
package. This module keeps the old import paths working -- including the
private lex helpers (``_logical_lines``, ``_take_bracket``,
``_tokenise_kv``) that ``rsc_diff.verify`` reaches into.

Prefer importing from :mod:`rsc_parser` (or :mod:`rsc_parser.parser` for
the privates) directly in new code.
"""

from __future__ import annotations

from rsc_parser import parse_file, parse_text  # noqa: F401
from rsc_parser.parser import (  # noqa: F401
    SCRIPT_DIRECTIVE_RE,
    _consume_item,
    _logical_lines,
    _looks_like_kv,
    _normalise_menu,
    _split_menu_and_rest,
    _take_bracket,
    _take_quoted,
    _tokenise_kv,
)

__all__ = ["parse_file", "parse_text"]
