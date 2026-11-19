---
type: project_priority
title: "Ex Command Completeness Plan for vith"
description: "Phased architecture and implementation plan for missing crucial and popular Vim Ex (:) commands in vith — covering quick-win aliases (:ls, :x, :wa, :noh, :undo/:redo), :read / :saveas / :source, generalized range-editing commands (:d, :y, :pu, :sort, :g/:v, :m/:t, :j, :[range]!cmd), tab/buffer completeness, filename expansion (%/#), and :set ergonomics."
status: draft
sources:
  - ../vith/commands/commands.py
  - ../vith/commands/grammar.py
  - ../vith/commands/handler.py
  - ../vith/commands/completer.py
  - ../vith/commands/preview.py
  - ../vith/commands/lexer.py
  - ../vith/editor.py
  - ../vith/editor_buffer.py
  - ../vith/window_arrangement.py
  - ../vith/cold_text.py
  - ../vith/layout.py
  - ../vith/rc_file.py
  - ../vith/io/backends.py
  - ../vith/help.py
  - ../README.md
  - ../tests/test_commands_grammar.py
  - ../tests/test_write_commands.py
  - ../tests/test_quit_and_goto.py
  - ../tests/test_substitute.py
  - ../tests/test_set_int_options.py
verified: machine-confirmed
stale_after: 2027-05-19T00:00:00Z
tags: [commands, ex-mode, vim-parity, grammar, completer, preview, planning]
generated:
  by: "Antigravity (Gemini 4 Neon)"
  at: "2026-11-19T09:20:00Z"
---

# Ex Command Completeness Plan for `vith`

[Docs root](index.md) · sibling plans: [Thai & Lao support](thai_support_plan.md) · [Speed & latency](speed_latency_plan.md) · [Re-brand](vith_rebrand_plan.md) · [Findings (closed)](bugs/index.md)

This document audits the current Ex (`:`) command surface of `vith` (at Mercurial node `ea41ca1964477da3830b0614c4e61b5b36c46621`, `v0.14.0`) and defines a phased, test-driven plan to implement the missing crucial and popular Vim Ex commands while preserving `vith`'s grammar safety, Thai/Lao renderer invariants, cold-text undo/redo integrity, and Python 3.10+ quality gates.

---

## 1. Current Baseline Audit

### 1.1 Implemented Ex Commands (Verified Snapshot)

Every Ex command currently dispatched by `handle_command()`[^src-handler] is either matched by a dedicated rule in `COMMAND_GRAMMAR`[^src-grammar] or registered in `COMMANDS_TO_HANDLERS`[^src-commands]:

| Category | Currently Implemented | Notable Immediate Gaps |
| :--- | :--- | :--- |
| **Write / Quit** | `:w`, `:write`, `:wq`, `:wqa`, `:q`, `:quit`, `:qa`, `:qall`, `:cq` | Missing `:x`/`:xit`/`:exit`, `:xa`/`:xall`, `:wa`/`:wall`, `:wqall`, `:up`/`:update`, `:sav`/`:saveas`, range/append/pipe `:w`. |
| **File / Config** | `:e`, `:edit`, `:o`, `:open`, `:pwd`, `:cd` | Missing `:r`/`:read` (and `:r !cmd`), `:f`/`:file`, `:so`/`:source`, `%`/`#` filename expansion. |
| **Buffers** | `:b`, `:buffer`, `:bn`, `:bp`, `:n`, `:next`, `:N`, `:prev`, `:previous`, `:badd`, `:buffers`, `:bd`, `:bw` | **`:ls`/`:files` unregistered** despite being documented in `README.md`[^src-readme] and docstrings[^src-commands]; missing `:enew`, `:bf`/`:bl`, `:sb`, `:b #`, `:bd <target>`, long aliases (`:bnext`, `:bprevious`, `:bdelete`, `:bwipeout`). |
| **Windows & Tabs** | `:sp`, `:split`, `:vsp`, `:vsplit`, `:new`, `:vnew`, `:only`, `:hide`, `:tabe`, `:tabedit`, `:tabnew`, `:tabc`, `:tabclose`, `:tabn`, `:tabnext`, `:tabp`, `:tabprevious` | Missing `:clo`/`:close`, `:on`, `:tabo`/`:tabonly`, `:tabfir`/`:tabfirst`, `:tabl`/`:tablast`, `:tabm`/`:tabmove`, `:tabs`. |
| **Editing & History** | `:[range]s` / `:substitute` (with `g`, `i`, `I`, `n`, `p`) | Missing `:u`/`:undo`, `:red`/`:redo`, `:[range]d`, `:[range]y`, `:[range]pu`, `:[range]m`, `:[range]t`/`:co`, `:[range]j`, `:[range]sort`, `:g`/`:v`, `:[range]!cmd`, `:[range]retab`, `:[range]norm`. |
| **Search & Navigation** | `:<digits>` (bare positive line number only) | Missing **`:noh`/`:nohlsearch`** (painful because `highlight_search = True` by default[^src-editor]); missing `:$`, `:+N`/`-N` jumps; missing `:marks`, `:reg`/`:registers`. |
| **Options & Inspection** | `:set`, `:setlocal`, `:setl`, `:colorscheme`, `:colo`, `:h`, `:help`, `:compression`, `:comp`, `:coldtext`, `:!<shell>` | Missing `:set opt!`, `:set invopt`, `:set opt?`, bare `:set`, `:set all`, multi-option `:set`; missing `smartcase`, `shiftwidth`, `readonly`, `autoindent`, `fileencoding`; missing `:messages`/`:mes`, `:ver`/`:version`. |

