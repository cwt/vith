---
type: index
title: "vith Findings Bundle"
description: "OKF v0.2 findings bundle for the vith bug audit: 166 machine-verified findings across 10 audit rounds and 4 addenda (001–154 closed; Round 10 155–166 open)."
status: stable
sources:
  - docs/bugs/
  - docs/
verified: machine-confirmed
stale_after: 2027-05-22T00:00:00Z
tags: [bugs, findings, audit, round-6, round-7, round-8, round-9, round-10]
generated:
  by: "Antigravity (Gemini 4 Neon)"
  at: "2026-11-22T11:00:00Z"
timestamp: "2026-11-22T11:00:00Z"
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
* **Round 5 (2026-11-09, hg rev 256 / `v0.11.1` — [093](093.md)–[109](109.md), `fix_status: fixed`)**: 17 machine-confirmed findings from a fresh full-project re-audit of the *fully-fixed* tree (4 parallel auditors + dynamic probes: 6 000 random command strings, 4 000 random window/tab ops, end-to-end preview/substitute/paste/undo flows). Two documented fixes are **regressed** — [093](093.md) (091 previewer still leaks `ignore_case`) and [102](102.md) (090's lazy-import hoist was never applied) — plus new Vim-semantics defects ([094](094.md)–[096](096.md)), an incomplete [081](081.md) gate fix ([097](097.md)), a renderer state leak ([098](098.md)), tooling/test-quality gaps ([099](099.md)–[101](101.md)), and cleanup inventories ([103](103.md)–[109](109.md)). All 17 findings fixed finding-by-finding across hg rev 258–275 (217 passed / 1 skipped, `mypy .` 56 files clean, ruff clean, Python 3.10 floor intact).
* **Round 6 (2026-11-09, hg rev 283+ — [110](110.md)–[115](115.md), `fix_status: fixed`)**: 6 deep-audit inventories covering wrong-logic bugs ([110](110.md)), robustness and `None`-safety ([111](111.md)), dead code ([112](112.md)), duplicate logic ([113](113.md)), unoptimized algorithms and memory growth ([114](114.md)), and low readability ([115](115.md)). All items across all 6 inventories resolved with regression tests (247 passed, `mypy .` 57 files clean, ruff clean).
* **Round 7 addendum (2026-11-09–10, user field reports — [124](124.md)–[126](126.md), `fix_status: fixed`)**: missing `:next` / `:previous` file cycling ([124](124.md)), a tab-bar click crash from a two-argument mouse handler ([125](125.md)), and `:next` duplicating the tab-bar entry in multi-tab sessions ([126](126.md), 124 follow-up). Each fixed one-by-one with regression tests (279 passed, `mypy .` 57 files clean, ruff clean).
* **Round 8 (2026-11-19–20, hg rev 358 → fixed rev 360–381 — [130](130.md)–[150](150.md), `fix_status: fixed`)**: 17 defects and 4 inventories from a fresh full-project re-audit focused on the post-`v0.14.0` Ex command work. Highlights: `:w %`/`:sav <current>` same-file rejection ([130](130.md)); `:wq`/`ZZ` exiting the app and discarding other windows ([131](131.md)); `:only`/`:tabonly`/`:tabclose` scope and dirty-guard gaps ([132](132.md)); `:e` crashes on read-only/directory buffers ([133](133.md)); mouse-focus dispatch to the wrong buffer ([134](134.md)); `:g` substitute bookkeeping ([135](135.md)); search-buffer Tab ([136](136.md)); non-UTF-8 shell crashes ([137](137.md)); TOML validation/detection ([138](138.md)); Vim `&`/`~` replacement semantics ([139](139.md)); CRLF loss ([140](140.md)); CJK visual-column drift ([141](141.md)); ReDoS guard bypass ([142](142.md)); plus `:normal` range, `:retab`, `:cq`, Thai-gate, and cleanup/perf/readability inventories. All 21 were fixed in the Round 8 fix arc (rev 360–381, one commit per finding or tightly related group), followed by splitting `vith/commands/commands.py` into flat submodules under `vith/commands/` in rev 382.
* **Round 7 (2026-11-09, upstream triage — [116](116.md)–[123](123.md), `fix_status: fixed`)**: 8 worth-fixing upstream `prompt-toolkit/pyvim` issues that still reproduced in `vith`, filed as new findings and fixed one-by-one with regression tests (274 passed, `mypy .` 57 files clean, ruff clean): `ZZ`/`ZQ` ([116](116.md)), `:w!` read-only ([117](117.md)), `>>`/`<<` vs `tabstop` ([118](118.md)), `Ctrl-[` ([119](119.md)), counted `i`/`a` ([120](120.md)), extensionless highlight ([121](121.md)), `*` history ([122](122.md)), `:s` ReDoS guard ([123](123.md)).
* **Round 9 (2026-11-20, deep read-only analysis & re-validation — [151](151.md)–[153](153.md), `fix_status: fixed`)**: residual performance, duplication/logic-drift, and readability inventories against `v0.15.0` (rev 384), all closed with regression tests: [151](151.md) (`splice_buffer_lines`, inactive-tab frame retention, $O(n)$/$O(m\sqrt{n})$ `:g` move/copy, visible-buffer Thai gate decay/re-latch + single-pass SARA-AM scan, cached `:b` regex, `:retab` fast paths); [152](152.md) (medium — catalog-derived `_default_options`/`_CONFIG_OPTION_MAP`, shared `_resolve_editor_buffer`/`apply_int_option_value`, shared 1-based address resolver, unified `resolve_ignore_case`, and unified write-target normalization fixing the `:set ro` symlink bypass & stale dirty flag); [153](153.md) (`vith/lexer.py` top import consolidation, `completer.py` class ordering, and `vith/thai/__init__.py` public surface tightened via PEP 562 `__getattr__`/`__dir__` + `cold_text.py` direct submodule import).
* **Round 9 addendum (2026-11-20, user field report — [154](154.md), `fix_status: fixed`)**: vertical cursor movement (`j`/`k`), `{count}|`, visual-block selection (`<C-v>` highlight, `y`, `d`, `I`, `A`), and block paste (`p`/`P`) drifting after tabs on Thai text (and pure ASCII with tabs), fixed with tab-expanded visual-column math honoring buffer/local/global `tabstop`, visual-column block row iteration, `TextObject.operator_range` `BLOCK` corner fix, and visual-column `|` and `p`/`P`.
* **Round 10 (2026-11-22, deep performance & algorithmic audit — [155](155.md)–[166](166.md), `fix_status: open`)**: 12 machine-verified performance, memory, and algorithmic findings profiled across all 38 modules on Python 3.15.2 / `prompt_toolkit 3.0.53` / `Pygments 2.21.0` — eager 32-lexer startup imports ([155](155.md)), unconditional per-frame `TabsProcessor` fragment explosion ([156](156.md)), per-keystroke $O(\text{file size})$ `Document.current_line` copy & eager `_on_text_changed` line splits ([157](157.md)), quadratic `:join` ([158](158.md)) and `:render` HTML fallback ([159](159.md)), `:substitute` char-by-char callback ([160](160.md)), quadratic `:norm` replay ([161](161.md)), `DocumentLexer` backward-scroll leak & 215-module `guess_lexer` freeze ([162](162.md)), Thai render/Visual-Block hot spots ([163](163.md)), broken `DocumentWordsCompleter` cache key ([164](164.md)), Thai cluster/word/libthai hot spots ([165](165.md)), and secondary I/O/editing/command hot spots ([166](166.md)).

## Round 5 Findings (2026-11-09 full re-audit, hg rev 256 → `fix_status: fixed`)

**Resolution note (2026-11-09):** all 17 open Round 5 findings are resolved with one commit per finding
(or tightly related group): 093 (:set ic preview leak), 094 (:s flags i/I/E488), 095 (:q window-scoping),
096 (:e unsaved changes guard), 097 (paste/undo Thai gate latch), 098 (re-entrant escape restore),
099 (cold-text bench against vith.cold_text), 100 (speed plan benchmark refresh), 101 (test assertions/inspect),
102 (cold-text thai import hoist), 103 (misc low defects), 104 (docs/config drift), 105 (build/dist cleanup),
106 (dead code sweep), 107 (duplicate logic dedup), 108 (readability/type annotations), 109 (hot-path perf residuals).
Gates: **217 passed, 1 skipped**, `mypy .` 56 files clean, ruff clean.

### Defects (`type: bug`, `fix_status: fixed`)

| ID | Finding | Area | Priority |
| :--- | :--- | :--- | :--- |
| [093](093.md) | `:set ic` × Esc still leaks `ignore_case` — `save()`/`restore()` omit the 16th catalog toggle (091 regression) | commands/previewer | **high** |
| [094](094.md) | `:s` flags `i`/`I`/`c`/`n`/`p` accepted by the grammar but ignored (only `g` honoured) | commands/substitute | medium |
| [095](095.md) | `:q` blocked by a dirty buffer in another window (067 residual; contradicts its own comment) | commands/quit | medium |
| [096](096.md) | `:e <location>` discards unsaved changes and drops its `force` argument (073 regression) | commands/edit | medium |
| [097](097.md) | Thai renderer gate not latched for multi-line paste / undo / generic text replacement (081 incomplete) | thai/gate | medium |
| [098](098.md) | Re-entrant renderer early-return leaks `_AUTOWRAP_ARM_TAG` into `zero_width_escapes` | thai/renderer | medium |
| [099](099.md) | `tools/bench_quez_cold_text.py` benchmarks a private copy, not shipped `vith.cold_text` (008 regression) | tools/benchmark | medium |
| [100](100.md) | `docs/speed_latency_plan.md` "reproducible" output is stale (`cold_eager=0.44 ms` vs actual ~15.3 ms) | docs/benchmark | medium |
| [101](101.md) | Regression tests that cannot fail on their bug (vacuous `or`, source grep, missing assert, brittle constant) | tests | medium |
| [102](102.md) | 090 item-3 lazy-import hoist missing: `cold_text.py:217` still imports per `j`/`k` | cold-text/perf | low |
| [103](103.md) | Misc low defects: unguarded `current_editor_buffer`, vithrc F9 closure, style containment, unconditional trailing newline | misc | low |
| [104](104.md) | Docs/config drift & dead references (`>=2.0.0` floor, `setup.py`, `requirements-dev.txt`, stale counts, `.gitignore`) | docs/config | low |
| [105](105.md) | `build/lib/vith/` + `dist/` duplicate the full source tree despite the 074 deletion record | hygiene | low |

### Improvements (`type: improvement`, `fix_status: fixed`)

| ID | Finding | Category | Priority |
| :-- | :--- | :--- | :--- |
| [106](106.md) | Dead-code inventory: unreachable `_unwrap_command` branch, duplicate signature block, unused `width`, dead re-exports, `is_sara_am`, write-only `text_version` | dead code | low |
| [107](107.md) | Duplicate logic: 4× `translate_index_to_position`, `if` after returning `elif`, duplicated mutual-exclusion guard, three untitled-buffer labels | duplication | low |
| [108](108.md) | Readability: misleading `is_thai_base` docstring, misnamed `measure_ns`, implicit `None` return, `[No Name]` vs `[New file]` | readability | low |
| [109](109.md) | Perf residuals: stale completer cache key, partial 072 overlay span fix, per-frame sanitizer set/sort allocations | performance | low |

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

## Round 6 Findings (2026-11-09 deep audit → `fix_status: fixed`)

**Resolution note (2026-11-09):** all 6 Round 6 inventories ([110](110.md)–[115](115.md)) are 100% resolved and machine-verified (`fix_status: fixed`, `verified: machine-confirmed`) with regression tests across `tests/`. Gates: **247 passed**, `mypy .` 57 files clean, ruff clean.

### Defects (`type: bug`, `fix_status: fixed`)

| ID | Finding | Area | Priority |
| :--- | :--- | :--- | :--- |
| [110](110.md) | Bugs and wrong-logic inventory: substitute identical replacement, `:wqa!`, empty command, pyflakes syntax errors, invalid UTF-8 column math, cold-text undo/reset, `HttpIO.exists()`, editor_buffer encoding mutation, Thai renderer KeyError risks | commands/reporting/cold-text/io/thai | medium |
| [111](111.md) | Robustness and None-safety inventory: window/tab/buffer guards, command/handler dereferences, completer weakrefs, lexer syntax sync, Thai cluster snapping, key-binding `event.arg` | window-arrangement/commands/completion/lexer/thai/key-bindings | medium |

### Improvements (`type: improvement`, `fix_status: fixed`)

| ID | Finding | Category | Priority |
| :--- | :--- | :--- | :--- |
| [112](112.md) | Dead-code inventory: dead guards, redundant token loops, upstream-parity dead branch, compat aliases, commented grammar, obsolete base class, stale build modules | dead code | low |
| [113](113.md) | Duplicate-logic inventory: hsplit/vsplit, buffer navigation, visual-block rectangle, vertical cursor ordinals, pager waits, I/O expanduser, lexer caching, preview signature bookkeeping | duplication | low |
| [114](114.md) | Unoptimized algorithms/memory-growth inventory: unbounded style cache, unbounded `cell_starts`, Thai word-break rescan, SARA-AM loop, copy-body row scan, O(n) active buffer lookup, tab handler cache, split-tree rebuild | performance/memory | low |
| [115](115.md) | Low-readability inventory: patches monolith, constant ordering, mutable-list flag, layout node fallback, implicit None, misleading encoding names, Liskov write mismatch, Path rejection, default encoding, EOFError, process-name slicing, legacy config comparison, TOCTOU, late init, backend lookup, missing annotations, style validation, tilde registration, condition placement, cache attachment | readability | low |

## Round 7 Addendum Findings (2026-11-09–12 field reports & improvements → `fix_status: fixed`)

**Resolution note (2026-11-09–12):** six user-reported field bugs and improvements fixed one-by-one with regression tests (one commit per item). Gates: **338 passed**, `mypy .` 61 files clean, ruff clean. All 129 pre-Round-8 bundle findings are closed; Round 8 adds 21 open findings.

### Defects & Improvements (`fix_status: fixed`)

| ID | Finding | Area | Priority |
| :--- | :--- | :--- | :--- |
| [124](124.md) | Missing `:next` / `:previous` file cycling (user field report) | commands/navigation | medium |
| [125](125.md) | Mouse click on a document tab crashes the editor (two-argument handler) | layout/mouse | high |
| [126](126.md) | `:next` duplicates the tab-bar entry instead of switching tabs (124 follow-up) | commands/tabs | medium |
| [127](127.md) | Tab stop position calculation ignores character display widths (user field report) | thai/layout | high |
| [128](128.md) | Per-filetype standard tab stops and buffer-local tabstop options | editor/layout/commands | medium |
| [129](129.md) | TOML configuration format with modular hook registration | config/rc | medium |

## Round 7 Findings (2026-11-09 upstream triage → `fix_status: fixed`)

**Resolution note (2026-11-09):** all 8 Round 7 findings fixed one-by-one with regression tests (one commit per finding, `119`+`120` grouped as tightly-related insert-mode work). Gates: **274 passed**, `mypy .` 57 files clean, ruff clean. All 123 bundle findings are now closed.

### Defects (`type: bug`, `fix_status: fixed`)

| ID | Finding | Area | Priority |
| :--- | :--- | :--- | :--- |
| [116](116.md) | Missing `ZZ` / `ZQ` navigation-mode quit (upstream #122 / #19) | key-bindings/commands | low |
| [117](117.md) | `:w!` does not force-write read-only files (upstream #77) | commands/io | medium |
| [118](118.md) | `>>` / `<<` ignore `:set tabstop` (upstream #102) | key-bindings/indent | medium |
| [119](119.md) | `Ctrl-[` does not exit insert mode (upstream #134) | key-bindings | low |
| [120](120.md) | Count before `i` / `a` ignored, e.g. `5itest<Esc>` (upstream #133) | key-bindings | low |
| [121](121.md) | Extensionless Python files get no highlighting (upstream #74) | lexer | low |
| [122](122.md) | `*` search does not enter search history (upstream #89) | search/history | low |
| [123](123.md) | `:s` regex has no catastrophic-backtracking guard (upstream #159, CWE-1333) | commands/security | medium |

## Round 8 Findings (2026-11-19 full re-audit, hg rev 358 → `fix_status: fixed`)

**Resolution note:** all 21 Round 8 findings (130–150) were fixed one-by-one with
regression tests across hg rev 360–381, each verified with `scripts/runtest.sh`,
`type-check.sh`, `lint-check-and-fix.sh` and `code-format.sh`. Final gates:
**466 passed**, `mypy .` clean (69 files at rev 381; 79 files after splitting
`vith/commands/commands.py` into flat submodules in rev 382), ruff clean. The
write/quit/tab families were deduplicated via shared helpers; shell I/O, TOML
validation, fileformat, display-width and renderer gate paths were hardened; the
four inventories were resolved with targeted cleanups, cache fixes and
documentation updates.

**Scope and method:** a fresh read-only full-project re-audit after the `v0.14.0`
Ex-command work (hg rev 358 `ff583fcf26ba`, 424 tests passing). Source, tests,
tools, examples, and docs were inspected; every finding below was reproduced
with a focused probe (Python-level command dispatch, key-processor feeds, or a
`vim -Nu NONE -n -es` comparison). No source files were changed by this audit.

### Defects (`type: bug`, `fix_status: fixed`)

| ID | Finding | Area | Priority |
| :--- | :--- | :--- | :--- |
| [130](130.md) | `:w %` / `:w <current>` / `:wq %` / `:up %` / `:x %` / `:sav <current>` rejected by the overwrite guard | commands/write | **high** |
| [131](131.md) | `:wq`/`ZZ` exit the app and discard other windows; `ZZ` is not `:x`; `:q` over-blocks shared dirty buffers | commands/quit | **high** |
| [132](132.md) | `:only` closes other tab pages; `:tabonly`/`:tabclose` ignore dirty buffers and `!` | commands/tabs | medium |
| [133](133.md) | `:e`/`:e!` crash with `EditReadOnlyBuffer` on read-only/directory buffers; reload keeps stale pyflakes state | commands/edit | **high** |
| [134](134.md) | Mouse focus does not update `WindowArrangement.active_window`; Ex commands mutate the wrong buffer | layout/windows | **high** |
| [135](135.md) | `:g/.../s/.../` loses last-search/last-replacement bookkeeping; bare `:s` uses the global pattern | commands/global | medium |
| [136](136.md) | Tab in the `/` search buffer inserts indentation into the search pattern | key-bindings/search | medium |
| [137](137.md) | Non-UTF-8 shell output crashes `:r !`, `:[range]!`, and `:w !` | commands/shell | medium |
| [138](138.md) | TOML config bypasses type/bounds validation; `.vithrc` detection ignores top-level `colorscheme`/`colorcolumn` | config/toml | medium |
| [139](139.md) | `:s` replacement uses Python semantics (`&`, `\&`, `\n`) instead of Vim `&`/`~`/`\r` | commands/substitute | medium |
| [140](140.md) | CRLF files are silently saved as LF (no `fileformat` tracking) | io/editor-buffer | medium |
| [141](141.md) | Double-width CJK/emoji break `j`/`k` visual columns and visual-block rectangles | thai/unicode/layout | medium |
| [142](142.md) | ReDoS guard misses overlapping alternations/counted quantifiers (`(a|aa)+$`) | commands/security | medium |
| [143](143.md) | `:[range]normal` skips rows after line-count changes instead of clamping | commands/normal | low |
| [144](144.md) | `:retab` without `!` rewrites leading spaces when `noexpandtab` | commands/retab | low |
| [145](145.md) | `:cq` exits with status 0 despite the documented error code | commands/cli | low |
| [146](146.md) | Thai gate is not latched by `show_message`, so Thai toolbar text can skip SARA-AM sanitization | thai/renderer | low |

### Improvements & inventories (`type: improvement`, `fix_status: fixed`)

| ID | Finding | Category | Priority |
| :-- | :--- | :--- | :--- |
| [147](147.md) | Low-severity Vim-semantics deviations (`:colorscheme`, `:f`, `:w >>`, register args, `:setlocal`, smartcase) | wrong logic | low |
| [148](148.md) | Dead-code and duplicate-logic inventory (write guards, substitute pipelines, option catalogs, backend lookup, cleanups) | dead code/duplication | low |
| [149](149.md) | Unoptimized algorithms/state loss (`:g` move/copy O(m·n), full-document rebuilds, cache keys, tab frames) | performance/memory | low |
| [150](150.md) | Readability, missing regression tests, and help/docs drift (3,213-line `commands.py`, stale docstrings, bundle counts) | readability/docs | low |

## Round 9 Findings (2026-11-20 deep read-only analysis, re-validated at `v0.15.0` / rev 384)

**Validation note (2026-11-20):** all 15 sub-items across [151](151.md)–[153](153.md) were machine re-validated against source and live probes: fixed 5 stale line ranges, confirmed tab-switch scroll/lexer loss and $O(m^2 + m \cdot n)$ `:g` move/copy scaling, elevated [152](152.md) to `medium` after confirming a `:set ro` bypass + stale dirty flag on symlink `:w`, re-scoped `replace_buffer_lines` / `_retab_line` / `shiftwidth` / `should_ignore_case` / Thai-gate decay caveats, and re-attributed the split module-level import block in [153](153.md) from `preview.py` to `vith/lexer.py:1-23`.

### Improvements (`type: improvement`)

| ID | Finding | Category | Priority | Status |
| :-- | :--- | :--- | :--- | :--- |
| [151](151.md) | Unoptimized-algorithm inventory: inactive-tab frame/scroll/lexer loss, quadratic `:g` move/copy, constant-factor range rebuilds, SARA-AM scans, negligible `:b` regex compile, `:retab` import/ASCII scans | performance/memory | low | fixed |
| [152](152.md) | Duplicated-code and logic-drift inventory: hand-maintained defaults/config maps, weakref/int-setter duplication, line-address parsing, drifted `should_ignore_case` fallback, split write-target normalization (`:set ro` bypass + stale dirty flag on symlink `:w`) | duplication/logic | medium | fixed |
| [153](153.md) | Readability and import-organization inventory: split import block in `vith/lexer.py:1-23`, completer declaration ordering, deferred Thai `_private` surface | readability | low | fixed |

## Round 9 Addendum (2026-11-20, user field report)

### Defects (`type: bug`, `fix_status: fixed`)

| ID | Finding | Area | Priority |
| :--- | :--- | :--- | :--- |
| [154](154.md) | Vertical cursor motion (`j`/`k`), `{count}\|`, visual blocks (`<C-v>` highlight/`y`/`d`/`I`/`A`), and block paste (`p`/`P`) drift after tabs on Thai text (and ignore custom `tabstop`) | thai/tabs/selection | **high** |

## Round 10 Findings (2026-11-22 deep performance & algorithmic audit, `v0.15.1` / rev 392)

### Defects & Performance Improvements (`fix_status: open`)

| ID | Finding | Category | Priority | Status |
| :-- | :--- | :--- | :--- | :--- |
| [155](155.md) | Startup `FiletypeTabstops(DEFAULT_FILETYPE_TABSTOPS)` eagerly imports 32 Pygments lexer modules (187.8 ms / 63% of cold startup) | startup/pygments | **high** | fixed |
| [156](156.md) | Per-frame `TabsProcessor` unconditionally explodes fragments and scans every character on tab-free lines (36.3% of ASCII frame) | rendering/tabs | **high** | fixed |
| [157](157.md) | Per-keystroke $O(\text{file size})$ copy via `Document.current_line` (even in Insert mode) and eager `Document.lines` split in `_on_text_changed` | keystroke/latency | **high** | fixed |
| [158](158.md) | Quadratic $O(N^2)$ string concatenation in `:j` / `:join` (258.7 ms at 20k lines) | commands/algorithms | **high** | open |
| [159](159.md) | Quadratic $O(K^2)$ whole-document join in `:render` HTML fallback `_Renderer._ensure_block` (97.6 ms at 4k `<p>` tags) | commands/render | **high** | open |
| [160](160.md) | `:substitute` runs a per-match char-by-char Python callback even for literals and plain backreferences (428 ms vs 11.3 ms) | commands/substitute | medium | open |
| [161](161.md) | Quadratic $O(R \cdot N)$ `:norm` line replay and uncached `KeyProcessor` rebuild (478 ms on 2k lines) | commands/normal | medium | open |
| [162](162.md) | `DocumentLexer` unbounded `line_generators` leak (+18.4 MB on 642 KB file), out-of-range EOF drain, and 215-module `guess_lexer` freeze | lexer/memory | medium | open |
| [163](163.md) | Thai per-frame render overhead in `_sanitize_and_invalidate_sara_am`, `_patched_copy_body`, and Visual Block `_thai_selection_range_at_line` | thai/rendering | medium | open |
| [164](164.md) | `DocumentWordsCompleter` reads nonexistent `editor_buffer._text_version` (stale in prod) and breaks P3 if wired to `_text_version` | completion/caching | medium | open |
| [165](165.md) | Thai cluster & word-motion hot spots: `_compute_cell_columns` slices/generators, linear word-span scans, and `libthai` ctypes `*gen` | thai/algorithms | low | open |
| [166](166.md) | Secondary I/O, editing, and command hot spots (`>>` splice, `:w >>` append, `_read` CR scan, `:sort nu`, `DirectoryIO` scandir, static `:g/pat/m N`, completer double `get_all_lexers`) | io/commands/editing | low | open |

## Meta

* [Bundle Update Log](log.md) — chronological log of this bundle.
* Root bundle: [`../index.md`](../index.md).

## Name-mapping note

This bundle was written under the `pyvim` name. Since the 2026-11 rebrand, all live code paths are `vith/…`; the `CHANGELOG` appendix preserves the `pyvim` history verbatim, and the `LICENSE` carries the stacked copyright (Slenders 2015 + Suttipongsakul 2026).
