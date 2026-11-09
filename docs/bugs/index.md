---
type: index
title: "vith Findings Bundle"
description: "OKF v0.2 findings bundle for the vith bug audit: 92 machine-verified findings across Round 1 (hg rev 164, 2026-11-06, 001–037, closed), Round 2 (hg rev 191–233, 2026-11-07, 038–062, fixed), Round 3 (hg rev 87de3a3, 2026-11-08, 063–075, fixed in rev 253–265), Round 3 addendum (2026-11-08, 076–079, fixed in rev 267–269), and Round 4 addendum (2026-11-08–2026-11-09, 080–092, fixed in rev 270+)."
status: stable
sources:
  - docs/bugs/
  - docs/
verified: machine-confirmed
stale_after: 2027-01-01T00:00:00Z
tags: [bugs, findings, audit]
generated:
  by: "OpenCode (Crouton Alpha)"
  at: "2026-11-08T19:20:00Z"
timestamp: "2026-11-08T19:20:00Z"
---

# vith Findings Bundle

This bundle tracks machine-verified audit findings for `vith` (`vith/`,
`tests/`, `tools/`, `pyproject.toml`, and verification of the implemented plans in
[`../thai_support_plan.md`](../thai_support_plan.md) /
[`../speed_latency_plan.md`](../speed_latency_plan.md)):