### 1.2 Architectural Invariants to Preserve

All new commands MUST uphold the invariants established across `vith`'s audit rounds ([`bugs/index.md`](bugs/index.md)):

1. **Grammar Determinism & Non-Collision**[^src-grammar][^src-test-grammar]:
   * Alternations in `COMMAND_GRAMMAR` MUST remain longest-first (`_longest_first`) so longer names (`read`, `bdelete`, `tabonly`, `wqall`) win over shorter prefixes (`r`, `bd`, `tab`, `w`) without parser backtracking.
   * New rules MUST NOT break `GrammarCompleter`[^src-completer], `GrammarLexer`[^src-lexer], or existing non-collision pins (`:set`, `:sp`, `:s1`, `:sub`).
2. **Zero Side Effects During Command Preview**[^src-preview]:
   * `CommandPreviewer.preview()` matches `COMMAND_GRAMMAR` on every command-line keystroke. Only `:colorscheme` and `:set`/`:setlocal` preview live; no mutating command (`:read`, `:delete`, `:undo`, `:!`, etc.) may execute during preview.
3. **Thai & Lao Renderer Gate + Undo Safety**[^src-commands][^src-editor-buffer][^src-cold-text]:
   * Every buffer-mutating Ex command MUST guard `eb.buffer.read_only`, call `buffer.save_to_undo_stack()` before mutating, and call `note_document_text(...)` on newly introduced text so the sticky Thai/Lao renderer gate stays latched ([`bugs/081.md`](bugs/081.md), [`bugs/097.md`](bugs/097.md)).
4. **Command-Line Focus & Empty-Arrangement Safety**[^src-editor][^src-test-quit]:
   * Handlers MUST resolve the target buffer via `editor.window_arrangement.active_editor_buffer` (never `editor.current_editor_buffer`, which is `None` while `command_buffer` is focused) and gracefully no-op or show a message when `eb is None` ([`bugs/110.md`](bugs/110.md), [`bugs/111.md`](bugs/111.md)).
5. **ReDoS Guard for Regex Commands**[^src-commands]:
   * Any new command compiling user regexes (`:global`, `:vglobal`) MUST reuse `_UNSAFE_NESTED_QUANTIFIER` and `_strip_regex_literals()` ([`bugs/123.md`](bugs/123.md)).
6. **Unsaved-Changes & Window-vs-Exit Semantics**[^src-commands][^src-window-arrangement]:
   * Buffer-replacing/closing commands MUST reuse `_confirm_unsaved_changes(editor, force)` and preserve Vim's multi-window `:q` semantics ([`bugs/067.md`](bugs/067.md), [`bugs/095.md`](bugs/095.md), [`bugs/096.md`](bugs/096.md)).

---

## 2. Priority Roadmap Overview

| Phase | Focus | Key Deliverables | Risk / Scope |
| :--- | :--- | :--- | :--- |
| **Phase 1** | Quick wins, missing aliases & everyday staples | `:ls`/`:files`, `:u`/`:undo`, `:red`/`:redo`, `:noh`/`:nohlsearch`, `:x`/`:xa`/`:wa`/`:up`/`:wqall`, `:clo`/`:on`/`:bnext`/`:bprev`/`:bdelete`/`:bwipeout`, `:enew`, `:f`/`:file`, `:ver`/`:version`, `:messages`/`:mes` | Low — minimal grammar impact, mostly delegates to existing subsystems |
| **Phase 2** | `:read`, `:saveas`, `:source` & buffer/tab completeness | `:[range]r`/`:read` (`file` & `!cmd`), `:sav`/`:saveas`, `:so`/`:source`, alternate buffer (`:b #`), `:bf`/`:bl`/`:sb`/`:bd <target>`, `:tabo`/`:tabfir`/`:tabl`/`:tabm`/`:tabs` | Medium — adds file/subprocess insertion and buffer/tab helpers |
| **Phase 3** | Generalized range engine & Ex line-editing commands | Shared `ranges.py`, `:$`/`+N`/`-N` jumps, `:[range]d`, `:[range]y`, `:[range]pu`, `:[range]m`, `:[range]t`/`:co`, `:[range]j`, `:[range]sort`, `:g`/`:v`, `:[range]!cmd`, `:[range]w` (range/append/pipe), `:[range]retab`, `:[range]norm` | Medium–High — unifies Ex range parsing across `grammar.py` & `handler.py` |
| **Phase 4** | Command-line ergonomics, `%`/`#` expansion & `:set` parity | `%`/`#` filename expansion (with `:h`/`:t`/`:r`/`:e`/`:p`), `:set` `?`/`!`/`inv`/bare/`all`/multi-option, `smartcase`/`shiftwidth`/`readonly`/`autoindent`/`fileencoding`, `:reg`, `:marks`, completer & help refresh | Medium — touches `grammar.py`, `preview.py`, `completer.py`, `layout.py` |

