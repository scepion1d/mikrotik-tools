# rsc-bundle roadmap

Staged plan -- ship MVP first, add capabilities only when actually needed.

## Stage 1 -- MVP (this scaffold)

- [x] Recursive walk of a source root -> basename -> path map
- [x] Bundle entry file by inlining `/import file-name=X.rsc` directives
- [x] Cycle detection (graph DFS with visited set)
- [x] `:foreach` unfolder over `:local` / `:global` string-literal arrays
      so dynamic `/import file-name=$f` becomes resolvable
- [x] Library API: `bundle()` (text-in) + `bundle_file()` (path-in)
- [x] CLI: `python -m rsc_bundle entry --root R [-o out.rsc]`
- [x] Smoke tests on simple / nested / cycle fixtures + unfold unit tests

Limitations accepted at this stage:
- Each file inlined verbatim (comments, blank lines, all preserved)
- No de-dup of repeated `:global X` declarations
- Bundled output keeps runtime checks like `$iacParseCheck` calls; they
  will fail when run from the bundle because the originals aren't on flash

## Stage 2 -- bundle-aware runtime

- [ ] CLI: `--prepend ":global iacBundled true"` to mark bundled output
- [ ] `parse.rsc`: short-circuit `iacParseCheck` when `$iacBundled` is true
- [ ] Same flag exposed to scripts so they can branch on bundled vs modular

## Stage 3 -- quality of life

- [ ] `--strip-comments` -- drop `#`-prefixed lines (smaller output)
- [ ] `--strip-blank` -- collapse multiple blank lines
- [ ] `--header` -- prepend a generated banner with timestamp + sources
- [ ] Per-file section markers in output (`# >>> from FILE`) -- DONE in MVP

## Stage 4 -- safety / correctness

- [ ] De-dup leading `:global X` declarations within a single file's section
- [ ] Detect a missing `:global` reference (helper used before declared)
- [ ] Optional: validate the bundled output by feeding it through
      `rsc-diff --check` against a snapshot

## Stage 5 -- integration

- [ ] Plug into a `tools/rsc-deploy/` upload pipeline (single-file upload
      vs many-file upload)
- [ ] `--watch` mode for iterative dev