* **Round 1 (2026-11-06, hg rev 164 — [001](001.md)–[037](037.md), `fix_status: fixed`)**: 16 defects and 21 improvements verified against `prompt_toolkit 3.0.53`, live micro-probes, the test suite (68 passed), `mypy .` (40 files clean), and `tools/bench_plans.py`. Closed in hg rev 167–195 (100 tests, 43-file mypy baseline at the 3.10 floor, ruff clean).
* **Round 2 (2026-11-07, hg rev 191–233 — [038](038.md)–[062](062.md), `fix_status: fixed`)**: 18 defects ([038](038.md)–[054](054.md), [062](062.md)) and 7 improvements ([055](055.md)–[061](061.md)). All fixed in hg rev 194–233.
* **Round 3 (2026-11-08, hg rev 87de3a3 — [063](063.md)–[075](075.md), `fix_status: fixed`)**: 9 defects ([063](063.md)–[071](071.md)) and 4 improvements ([072](072.md)–[075](075.md)). All fixed in hg rev 253–265 (fix arc of 2026-11-08, one commit per finding or tightly-bound pairing, 179 tests passing, `mypy` 32 files clean, `ruff` clean). Originally filed as 18 findings (063–080) at hg rev 251 and re-validated/merged/renumbered on 2026-11-08 — see the [renumbering note](#renumbering-note-2026-11-08).
* **Round 3 addendum (2026-11-08, user-reported field bugs after the fix arc — [076](076.md)–[079](079.md))**: [076](076.md) — visual highlight + `j` crash (absolute/line-relative mix in the `selection_range_at_line` wrapper, fixed rev 267); [077](077.md) — visual block rectangle counts combining marks (cluster-ordinal rect, fixed rev 268); [078](078.md) + [079](079.md) — extrapolation audit of the same selection/cursor stack (fixed rev 269): `j`/`k` visual-column drift over Thai rows (ordinal-based `preferred_column` in `ColdTextBuffer`) and visual-block `I`/`A` anchors splitting clusters (block rectangle in `selection_ranges`).
* **Round 4 addendum — stem-scan audit (2026-11-08–2026-11-09, hg rev 269 → fixed rev 270+)**: 13 machine-confirmed findings from re-scanning the whole project against all prior report classes ([080](080.md)–[092](092.md)). User-visible: 080 `:b <pattern>` crash with untitled buffers (HIGH); 084 block-`d` cut vs highlight corner skew; 086 mixed-script cluster slicing in word spans; 088 `:e` on a vanished file wiping the saved buffer; 091 previewer abort leaking every uncovered `:set` option; 092 visual-block overshoots on lines with SARA AM / fewer combining marks; 083/087 command-grammar/pager nits. Correctness: 081 gate-latch holes on mass edits (substitute), 082 gate-decay non-text coverage (Thai names/banners), 085 charwise highlight-cluster parity. Perf/hygiene: 090 (`cell_starts` LRU, big-line span-cache bypass, `_cursor_vertical` lazy-import hoist, docstring/update chain) and 089 test-cwd pollution.

## Round 4 Findings (2026-11-08 stem-scan, hg rev 269 → fixed in rev 270)

**Resolution note (2026-11-08 stem-scan):** a whole-project re-scan seeded with all prior report
classes (as requested after the 076-079 field fixes) ran 4 parallel auditors + a confirmatory E2E
harness at rev 269, producing **12 machine-confirmed findings** — fixed in one grouped arc at
rev 270 with 53 new tests (198 passing, `mypy` 32 files clean, ruff clean), followed by 092
(visual block SARA AM highlight fix).

### Defects (`type: bug`, `fix_status: fixed`)

| ID | Finding | Area | Priority |
| :--- | :--- | :--- | :--- |
| [080](080.md) | `:b <pattern>` runs `buffer_name in eb.location` without a None guard — crashes the whole TUI whenever the [No Name] buffer exists | commands/buffer | **high** |
| [081](081.md) | Thai renderer gate not latched by `:s` replacements / multi-row paste (the cursor-line-only note misses document-wide edits) | thai/gate | medium |
| [082](082.md) | Thai gate decay ignores Thai `editor.message` and Thai file locations (status bar/overlay) | thai/gate | medium |
| [083](083.md) | `:s` flags grammar: only `` / `/g` accepted (`:s/x/y//g` and multi-letter flags rejected); `:0,1s` raised E19 | commands/substitute | medium |
| [084](084.md) | Visual-block `d` cuts one cell wider than the on-screen highlight (operator cursor decrement mid-cluster) | thai/selection | medium |
| [085](085.md) | Charwise `v` highlight not cluster-extended — highlight ≠ yank/cut at cluster granularity | thai/selection | low |
| [086](086.md) | Word-walker spans slice mixed-script clusters (`Latin base + Thai mark`, generic Mn accents) | thai/words | medium |
| [087](087.md) | `:ls` pager printed 0-based indices (contradicting `:b <n>`) and `None` names | commands/pager | low |
| [088](088.md) | `:e` on a file deleted since it was read silently blanked the saved buffer (data loss) | io/editor-buffer | medium |
| [089](089.md) | `tests/test_cd.py` leaked the process cwd, breaking two later subprocess tests | tests | low |
| [092](092.md) | Visual block (`C-v`) over lines with SARA AM overshoots: `max(end, cell_end)` bleeds codepoint columns | thai/selection | medium |

### Improvements (`type: improvement`, `fix_status: fixed`)

| ID | Finding | Category | Priority |
| :-- | :--- | :--- | :--- |
| [090](090.md) | Perf residuals: `j/k` cell-start re-scans (762 µs/move → LRU), megabyte-line span-cache bypass, `_cursor_vertical` lazy import hoist, docstring chain | performance | low |
| [091](091.md) | Command previewer leaked every catalog option beyond save/restore on abort (`:set paste` × Esc left paste_mode on) | commands/previewer | medium |

## Round 3 Addendum Findings (2026-11-08, user-reported field bugs & extrapolation → fixed in rev 267–269)

**Resolution note (2026-11-08 addendum):** user-reported field bugs after the fix arc ([076](076.md), [077](077.md)) and the follow-up extrapolation audit of the selection/cursor coordinate stack ([078](078.md), [079](079.md)). Fixed across hg rev 267–269 (186 tests passing, mypy 32 files clean, ruff clean).

### Defects (`type: bug`, `fix_status: fixed`)

| ID | Finding | Area | Priority |
| :--- | :--- | :--- | :--- |
| [076](076.md) | Visual highlight crossing rows crashes (`v` + `j`): line-relative ↔ absolute mix-up in `selection_range_at_line` | thai/selection | **high** |
| [077](077.md) | Visual block (`C-v`) over multiple Thai lines is not rectangular: block columns count combining marks | thai/selection | **high** |
| [078](078.md) | `j`/`k` drift visually across Thai rows: `preferred_column` is a codepoint column | thai/cursor | medium |
| [079](079.md) | Visual-block `I`/`A` multi-cursor anchors sit on combining marks — insertions split Thai clusters | thai/selection | medium |

## Round 3 Findings (2026-11-08, hg rev 87de3a3 → fixed in rev 253–265)

**Resolution note (2026-11-08 fix arc):** every finding below was fixed one-by-one with regression tests,
re-verified after each commit (pytest + `mypy vith` + `ruff check/format`). Fix revisions: 063 (253),
064 (254), 065 (255), 066 (256), 067 (257), 068 (258), 069 (259), 070 (260), 071 (261),
072 (262), 073 (263), 074 (264), 075 (265). Final verification: 179 tests, mypy 32 source files
clean at the 3.10 floor, ruff clean, `tools/bench_plans.py` reproduces after the split.

### Defects (`type: bug`, `fix_status: fixed`)

| ID | Finding | Area | Priority |
| :--- | :--- | :--- | :--- |
| [063](063.md) | Cell-cluster motion overrides: `$` drops its count (`3$` stays put) and `e`/`E` pass the whole document to `prev_cell_offset` | key-bindings/word-motion | **high** |
| [064](064.md) | Thai patch machinery: `TextObject.operator_range` escapes the install rollback, and the renderer/window wrappers rely on fragile prompt_toolkit internals (`args[]`, `_CHAR_CACHE`, style substrings, dict order, per-frame `write` swap) | thai/patches | medium |
| [065](065.md) | Legacy `DocumentLexer` fallback rebuilds the lexer every frame (no cache; `ClassNotFound` already handled upstream; branch likely dead in the pinned range) | lexer/pygments | low |
| [066](066.md) | Unguarded None-invariants: empty `TabPage` (`VSplit([])`, `active_window = None`) + `close_buffer` index bookkeeping (`IndexError`), and `current_editor_buffer` → `None` | window-arrangement/editor | medium |
| [067](067.md) | Vim command semantics: `:q`/`:wqa` count buffers instead of windows, and `:b` completer (substring) vs jump (exact) vs indices (0-based) disagree | commands/vim-semantics | medium |
| [068](068.md) | `import vith.__main__` starts the editor (unguarded `run()`) | entry-point | low |
| [069](069.md) | `run_reporter` clears stale errors without `invalidate()`, leaving stale pyflakes highlight | reporting/editor-buffer | low |
| [070](070.md) | Startup and I/O robustness: single-level `mkdir`, bare `assert` on `-p -o`, latin-1 write failure, unknown-location indistinguishability | editor/io/startup | low |
| [071](071.md) | Miscellaneous Vim-semantics/cold-text deviations (`r` count, file-explorer whole-line open, `is_combining_char` multi-char, cold-text undo cursor-restoration defect, private `_search`) | vim-semantics/cold-text | low |

### Improvements & cleanup (`type: improvement`, `fix_status: fixed`)

| ID | Finding | Category | Priority |
| :-- | :--- | :--- | :--- |
| [072](072.md) | Performance residuals: `O(n)` string work per keystroke (dirty-check, completer cache key, `is_thai_base`, `find_spec`) and per-frame allocations after the Thai gate latches (SARA-AM sanitizer, sticky gate, overlay tokens, tab handlers, reload note) | performance | medium |
| [073](073.md) | Duplicate logic: four Thai word-motion row-walks and assorted handler clones (`:set`/preview tables, pager, tmux title, grammar ordering) | duplication | low |
| [074](074.md) | Dead code and stale example: test-only exports, `examples/config/vithrc`, stale `build/`/`dist/` artifacts, legacy imports | dead code | low |
| [075](075.md) | Readability: `thai.py` monolith, import-time patching, decorators that return the wrong thing, remaining `lambda`s | readability | low |

## Round 2 Findings (2026-11-07, hg rev 191 → fixed in rev 194–233)

### Defects (`type: bug`, `fix_status: fixed`)

| ID | Finding | Area | Priority |
| :--- | :--- | :--- | :--- |
| [038](038.md) | `_delete_cell_clusters` raises `UnboundLocalError` on `X` at column 0 or `x` on an empty line | key-bindings/thai | **high** |
| [039](039.md) | `indent_line` (`<C-t>` in Insert mode) raises `AttributeError: 'KeyPressEvent' object has no attribute 'application'` | key-bindings | **high** |
| [040](040.md) | `TabPage._close_window` closes `active_window` instead of target `window` and mutates splits during iteration | window-arrangement | **high** |
| [041](041.md) | `WindowArrangement.close_window` (`:hide`) on the last window of a tab leaves an empty `VSplit` and crashes rendering | window-arrangement/layout | **high** |
| [042](042.md) | Visual mode (`v`) slices Thai/Lao cell clusters in half and ignores cell-cluster motions | thai/visual-mode | **high** |
| [043](043.md) | Inclusive Vi motions (`dge`, `dgE`, `df`, `dt`, `dT`) slice Thai/Lao cell clusters and fuse combining marks | thai/motion | **high** |
| [044](044.md) | `DocumentWordsCompleter` regex (`\w+`) strips Thai/Lao combining marks (`GC=Mn`) and fails to complete Thai words | completion/thai | **high** |
| [045](045.md) | `report_pyflakes` treats `pyflakes` UTF-8 byte `col_offset` as codepoint index and restricts identifiers to ASCII | reporting/unicode | medium |
| [046](046.md) | `ReporterError` lacks `__eq__`, defeating `report_errors != self.report_errors` deduplication in `run_reporter` | reporting/redraw | medium |
| [047](047.md) | `:cd` without an argument raises uncaught `TypeError` and skips `os.path.expanduser` | commands/io | medium |
| [048](048.md) | `substitute` (`:s`) raises uncaught `re.error` on invalid regex or replacement escapes | commands/substitute | medium |
| [049](049.md) | Opening `~` or `~/dir` fails with `IsADirectoryError` because `DirectoryIO` and `FileIO.can_open_location` omit `expanduser` | io/file-explorer | medium |
| [050](050.md) | `EditorBuffer.write` and `:w` mutate `location` before validation, skip `expanduser` on overwrite check, and miss reporter/lexer refresh | io/editor-buffer/lexer | medium |
| [051](051.md) | `:colo <scheme>` is registered in `commands.py` but rejected by `COMMAND_GRAMMAR` and ignored by `CommandPreviewer` | commands/grammar/preview | low |
| [052](052.md) | `BufferListOverlay` checks `location in c.display` (`FormattedText` list, always `False`) and triggers on `:buffers` | layout/completion | low |
| [053](053.md) | `reverse_case_cell` (`~`) overrides `:set tildeop` unconditionally and ignores `event.arg` | key-bindings/vi-mode | low |
| [054](054.md) | Miscellaneous logic defects (`is_thai_base` digits, `close_buffer` tab index, `hsplit`/`vsplit` assert, preview mismatches, `ignorecase`, file explorer, `tabstop`, `_try_char`) | thai/window-arrangement/commands/layout | low |
| [062](062.md) | Thai/Lao upper/lower vowels and tone marks dropped or misplaced at the last column | thai/renderer/wrapping | **high** |

### Improvements & cleanup (`type: improvement`, `fix_status: fixed`)

| ID | Finding | Category | Priority |
| :-- | :--- | :--- | :--- |
| [055](055.md) | `substitute` (`:s`) runs `re.sub` twice per line and rebuilds `Document` on zero matches | performance | medium |
| [056](056.md) | `_ThaiTextGate.note` scans the whole buffer per keystroke on non-ASCII non-Thai files, and word motions check `doc.text.isascii()` | performance | medium |
| [057](057.md) | `DocumentLexer` joins lines to EOF per edit and `_sanitize_and_invalidate_sara_am` inflates sparse `Screen.data_buffer` rows/columns | performance | low |
| [058](058.md) | Dead code inventory (round 2: `_current_window_for_event`, `TABSTOP_DOT`, unreachable `ENCODINGS` fallbacks, `_buffer` param, `ignore_case`, `_gate_fixture`) | dead code | low |
| [059](059.md) | Duplicate code and logic inventory (round 2: duplicated comment block, `ENCODINGS` loop, `FileIO()` instantiation, redundant filter, active buffer lookup) | duplication | low |
| [060](060.md) | Readability and test hygiene inventory (round 2: 1-tuple `super`, `cls` parameter, `bytes` shadowing, docstrings, `__import__`, `conftest` `~/.vith`) | readability/hygiene | low |
| [061](061.md) | Plan and benchmark drift (round 2: corrupted code fence in `thai_support_plan.md`, P2 `bench_plans.py` calculation, stale metadata) | documentation/benchmark | low |

---

## Round 1 Closed Findings (2026-11-06, hg rev 164 → fixed in rev 167–195)

**Resolution note (2026-11-06 fix arc):** every Round 1 finding below has been
fixed and machine-re-verified (hg rev 167–195: 100 tests,
43-file mypy baseline at the 3.10 floor, ruff clean, bench reproducing). Each
document's frontmatter carries `fix_status: fixed (hg rev N)`; the fix-verdict
revisions are also summarized in [`log.md`](log.md).

### Defects (`type: bug`, `fix_status: fixed`)

| ID | Finding | Area | Priority |
| :--- | :--- | :--- | :--- |
| [001](001.md) | Thai/Lao word motions re-segment the whole document per motion key (~640 ms/`w` on 2.4 MB) | thai/motion | **high** |
| [002](002.md) | SARA-AM sanitizer full-screen scan per frame even for ASCII-only content (1.27 ms/frame) | thai/renderer | **high** |
| [003](003.md) | Uncaught `ImportError` on Tab for `.py` buffers without jedi (jedi is optional, flag default-on) | completion | medium |
| [004](004.md) | `:<n>s/…` with a range beyond EOF raises uncaught `IndexError` | commands | medium |
| [005](005.md) | Version-gate rollback incomplete: KeyProcessor patches survive a failed install | thai/patches | medium |
| [006](006.md) | `EditorBuffer.write()` writes via the last backend after "Unknown location" | io | low |
| [007](007.md) | `:wq` exits even when `:w` failed (data-loss risk) | commands | low |
| [008](008.md) | `tools/bench_plans.py` validates duplicated copies, not shipped code (4 drifted re-implementations) | tooling | medium |
| [009](009.md) | Command grammar regex built from an unordered set | commandline | low |
| [010](010.md) | Duplicated `.py` gating (`run_reporter` vs `report`) | reporting | low |
| [011](011.md) | Pyflakes columns are source-space but highlight happens in post-tab fragment space | reporting/layout | medium |
| [012](012.md) | Toolbar shows only `errors[0]` per line | layout | low |
| [013](013.md) | New buffer inserted *before* active; comment says after (`:buffers` numbering) | window-arrangement | low |
| [014](014.md) | Cross-line cluster-walk safety rides on an undocumented `is_combining_char` invariant | thai | low |
| [015](015.md) | `:1,3s/…` cursor targets end-of-range even without substitutions (upstream FIXME) | commands | low |
| [016](016.md) | `loop` bound only on the PTK3 branch in `run_reporter` | reporter | low |

### Improvements & cleanup (`type: improvement`, `fix_status: fixed`)

| ID | Finding | Category | Priority |
| :-- | :--- | :--- | :--- |
| [017](017.md) | `vith/enums.py` empty, never imported | dead code | low |
| [018](018.md) | `PyvimScrollOffsets` unused (ScrollOffsets takes callables) | dead code | low |
| [019](019.md) | `get_terminal_title` dead since its comment-only caller | dead code | low |
| [020](020.md) | `get_editor_buffer_for_buffer_name` dead + would `AttributeError` | dead code | low |
| [021](021.md) | unused import `fragment_list_len` | dead code | low |
| [022](022.md) | write-only attribute `editor.file_explorer` | dead code | low |
| [023](023.md) | dead identical if/return branches (thai `_start_timeout` wrapper) | dead code | low |
| [024](024.md) | unreachable second `find_closest_generator` call | dead code | low |
| [025](025.md) | stale `.travis.yml` (2.7/3.5/3.6, defunct service) | legacy/CI | low |
| [026](026.md) | `vith.egg-info/` debris; repo lacks `.hgignore` | hygiene | low |
| [027](027.md) | `six` + 26× `unicode_literals` py2 legacy weight | legacy | low |
| [028](028.md) | `:set` option map duplicated (commands vs preview) | duplication | low |
| [029](029.md) | near-clone pairs: `e`/`E`, `x`/`X`, cursor up/down, int setters | duplication | low |
| [030](030.md) | extra hot paths: explorer lexer, to-EOF join, jedi introspection, key slices | performance | low |
| [031](031.md) | `mypy.ini python_version = 3.15` vs the 3.10 floor | compatibility | low |
| [032](032.md) | `setup.py` missing `python_requires` / metadata | packaging | low |
| [033](033.md) | 27 lambdas vs the no-lambda coding rule | readability | low |
| [034](034.md) | typo / copy-paste docstring inventory | readability | low |
| [035](035.md) | docs-bundle drift: plan sections, index counts, bench citations | documentation | low |
| [036](036.md) | CHANGELOG/README omit 2026 arc, point upstream | documentation | low |
| [037](037.md) | venv lacks pytest/mypy; claims not reproducible in-repo | toolchain | low |

## Verification note

Python **3.11+ syntax scan**: `None found` — all modules parse under
`ast.parse(feature_version=(3, 10))`; the only floor-related issues are the
configuration/packaging ones above ([031](031.md), [032](032.md)).

## Renumbering note (2026-11-08)

All 18 originally filed Round 3 findings (063–080) were machine re-validated at
hg rev 251 (tip; the code is identical to audit rev `87de3a3` — the rev-251
commit is docs-only — and 134 tests pass): **10 findings confirmed verbatim, 8
partially confirmed** with overstated sub-claims corrected in-place. Same-area
findings were merged and the open set renumbered into contiguous 063–075
(defects first, then improvements, priority order):

| New | Old | Content of the merged doc |
| :-- | :-- | :-- |
| [063](063.md) | 063 + 064 | Cell-cluster motion overrides (`$` count; `e`/`E` offset — mis-landing claim refuted, re-scoped) |
| [064](064.md) | 065 + 080 | Thai patch machinery (rollback gap; fragile internals — late-binding consequence unreachable) |
| [065](065.md) | 066 | Legacy lexer fallback (demoted to low: `ClassNotFound` handled upstream; branch near-dead) |
| [066](066.md) | 068 + 069 | Unguarded None-invariants (empty `TabPage` — strengthened with a traced `IndexError`; `current_editor_buffer` — `<Enter>` pre-guarded) |
| [067](067.md) | 070 + 071 | Quit/`:b` Vim-semantics deviations |
| [068](068.md) | 067 | Unguarded `run()` in `vith/__main__.py` |
| [069](069.md) | 072 | Reporter invalidation |
| [070](070.md) | 073 | Startup/IO robustness lows |
| [071](071.md) | 079 | Misc deviations — the false “cursor-only path never hits” claim was replaced by a verified `update_top_cursor` cursor-restoration defect |
| [072](072.md) | 074 + 075 | Performance residuals (074 row 5 re-scoped: ptk caches `Document.lines`) |
| [073](073.md) | 076 | Duplicate logic (row-1 count corrected to four walkers) |
| [074](074.md) | 077 | Dead code/stale example (attribution fixed; `build`/`dist` are stale, not byte-identical) |
| [075](075.md) | 078 | Readability |

## Meta

* [Bundle Update Log](log.md) — chronological log of this bundle.
* Root bundle: [`../index.md`](../index.md).

## Name-mapping note

This bundle was written under the `pyvim` name. Since the 2026-11 rebrand, all live code paths are `vith/…`; the `CHANGELOG` appendix preserves the `pyvim` history verbatim, and the `LICENSE` carries the stacked copyright (Slenders 2015 + Suttipongsakul 2026).