---

## 3. Detailed Phase Specifications

### 3.1 Phase 1 — Quick Wins, Missing Aliases & Everyday Staples

#### 3.1.1 Missing Registrations & Aliases

| Command(s) | Target / Behavior | Notes |
| :--- | :--- | :--- |
| `:ls`, `:files` | Alias `buffer_list` alongside `:buffers`[^src-commands] | Fixes drift with `README.md`[^src-readme] and docstrings. |
| `:bnext` | Alias `_bn` (`accepts_force=True`) | Matches Vim full spelling. |
| `:bprevious`, `:bprev` | Alias `_bp` (`accepts_force=True`) | Matches Vim full/short spellings. |
| `:bdelete`, `:bwipeout` | Alias `buffer_wipe` (`accepts_force=True`) | Extended to accept optional target in Phase 2. |
| `:on` | Alias `only` | Standard Vim abbreviation. |
| `:clo`, `:close` | Close current window (`accepts_force=True`) | Checks `_confirm_unsaved_changes` when closing the last window showing a dirty buffer (unless `!`), then calls `window_arrangement.close_window()`. |
| `:wqall` | Alias `write_and_quit_all` (`accepts_force=True`) | Completes `:wqa`/`:wqall` pair. |

#### 3.1.2 Undo, Redo & Search Highlight Reset

* **`:u` / `:undo`** and **`:red` / `:redo`**:
  * Delegate to `eb.buffer.undo()` / `eb.buffer.redo()` on `ColdTextBuffer`[^src-cold-text].
  * Guard `eb is None` and `eb.buffer.read_only` (message: `E21: Cannot make changes, 'modifiable' is off`).
  * Report `Already at oldest change` / `Already at newest change` when the corresponding `ColdTextStack` is empty (or no text delta occurred).
* **`:noh` / `:nohlsearch`**:
  * Add `self._hlsearch_suppressed = False` on `Editor`[^src-editor].
  * Update `search_highlight_enabled()` in `EditorLayout._create_buffer_control()`[^src-layout] to return `self.editor.highlight_search and not self.editor._hlsearch_suppressed`.
  * Clear `_hlsearch_suppressed = False` automatically whenever search text changes, search mode is entered, or `*`/`#`/`n`/`N`/`:s` updates `current_search_state`, and invalidate the app so highlights disappear immediately.
  * Preserves `editor.highlight_search = True` (unlike `:set nohls`, which permanently disables it).

#### 3.1.3 Conditional Write & Exit Commands

* **`:up` / `:update`** (`location_cmd`, `accepts_force=True`):
  * If `location` is given or `eb.has_unsaved_changes` is true, delegates to `write(editor, location, force=force)`; otherwise no-ops cleanly.
* **`:x` / `:xit` / `:exit`** (`location_cmd`, `accepts_force=True`):
  * Calls conditional write (`update` logic); if write fails, aborts without closing/exiting.
  * On success (or clean buffer), calls `quit(editor, all_=False, force=force)` so multi-window semantics match `:q`.
* **`:wa` / `:wall`** (`cmd`, `accepts_force=True`):
  * Iterates `editor.window_arrangement.editor_buffers`; writes every buffer with `eb.has_unsaved_changes` (aborting and reporting on failure, matching `write_and_quit_all`[^src-commands]).
* **`:xa` / `:xall`** (`cmd`, `accepts_force=True`):
  * Alias / shared implementation with `write_and_quit_all` (`accepts_force=True`).

#### 3.1.4 Buffer Creation, Info & Diagnostics

* **`:enew` / `:ene`** (`cmd`, `accepts_force=True`):
  * Checks `_confirm_unsaved_changes(editor, force)`, creates a fresh untitled `EditorBuffer(editor)` and shows it in the *current* window (`window_arrangement.show_editor_buffer()`).
