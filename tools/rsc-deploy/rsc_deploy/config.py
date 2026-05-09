"""Connection settings + minimal ``.env`` parser.

We deliberately avoid python-dotenv to keep the dependency surface small.
The parser supports the common subset:
  - ``KEY=value``
  - ``KEY="value with spaces"`` and ``KEY='value'``
  - ``# comment`` lines and inline ``# comments`` after a value
  - blank lines
  - leading/trailing whitespace
It does NOT support: variable interpolation, multi-line values, ``export``
prefixes, escaped quotes inside values.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class EnvError(Exception):
    """Raised on malformed .env or missing required keys."""


@dataclass(frozen=True)
class Settings:
    host: str
    user: str
    password: str
    port: int = 22
    timeout: float = 10.0


REQUIRED_KEYS = ("ROUTER_HOST", "ROUTER_USER", "ROUTER_PASSWORD")


def load_env(path: str | Path) -> Settings:
    """Parse *path* into a :class:`Settings`. Raises :class:`EnvError` on
    missing file, parse failure, or missing required key.
    """
    p = Path(path)
    if not p.is_file():
        raise EnvError(f".env not found: {p}")

    pairs = parse_env_text(p.read_text(encoding="utf-8"))

    missing = [k for k in REQUIRED_KEYS if k not in pairs]
    if missing:
        raise EnvError(
            f".env missing required key(s): {', '.join(missing)}; "
            f"see .env.example"
        )

    try:
        port = int(pairs.get("ROUTER_PORT", "22"))
    except ValueError as exc:
        raise EnvError(f"ROUTER_PORT not an integer: {pairs['ROUTER_PORT']!r}") from exc
    try:
        timeout = float(pairs.get("ROUTER_TIMEOUT", "10"))
    except ValueError as exc:
        raise EnvError(f"ROUTER_TIMEOUT not a number: {pairs['ROUTER_TIMEOUT']!r}") from exc

    return Settings(
        host=pairs["ROUTER_HOST"],
        user=pairs["ROUTER_USER"],
        password=pairs["ROUTER_PASSWORD"],
        port=port,
        timeout=timeout,
    )


def parse_env_text(text: str) -> dict[str, str]:
    """Parse the body of a .env file into a dict. Last writer wins."""
    out: dict[str, str] = {}
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise EnvError(f"line {lineno}: missing '=': {raw!r}")
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            raise EnvError(f"line {lineno}: empty key: {raw!r}")
        out[key] = _unquote(value.strip())
    return out


def _unquote(value: str) -> str:
    """Strip matching surrounding quotes and trailing inline comments.

    Only strips the comment if value isn't quoted; inside a quoted string we
    leave ``#`` alone.
    """
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    # Bare value: drop trailing inline comment after whitespace + #
    hash_pos = value.find(" #")
    if hash_pos != -1:
        value = value[:hash_pos].rstrip()
    return value
