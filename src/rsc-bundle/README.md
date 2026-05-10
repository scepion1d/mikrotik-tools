# rsc-bundle

Inlines `/import file-name=...` directives in a RouterOS `.rsc`, unrolls `:foreach` over string-literal arrays, resolves `:global` variable assignments into property values, and strips RouterOS scripting wrappers (`:if` / `:foreach` / `$helper` invocations). Produces a single self-contained `.rsc` that matches `/export` style.

## Install

```powershell
.\build.ps1            # uv sync; or run repo-root build.ps1 to also link bin\rsc-bundle.cmd
```

Python ≥ 3.10. Zero runtime deps.

## CLI

```powershell
..\..\bin\rsc-bundle.cmd --mainScript ..\..\rsc\<site>\<site>.rsc --out ..\..\out
# -> out\<site>-YYMMDD-XXXXX.rsc

# keep RouterOS scripting wrappers + unresolved $vars (raw bundle)
..\..\bin\rsc-bundle.cmd --mainScript ... --out ... --no-flatten
```

| Flag | Meaning |
|---|---|
| `--mainScript PATH` | Entry `.rsc`. Imports resolve relative to its parent dir. **Required.** |
| `--out DIR` | Output dir. Filename auto-generated. **Required.** |
| `--no-flatten` | Keep `:global` / `:if` / `:foreach` / `$helper` lines and unresolved `$var` references in output. Default is to flatten so the bundle diffs cleanly against `/export`. |

## Library

```python
from rsc_bundle import bundle_file, flatten

text = bundle_file("rsc/<site>/<site>.rsc")  # raw bundle (imports inlined)
text = flatten(text)                          # post-pass: vars + strip + normalize quoting
```

| Symbol | Purpose |
|---|---|
| `bundle_file(entry)` | Bundle from disk; imports resolve relative to each importing file. |
| `bundle(entry_basename, sources)` | Bundle from `{basename: text}` map (tests). |
| `flatten(text)` | Resolve `:global` vars, strip scripting, normalize KV quoting to `/export` style. |
| `BundleError` | Raised on missing import / cycle / read failure. |

## Caveats

- Only `:global NAME "literal"` assignments are resolved. Computed expressions are left intact.
- Only `:foreach` over `:local` / `:global` arrays of bare string literals is unrolled.
- `/import file-name=$var` lines that survive unfolding are passed through verbatim.
- Quoting normalization assumes `/export` style: `key="bareword"` → `key=bareword` unless the value contains whitespace, brackets, parens, semicolons, or shell-special chars. Empty `key=""` stays quoted.
- Comment lines (`#`) are skipped during var substitution so secrets don't leak into doc-strings that name them.