* **`:f` / `:file`** (`location_cmd`, `accepts_force=False`):
  * With no argument: formats `"<display_name>" [Modified][New file] <N> lines --<P>%--` via `editor.show_message()`.
  * With a location argument: updates `eb.location = location`, resets `eb._detected_filetype = None`, refreshes `editor.update_title()`, and shows the updated file status.
* **`:ver` / `:version`** (`cmd`):
  * Displays `vith` version (`importlib.metadata.version('vith')` with fallback), Python version, `prompt_toolkit` version, Thai word-segmentation backend (`libthai` vs orthographic fallback), and default cold-text compressor.
* **`:messages` / `:mes`** (`cmd`):
  * Record non-empty messages in a bounded `collections.deque(maxlen=200)` inside `Editor.show_message()`[^src-editor].
  * `:messages` displays the recorded history (or `No messages` when empty).

---

### 3.2 Phase 2 — `:read`, `:saveas`, `:source` & Buffer/Tab Completeness

#### 3.2.1 `:[range]r` / `:read` (File and Shell Output Insertion)

Syntax supported in `COMMAND_GRAMMAR`[^src-grammar]:

```text
:[addr]r[ead]                 # insert current buffer's file after [addr] (default: '.')
:[addr]r[ead] {file}          # insert {file} after [addr] (supports FileIO, GZipFileIO, HttpIO, ~)
:[addr]r[ead] !{shell_cmd}    # run {shell_cmd} and insert stdout lines after [addr]
```

Behavioral contract:

1. **Address resolution**:
   * Default address is current cursor row (`.`).
   * `:0r` inserts *before* line 1 (index `0`); `:$r` appends after the last line; `:Nr` inserts after 1-based line `N`.
   * Out-of-bounds addresses report `E19: No such line in current buffer` with no buffer mutation.
2. **Read-only guard**:
   * Rejects when `eb.buffer.read_only` is true (`E21: Cannot make changes, 'modifiable' is off`).
3. **File reading (`:r [file]`)**:
   * Resolves backend from `editor.io_backends`[^src-backends] (same pipeline as `EditorBuffer._read`[^src-editor-buffer], without mutating `eb.location`, `eb.is_new`, `eb.isdir`, or `eb.encoding`).
   * Missing file reports `E484: Can't open file {location}`; bare `:r` on an untitled buffer reports `No file name`.
   * Normalizes `\r\n` → `\n` and strips a single trailing `\n` before splitting into inserted lines.
4. **Shell reading (`:r !{cmd}`)**:
   * Runs `subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)` (avoiding interactive terminal hijack while staying safe against hung commands).
   * Inserts decoded `stdout` lines (normalizing `\r\n` → `\n`). If `returncode != 0` and `stderr` is present, surfaces the error message clearly.
