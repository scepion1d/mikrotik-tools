# rsc-parser

Library. Parses a RouterOS `.rsc` script into an indexed `Config` of `Item` rows and resolves a stable `iac.<type>.<id>` identifier for every row, including synthetic ids for built-in / id-less rows (`/system/clock`, `/ip/service set telnet`, `/user set [find name=admin]`, default-named `etherN`).

Used by [`rsc-diff`](../rsc-diff/) and [`rsc-bundle`](../rsc-bundle/). No CLI, no runtime deps.

## Quick start

```python
from rsc_parser import parse_file, entity_id

cfg = parse_file("baseline.rsc")
for menu, items in cfg.items_by_menu.items():
    for pos, item in enumerate(items):
        print(menu, entity_id(item, pos))
```

## API

| Symbol                                       | Purpose                                                       |
| -------------------------------------------- | ------------------------------------------------------------- |
| `parse_file(path)` / `parse_text(text)`      | `.rsc` → `Config`                                             |
| `Config` / `Item` / `Op`                     | Dataclasses: parsed config, single row, diff op               |
| `entity_id(item, position)`                  | Bare `iac.x.y` id; synthetic when needed                      |
| `is_synthetic(item, position)`               | True if `entity_id` derived the id rather than read it        |
| `MENUS_WITH_NAME` / `_ORDERED` / `_SINGLETON`| Menu classification sets                                      |

### Identity resolution chain

`entity_id(item, position)` returns the FIRST match:

1. `name=iac.x.y` — user-set, iac-prefixed `name=` field
2. `iac.x.y` token inside `comment="..."`
3. **Synthetic**:
   - singleton (`/system/clock`) → `iac.system.clock`
   - `set [find default-name=ether1]` → `iac.interface.ethernet.ether1`
   - `set telnet …` → `iac.ip.service.telnet`
   - `set [find name=admin]` → `iac.user.admin`
   - free-standing `name=foo` → `iac.<menu>.foo`
   - ordered menu fallback → `iac.<menu>.<pos>`
   - last resort → `iac.<menu>.@<pos>`

## Install

```powershell
.\build.ps1            # uv sync
uv run pytest -q
```

Python ≥ 3.10.

## Known issues

- Variable expansion not interpreted; `$adminPass` is kept as the literal string `$adminPass`. Resolution happens upstream (`rsc-bundle.flatten`).
- Control-flow (`:if`, `:foreach`, `:global`, `:log`) and `/import` lines are silently skipped.
- Quoted values keep their surrounding quotes in `Item.props` so emitters can echo them back verbatim. Comparison code must strip quotes before comparing.
