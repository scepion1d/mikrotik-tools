# rsc-bundle

Bundles a flat RouterOS `.rsc` profile folder (`rsc/<profile>/{secrets.rsc, vars.rsc, NN-*.rsc}`) into one minimal deploy-ready file. Substitutes `:global` vars, strips RouterOS scripting, parses with [`rsc-parser`](../rsc-parser/), and re-emits one operation per line with `comment=` minified to bare `iac.id` tokens.

A *profile* is a complete, named router-config variant for the same physical device (e.g. `basic` = single LAN, `segmented` = LAN + IoT VLAN). One profile folder = one apply-able config.

## Install

```powershell
.\build.ps1            # uv sync (depends on rsc-parser sibling path)
```

Python ≥ 3.10.

## CLI

```text
usage: rsc-bundle [-h] --profile PROFILE [-o OUT] [--keep-comments] [--no-flatten]

Bundle a flat RouterOS .rsc profile folder into one minimal deploy-ready file.
By default, $var references are substituted, scripting wrappers are stripped,
and `comment=` properties are minified to their iac.id tokens.

options:
  -h, --help         show this help message and exit
  --profile PROFILE  profile folder containing the .rsc modules (a named
                     router configuration variant, e.g. rsc/basic or
                     rsc/segmented). Loaded order: secrets.rsc, vars.rsc,
                     then every other *.rsc alphabetically.
  -o, --out OUT      output path. If a directory (or omitted -> ./out/), the
                     filename is auto-generated as
                     <profile>-<yymmdd>-<secs>.rsc. If a file path, used as-is.
  --keep-comments    preserve the full `comment="..."` text verbatim instead
                     of minifying to the bare iac.id token.
  --no-flatten       skip flatten + parse + compact. Emit the raw concatenated
                     source. RouterOS resolves `:global $vars` at /import time.
```

## Library

```python
from rsc_bundle import bundle

text = bundle("rsc/segmented")                       # default pipeline
text = bundle("rsc/segmented", keep_comments=True)
text = bundle("rsc/segmented", flatten_output=False) # raw concat
```

| Symbol                      | Purpose                                                                       |
| --------------------------- | ----------------------------------------------------------------------------- |
| `bundle(dir, **kw)`         | Folder-in, text-out (default pipeline).                                       |
| `flatten(text)`             | Resolve `:global` vars + strip scripting + normalise quoting.                 |
| `load_profile(dir)`         | Enumerate profile files in load order (`secrets`, `vars`, then alpha).        |
| `LoaderError`               | Raised on malformed profile folder.                                           |
| `bundle_file(entry)`        | Legacy: bundle from disk via `/import` inlining.                              |
| `bundle_inline(name, src)`  | Legacy: bundle from in-memory `{name: text}` map (tests).                     |

## Known issues

- Site folder must be **flat** — subdirectories are not traversed.
- Only `:global NAME "literal"` assignments are substituted. Computed expressions are left intact.
- Bracket expressions (`admin-mac=[/interface get …]`) pass through unquoted by design; quoting them would change semantics.
- The compact emitter loses original line wrapping. RouterOS `/import` doesn't care, and `rsc-diff` doesn't either.
- Comments without an `iac.*` token are dropped under default minification — identity must come from `name=` or a `[find …]` selector for those rows.