5. **Buffer insertion, undo & Thai gate**:
   * Empty-buffer special case: if the buffer is completely empty (`text == ''`), replaces the empty line instead of prepending a spurious blank line (matching Vim's `:r` into an empty buffer).
   * Calls `buffer.save_to_undo_stack()`, assigns `Document(new_text)`, calls `note_document_text(inserted_text)`, moves cursor to the first non-blank character of the first inserted line, and shows `"{source}" {N}L` via `editor.show_message()`.

#### 3.2.2 `:sav` / `:saveas` and `:so` / `:source`

* **`:sav` / `:saveas`** (`location_cmd`, `accepts_force=True`):
  * Requires a location (otherwise `E471: Argument required`).
  * Checks file-exists guard unless `!`, writes buffer to `location`, updates `eb.location` and refreshes filetype/reporter/title.
  * *(See §6 Decision D1 regarding `:w {file}` vs `:saveas {file}` location mutation semantics.)*
* **`:so` / `:source`** (`location_cmd`, `accepts_force=False`):
  * Reloads `location` (or `find_config_file()` when omitted) via `vith.rc_file`[^src-rc-file].
  * Add a quiet/message-reporting callback mode to `run_rc_file` so `:source` surfaces parse/missing-file errors via `editor.show_message()` without dropping out of the TUI to raw `input()`.

#### 3.2.3 Buffer & Tab Completeness

* **Alternate buffer (`#`)**:
  * Track `WindowArrangement.alternate_editor_buffer` whenever the active buffer changes (clearing it if that buffer is wiped).
  * Allow `:b #` (and `:e #` in Phase 4) to jump back to the alternate buffer; report `E23: No alternate file` when unset.
* **Buffer commands**:
  * `:bf` / `:bfirst` and `:bl` / `:blast` (`accepts_force=True`, guarded by `_confirm_unsaved_changes`).
  * `:bd[!] [N|name]` and `:bw[!] [N|name]`: extend `buffer_wipe` to accept an optional buffer selector resolved through `WindowArrangement` (defaulting to active buffer when omitted).
  * `:sb` / `:sbuffer [N|name]`: horizontal split showing the resolved buffer (reusing `BufferNameCompleter`[^src-completer]).
* **Tab commands** (extending `WindowArrangement`[^src-window-arrangement]):
  * `:tabo` / `:tabonly`: close all tab pages except `active_tab`.
  * `:tabfir` / `:tabfirst`, `:tabl` / `:tablast`: jump to tab `0` / `-1`.
  * `:tabm [N|+N|-N]` / `:tabmove`: move active tab to position `N` (default end), or relative `±N`.
  * `:tabs`: list all tab pages and their windows/buffers (highlighting active tab/window).

---

### 3.3 Phase 3 — Generalized Ex Range Engine & Line-Editing Commands

Currently, range parsing (`%`, `.`, `$`, `'<`, `'>`, `N`, `±offset`) is hardcoded exclusively to `:s` / `:substitute` in `COMMAND_GRAMMAR`[^src-grammar] and `commands.py`[^src-commands]. Phase 3 extracts a clean shared module `vith/commands/ranges.py` and generalizes grammar dispatch.

#### 3.3.1 Shared Address & Range Module (`vith/commands/ranges.py`)

* Extract `_LINE_ADDRESS_RE`, `parse_range(range_str)`, `resolve_address(addr, ...)`, and `resolve_range(start, end, ..., default_range, allow_zero=False)` from `commands.py`[^src-commands].
* Preserve 100% compatibility with existing `:s` tests (`tests/test_substitute.py`[^src-test-sub]).
* Upgrade bare address jump (`_go_to_line`[^src-handler]):
  * Support `:$` (jump to last line), `:.`, `:+N`, `:-N`, `:'<`, `'>`, in addition to `:<digits>`.
* Provide shared line-mutation helper `_replace_buffer_lines(eb, lines, cursor_row, *, col=0, first_non_blank=True, inserted_text='')` that uniformly handles:
  1. `read_only` check,
  2. `buffer.save_to_undo_stack()`,
  3. single `Document` construction,
  4. `note_document_text(...)` for the Thai/Lao renderer gate,
  5. clamped cursor placement.

#### 3.3.2 Range-Capable Ex Commands

| Command(s) | Default Range | Behavior |
| :--- | :--- | :--- |
| `:[range]d`, `:delete` | `.` (current line) | Deletes addressed lines, places them on `editor.application.clipboard` with `SelectionType.LINES`, moves cursor to next surviving line. |
| `:[range]y`, `:yank` | `.` | Copies addressed lines to `editor.application.clipboard` with `SelectionType.LINES`; reports `{N} lines yanked`. |
| `:[addr]pu[!]`, `:put[!]` | `.` | Inserts clipboard text after `[addr]` (or before with `!`, or at top with `:0pu`). |
| `:[range]m {addr}`, `:move` | `.` | Moves addressed lines after destination `{addr}` (`0` allowed). Rejects moving a range inside itself (`E134: Cannot move a range of lines into itself`). |
| `:[range]t {addr}`, `:co`, `:copy` | `.` | Duplicates addressed lines after destination `{addr}` (`0` allowed). |
| `:[range]j[!]`, `:join[!]` | `.` (joins `.` and `.+1` if single line) | Joins lines in range with single space (stripping leading whitespace on joined lines); `!` joins verbatim without stripping/inserting spaces. |
| `:[range]sort[!] [i][u][r][n]` | `%` (whole buffer) | Sorts lines in range: `!` or `r` reverses, `i` folds case, `u` deduplicates, `n` sorts by first decimal integer on each line. |
| `:[range]g[!]`/`:global[!]`, `:[range]v`/`:vglobal` | `%` | `/{pat}/{cmd}` (supports alternate delimiters like `:s`, enforces `_UNSAFE_NESTED_QUANTIFIER` ReDoS guard). Marks matching (or non-matching for `:g!`/`:v`) lines first, then executes sub-command (`d`, `y`, `s`, `m`, `t`, `p`, `norm`) as a single undo step. |
| `:[range]!{cmd}` | *(requires explicit range)* | Pipes addressed lines to `cmd`'s stdin and replaces the range with stdout as one undoable change; aborts without mutating on subprocess error. |
| `:[range]w[!] [file]`, `:[range]w[!] >> {file}`, `:[range]w !{cmd}` | `%` | Writes only the addressed range to `{file}` (without altering `eb.location` or clearing `has_unsaved_changes` when partial), appends with `>>`, or pipes range to `{cmd}`'s stdin. |
| `:[range]retab[!] [N]` | `%` | Re-expands tabs/spaces using `tabstop` (or updated `N`) and `expand_tab`; without `!`, converts only leading indent when `expandtab` is off. |
| `:[range]norm[!] {keys}` / `:normal` | `.` | *(Phase 3b / scoped)* Feeds normal-mode key sequence on each line in the range inside a single undo snapshot (guarded against recursion and unmapped hangs). |

---

### 3.4 Phase 4 — Command-Line Ergonomics, `%`/`#` Expansion & `:set` Completeness

#### 3.4.1 Special Filename Expansion (`%`, `#`)

* Add `expand_command_filenames(text, editor, *, shell_escape=False)` applied to location-taking commands (`:e`, `:w`, `:r`, `:sav`, `:sp`, `:vsp`, `:tabe`, `:so`) and shell commands (`:!`, `:r !`, `:[range]!`, `:w !`):
  * `%` → active buffer's `location` (error `E499: Empty file name for '%'` if untitled).
  * `#` → alternate buffer's `location` (error `E194: No alternate file name to substitute for '#'` if unset).
  * Standard filename modifiers: `:p` (absolute path), `:h` (dirname), `:t` (basename), `:r` (stem without extension), `:e` (extension), chainable (e.g. `%:p:h`).
  * Escaped `\%` and `\#` pass through literally.

#### 3.4.2 `:set` Grammar, Preview & Option Completeness

Upgrade `:set` / `:setlocal` / `:setl` across `grammar.py`[^src-grammar], `commands.py`[^src-commands], and `preview.py`[^src-preview] (preserving existing validation semantics pinned in `tests/test_set_int_options.py`[^src-test-set]):

1. **Syntax upgrades**:
   * **Bare `:set`**: lists options whose current values differ from defaults.
   * **`:set all`**: lists all options and their current values.
   * **Query (`?`)**: `:set {opt}?` displays `{opt}` / `no{opt}` for boolean toggles and `{opt}={val}` for int/list/filetype options.
   * **Toggle (`!` / `inv`)**: `:set {opt}!` and `:set inv{opt}` flip any boolean option in `SET_TOGGLE_GROUPS` (plus `tildeop`).
   * **Multiple arguments**: `:set nu rnu ts=4 et` parses space-separated tokens left-to-right and validates/previews cleanly.
2. **High-value missing options** (added to the canonical catalog in `commands.py`[^src-commands] and `_CONFIG_OPTION_MAP` in `rc_file.py`[^src-rc-file]):

| Option | Aliases | Type | Scope & Wiring |
| :--- | :--- | :--- | :--- |
| `smartcase` | `smartcase`, `scs` | Toggle | When both `ignorecase` and `smartcase` are on, patterns containing an uppercase character search case-sensitively (`SearchToolbar` in `layout.py`[^src-layout] and `substitute()`[^src-commands]). |
| `shiftwidth` | `shiftwidth`, `sw` | Int (`min=0`) | `0` follows buffer `tabstop` (Vim convention); wired into `>>`/`<<` (`_shift_lines` in `key_bindings.py`) and `<C-t>`. |
| `readonly` | `readonly`, `ro` | Toggle (buffer-local) | Toggles `eb.buffer.read_only` (and blocks `:w` without `!`). |
| `autoindent` | `autoindent`, `ai` | Toggle | Copies previous line's leading whitespace on Enter in Insert mode and `o`/`O` in Normal mode. |
| `fileencoding` | `fileencoding`, `fenc` | String (buffer-local) | Reads/sets `eb.encoding` (validated via `codecs.lookup`) so users can save in explicit encodings. |

#### 3.4.3 Inspection Commands & Command-Line Polish

* **`:reg` / `:registers`**: lists non-empty registers from `editor.application.vi_state.named_registers` and the unnamed clipboard (`"`).
* **`:marks`**: lists marks recorded on `eb.buffer` plus `'<` / `'>` visual line marks.
* **Completer & Lexer updates** (`completer.py`[^src-completer], `lexer.py`[^src-lexer]):
  * Ensure all newly registered commands appear in command completion, `:r`/`:sav`/`:so` complete paths via `PathCompleter`, `:r !` completes executables via `SystemCompleter`, `:bd`/`:sb` complete open buffers via `BufferNameCompleter`, and `:set` completes `inv*` / `?` / `!` forms gracefully.
* **Documentation refresh**:
  * Update `HELP_TEXT` in `vith/help.py`[^src-help] and the command summary in `README.md`[^src-readme] once implemented.

---

## 4. Module Touchpoint Matrix

| File | Phases | Planned Changes |
| :--- | :--- | :--- |
| `vith/commands/ranges.py` *(new)* | P3 | Extracted & generalized address/range parsing + shared line-mutation helper |
| [`../vith/commands/commands.py`](../vith/commands/commands.py) | P1–P4 | Command handlers, aliases, `:read`, `:saveas`, `:source`, `:set` multi/query/toggle, range commands, new options catalog |
| [`../vith/commands/grammar.py`](../vith/commands/grammar.py) | P2–P4 | Generalized range prefix, `:r !cmd`, `:bd <target>`, `:g`/`:v`, `:[range]!cmd`, multi-token `:set`, `$`/`±N` jumps |
| [`../vith/commands/handler.py`](../vith/commands/handler.py) | P2–P4 | Range dispatch, `%`/`#` expansion hook, generalized address jump |
| [`../vith/commands/completer.py`](../vith/commands/completer.py) | P1–P4 | Completion for new commands, `:bd`/`:sb`, `:r`/`:sav`/`:so`, `:r !`, `:set` forms |
| [`../vith/commands/preview.py`](../vith/commands/preview.py) | P4 | Multi-option `:set` preview, toggle (`!`/`inv`) preview, `smartcase`/`shiftwidth`/`autoindent` save/restore |
| [`../vith/editor.py`](../vith/editor.py) | P1, P4 | `_hlsearch_suppressed`, bounded `_messages` history deque, new option defaults |
| [`../vith/editor_buffer.py`](../vith/editor_buffer.py) | P2, P4 | Read-only write guard (`readonly`), `fileencoding` support, non-mutating backend read helper |
| [`../vith/window_arrangement.py`](../vith/window_arrangement.py) | P2 | `alternate_editor_buffer`, `go_to_first/last_buffer`, `close_buffer(target)`, `tabonly`/`tabfirst`/`tablast`/`tabmove` |
| [`../vith/layout.py`](../vith/layout.py) | P1, P4 | Wire `_hlsearch_suppressed` and `smartcase` into `BufferControl` / `SearchToolbar` |
| [`../vith/rc_file.py`](../vith/rc_file.py) | P2, P4 | Non-interactive error-reporting path for `:source`, new `_CONFIG_OPTION_MAP` entries |
| [`../vith/help.py`](../vith/help.py), [`../README.md`](../README.md) | P4 | Sync documented Ex command inventory |

---

## 5. Verification & Test Strategy

Every phase MUST land with dedicated unit tests and pass all repository gates before moving to the next phase:

1. **New Test Suites**:
   * `tests/test_ex_commands.py`: Phase 1 aliases (`:ls`, `:files`, `:x`, `:xa`, `:wa`, `:up`, `:u`/`:undo`, `:red`/`:redo`, `:noh`, `:clo`, `:enew`, `:f`/`:file`, `:ver`, `:messages`).
   * `tests/test_read_command.py`: Phase 2 `:r`/`:read` (file, gzip, missing file, untitled bare `:r`, `:0r`, `:$r`, empty buffer, `:r !cmd`, read-only guard, Thai gate latch, undo round-trip), `:sav`, `:so`, `:b #`, `:bd <target>`, `:tabo`/`:tabm`/`:tabs`.
   * `tests/test_range_commands.py`: Phase 3 `ranges.py`, `:$`/`+N`/`-N`, `:d`, `:y`, `:pu`, `:m`, `:t`/`:co`, `:j`, `:sort` (flags + Unicode/Thai), `:g`/`:v` (incl. ReDoS guard), `:[range]!cmd`, `:[range]w` (partial/append/pipe), `:retab`.
   * `tests/test_set_ergonomics.py`: Phase 4 `%`/`#` expansion + modifiers, `:set` `?`/`!`/`inv`/bare/`all`/multi-option, `smartcase`/`shiftwidth`/`readonly`/`autoindent`/`fileencoding`, `:reg`, `:marks`.
   * Extend `tests/test_commands_grammar.py`[^src-test-grammar] and `tests/test_preview.py` to pin grammar non-collisions and zero-side-effect previewing.
2. **Mandatory Invariant Assertions per Mutating Command**:
   * `ColdTextBuffer` undo restores exact pre-command text and cursor.
   * Introducing Thai/Lao text latches `note_document_text`.
   * Read-only buffer rejects the command cleanly with an editor message.
   * Empty `WindowArrangement` (`active_editor_buffer is None`) never raises.
3. **Repository Gates**:
   * `bash scripts/runtest.sh` — full pytest suite green with `>= 85%` coverage.
   * `bash scripts/type-check.sh` — `mypy .` clean against the Python 3.10 floor.
   * `bash scripts/lint-check-and-fix.sh` & `ruff format --check .` — clean.

---

## 6. Open Design Decisions (Confirm Before Implementation)

| ID | Topic | Options | Recommended Default |
| :--- | :--- | :--- | :--- |
| **D1** | `:w {file}` vs `:saveas {file}` location mutation | **(A)** Keep `:w {file}` mutating `eb.location` (preserves current pyvim/`test_write_commands.py`[^src-test-write] behavior) and have `:sav` explicitly share it; **(B)** Make `:w {file}` write-a-copy (strict Vim) and reserve renaming for `:sav` (only rename on `:w {file}` when buffer was untitled). | **(B)** for Vim parity — update only any test that relied on renaming a named buffer via `:w other`. |
| **D2** | Pager vs scratch split for `:messages`, `:tabs`, `:reg`, `:marks`, `:set all` | **(A)** `_run_pager` (exits alternate screen, `Press ENTER`, matches `:buffers`/`:ls`); **(B)** Read-only scratch `hsplit` (matches `:help`/`:compression`, stays inside TUI, easy to search/yank/test). | **(B)** for multi-line inspection commands (`:messages`, `:tabs`, `:reg`, `:marks`, `:set all`, `:ver`), keeping `:ls`/`:buffers` on `_run_pager` (or switching consistently if preferred). |
| **D3** | `:r !cmd` / `:[range]!cmd` non-zero exit code | **(A)** Abort buffer mutation when `returncode != 0` and show `stderr`; **(B)** Insert `stdout` even on non-zero exit (Vim behavior) and show exit status. | **(B)** for `:r !cmd` if `stdout` is non-empty (plus message), **(A)** for `:[range]!cmd` so a typo in `:%!jq` never wipes the buffer. |
| **D4** | `:normal` (`:norm`) scope | **(A)** Include in Phase 3 via `KeyProcessor.feed()` with recursion guard; **(B)** Defer `:norm` to a follow-up after Phase 3 ships. | **(B)** Defer `:norm` until core range commands stabilize, avoiding subtle `KeyProcessor` re-entrancy risks. |

---

## References

[^src-commands]: `vith/commands/commands.py` (command registry, `:set` catalog, `substitute`, write/quit/buffer handlers) — [../vith/commands/commands.py](../vith/commands/commands.py)
[^src-grammar]: `vith/commands/grammar.py` (`COMMAND_GRAMMAR`, `_longest_first`) — [../vith/commands/grammar.py](../vith/commands/grammar.py)
[^src-handler]: `vith/commands/handler.py` (`handle_command`, `_go_to_line`) — [../vith/commands/handler.py](../vith/commands/handler.py)
[^src-completer]: `vith/commands/completer.py` (`create_command_completer`, `BufferNameCompleter`, `FiletypeCompleter`) — [../vith/commands/completer.py](../vith/commands/completer.py)
[^src-preview]: `vith/commands/preview.py` (`CommandPreviewer`) — [../vith/commands/preview.py](../vith/commands/preview.py)
[^src-lexer]: `vith/commands/lexer.py` (`create_command_lexer`) — [../vith/commands/lexer.py](../vith/commands/lexer.py)
[^src-editor]: `vith/editor.py` (`Editor.__init__`, `show_message`, `highlight_search`) — [../vith/editor.py](../vith/editor.py)
[^src-editor-buffer]: `vith/editor_buffer.py` (`EditorBuffer._read`, `write`, `reload`, `_on_text_changed`) — [../vith/editor_buffer.py](../vith/editor_buffer.py)
[^src-window-arrangement]: `vith/window_arrangement.py` (`WindowArrangement`, `TabPage`) — [../vith/window_arrangement.py](../vith/window_arrangement.py)
[^src-cold-text]: `vith/cold_text.py` (`ColdTextBuffer.undo`, `redo`, `save_to_undo_stack`) — [../vith/cold_text.py](../vith/cold_text.py)
[^src-layout]: `vith/layout.py` (`EditorLayout._create_buffer_control`, `SearchToolbar`, `BufferListOverlay`) — [../vith/layout.py](../vith/layout.py)
[^src-rc-file]: `vith/rc_file.py` (`find_config_file`, `run_rc_file`, `_CONFIG_OPTION_MAP`) — [../vith/rc_file.py](../vith/rc_file.py)
[^src-backends]: `vith/io/backends.py` (`FileIO`, `GZipFileIO`, `DirectoryIO`, `HttpIO`) — [../vith/io/backends.py](../vith/io/backends.py)
[^src-help]: `vith/help.py` (`HELP_TEXT`) — [../vith/help.py](../vith/help.py)
[^src-readme]: `README.md` (documented command list including `:ls` and `!python3 %`) — [../README.md](../README.md)
[^src-test-grammar]: `tests/test_commands_grammar.py` (grammar alternation & non-collision tests) — [../tests/test_commands_grammar.py](../tests/test_commands_grammar.py)
[^src-test-write]: `tests/test_write_commands.py` (`:w`/`:wq`/`:wqa`/`:e` write & guard tests) — [../tests/test_write_commands.py](../tests/test_write_commands.py)
[^src-test-quit]: `tests/test_quit_and_goto.py` (`:q`/`:b`/`:ls`/`:next` tests) — [../tests/test_quit_and_goto.py](../tests/test_quit_and_goto.py)
[^src-test-sub]: `tests/test_substitute.py` (`:s` range, delimiter, flag & history tests) — [../tests/test_substitute.py](../tests/test_substitute.py)
[^src-test-set]: `tests/test_set_int_options.py` (`:set` integer option tests) — [../tests/test_set_int_options.py](../tests/test_set_int_options.py)
