---
type: architecture_guideline
title: "Live Async :render Split Preview — Technical Architecture & Reference"
description: "Full technical specification of vith's real-time asynchronous :render / :render-split-v / :render-split-h preview architecture: optional Rich/rich-rst backends, direct Segment-to-StyleAndTextTuples bridging, debounced executor coalescing, stale-edit retention, heading/landmark-anchored cursor & scroll sync, hot-path latency engineering, and TOML configuration."
status: stable
sources:
  - ../vith/render.py
  - ../vith/commands/render.py
  - ../vith/commands/__init__.py
  - ../vith/commands/options.py
  - ../vith/editor.py
  - ../vith/editor_buffer.py
  - ../vith/window_arrangement.py
  - ../vith/lexer.py
  - ../vith/layout.py
  - ../vith/rc_file.py
  - ../vith/cold_text.py
  - ../vith/help.py
  - ../pyproject.toml
  - ../examples/config/config.toml
  - ../tests/test_render.py
verified: [machine-confirmed, human-reviewed]
stale_after: 2027-05-22T00:00:00Z
tags: [render, preview, async, rich, markdown, rst, scroll-sync, latency, architecture]
generated:
  by: "OpenCode (Hy4 flash Free)"
  at: "2026-11-23T12:00:00Z"
timestamp: "2026-11-23T12:00:00Z"
---

# Live Async `:render` Split Preview — Technical Architecture & Reference

[Docs root](index.md) · sibling docs: [Ex command completeness](command_completeness_plan.md) · [Thai & Lao support](thai_support_plan.md) · [Speed & latency](speed_latency_plan.md) · [Findings (closed)](bugs/index.md)

This document details the architecture, algorithms, invariants, latency optimizations, and configuration of `vith`'s real-time asynchronous preview splits (`:render`, `:render-split-v`, `:render-split-h`), shipped in Mercurial rev `407:cc09ab108f1b`.

---

## 1. Design Goals & Architectural Principles

| Goal | Mechanism | Verification |
| :--- | :--- | :--- |
| **Zero startup overhead** | Optional `vith[render]` extra (`rich>=13.0.0`, `rich-rst>=1.3.0`)[^src-pyproject]; all third-party imports deferred to render execution; availability probed side-effect-free via `importlib.util.find_spec`[^src-render]. | Cold `Editor()` init loads **0** modules from `rich`, `rich_rst`, `markdown_it`, or `docutils` (`6.26 ms`). |
| **Zero editing lag** | Debounced single-flight execution in `asyncio`'s default thread-pool executor; main-thread hooks preserve `Document._cache` laziness and run in $O(1)$ amortized time[^src-render]. | `200 × insert_text('a')` on 5,000-line Markdown: `+0.006 ms/key` vs non-render (`_cache.line_indexes is None`). |
| **Zero hand-rolled markup parsers** | Delegates Markdown (`markdown-it-py` via `rich.markdown`), reStructuredText (`docutils` via `rich_rst`), JSON (`rich.json`), tables (`rich.table`), and code (`rich.syntax`) to mature upstream engines[^src-render]. | No custom HTML/Markdown/RST AST parser to maintain; HTML/LaTeX/custom formats plug in via `[render.commands]`. |
| **Resilience to mid-edit syntax** | Markdown & CSV render incrementally even with unclosed markers; strict formats (JSON, incomplete RST directives) raise `ParseIncompleteError` and retain the last valid preview marked `stale`[^src-render]. | Preview never blanks out or flashes raw tracebacks while typing. |
| **Full Vi ergonomics & Thai/Lao parity** | Preview lives in a standard read-only scratch `EditorBuffer` whose `plain_text` aligns 1:1 with pre-rendered `StyleAndTextTuples` and feeds `note_document_text()`[^src-render][^src-lexer]. | `/` search, `v`/`y` yank, Thai/Lao cell-cluster motions, and SARA AM differential rendering work natively. |

### 1.1 Why HTML Is Delegated to `[render.commands]`

Unlike Markdown or CSV, raw HTML cannot guarantee well-formed tag nesting mid-keystroke (an unclosed `<table>`, `<script>`, or `<style>` tag can swallow the rest of the document on every character typed) and requires a dedicated layout/CSS engine for faithful terminal output. Rather than maintaining an error-prone custom HTML parser (the earlier experimental prototype archived in [`bugs/159.md`](bugs/159.md)), built-in rendering focuses on formats with rock-solid terminal libraries while exposing `[render.commands]` so users who want HTML, Org, AsciiDoc, or custom DSL previews can attach any external CLI tool (e.g., `lynx -dump -stdin`, `w3m -dump -T text/html`, or `glow`) with zero core complexity[^src-render][^src-rc].

---

## 2. System Architecture & Data Flow

```text
User Action (Main Thread / asyncio Event Loop)
────────────────────────────────────────────────────────────────────────────────
  Edit text ──▶ EditorBuffer._on_text_changed() ──▶ ctrl.schedule()
  :set ft=… ──▶ set_filetype()                  ──▶ ctrl.schedule(immediate=True)
  :w / :e!  ──▶ EditorBuffer.write()/reload()   ──▶ ctrl.schedule(immediate=True)
  Move cursor ─▶ buffer.on_cursor_position_changed ▶ ctrl.sync_scroll() [O(1)/O(log A)]
  Resize / zz ─▶ Editor.after_render            ──▶ ctrl.on_after_render()
                                                      ├─ width changed ──▶ ctrl.schedule()
                                                      └─ vscroll changed ─▶ ctrl.sync_scroll()

Debounced Single-Flight Dispatch (Main Thread)
────────────────────────────────────────────────────────────────────────────────
  loop.call_later(delay, _dispatch)   [debounce_ms with max_wait ceiling]
       │
       ▼  _snapshot() captures immutable (version, text, location, filetype, width, cmds)
  loop.run_in_executor(None, in_executor)

Worker Thread (Pure, Thread-Safe — No prompt_toolkit / Editor Mutation)
────────────────────────────────────────────────────────────────────────────────
  render_document(text, location, filetype, width, custom_commands)
       ├─ detect_render_format()       (4-tier precedence)
       ├─ Rich / rich-rst / ANSI / CLI (Console.render_lines + _rich_style_to_ptk)
       ├─ _coalesce_line_fragments()   (merge adjacent equal styles)
       └─ _attach_anchors()            (_extract_source_landmarks + _build_line_anchors)
       │
       ▼  loop.call_soon_threadsafe(finish)

Atomic Main-Thread Commit
────────────────────────────────────────────────────────────────────────────────
  _apply_result() / _apply_error()
       ├─ preview_eb._rendered_lines = result.styled_lines
       ├─ preview_eb._rendered_version += 1
       ├─ precompute _preview_line_starts  [O(R) once per render]
       ├─ preview_eb.buffer.set_document(Document(plain_text, ...), bypass_readonly=True)
       ├─ note_document_text(plain_text)   [latch Thai/Lao SARA AM gate if needed]
       ├─ sync_scroll(force=True)          [anchor-interpolated cursor + vertical_scroll]
       ├─ editor.application.invalidate()
       └─ if _pending or _text_version != version: ctrl.schedule()  [coalesce missed edits]

Frame Paint (prompt_toolkit Renderer)
────────────────────────────────────────────────────────────────────────────────
  DocumentLexer.invalidation_hash() ──▶ includes preview_eb._rendered_version
  DocumentLexer.lex_document()      ──▶ O(1) list lookup into preview_eb._rendered_lines
```

---

## 3. Ex Commands, Split Placement & Lifecycle

### 3.1 Command Surface

Registered in [`vith/commands/render.py`](../vith/commands/render.py)[^src-cmd-render] and re-exported in [`vith/commands/__init__.py`](../vith/commands/__init__.py)[^src-cmd-init]:

| Command | Behavior | With `!` (`force=True`) |
| :--- | :--- | :--- |
| `:render` | Opens/refreshes preview using `editor.render_default_split` (`'vertical'` by default). | Closes active buffer's live preview split. |
| `:render-split-v` | Opens vertical preview split (editor left, preview right); re-orients if currently horizontal. | Closes active buffer's live preview split. |
| `:render-split-h` | Opens horizontal preview split (editor top, preview bottom); re-orients if currently vertical. | Closes active buffer's live preview split. |

Key behavioral invariants:
* **Grammar safety**: All three commands register through `@cmd(..., accepts_force=True)`, so `COMMAND_GRAMMAR` orders them longest-first (`render-split-v` / `render-split-h` before `render`), preventing prefix collisions and keeping `CommandPreviewer` completely side-effect-free while typing.
* **Source-buffer resolution**: `_resolve_source_eb()` and `open_or_update_live_render()` inspect `eb._owned_render_controller`; running `:render`, `:render-split-h`, or `:render!` while focused *inside* the preview pane resolves cleanly back to the underlying source buffer instead of nesting a preview of a preview[^src-cmd-render][^src-render].
* **Idempotency & re-orientation**: Re-running the same orientation triggers an immediate refresh (`existing.schedule(immediate=True)`) without creating duplicate splits; running the opposite orientation closes the old preview split cleanly and opens the new orientation[^src-render].

### 3.2 Split Placement & Focus Retention

[`WindowArrangement`](../vith/window_arrangement.py)[^src-wa] extends `TabPage._split()`, `WindowArrangement._split()`, `hsplit()`, and `vsplit()` with two backward-compatible keyword arguments:

* `after=True`: inserts the new preview `Window` **after** the active window in the split container (right for vertical splits, bottom for horizontal splits), rather than before it.
* `focus_new=False`: keeps `active_window` and keyboard focus in the source editing pane so the user can keep typing immediately.

Both `hsplit()` and `vsplit()` return the newly created `Window` instance so `open_or_update_live_render()` can bind `LiveRenderController` directly to the exact `Window` and its `pt_window`[^src-wa][^src-render].

### 3.3 Preview Pane Chrome & Cleanup

In [`vith/layout.py`](../vith/layout.py)[^src-layout], panes where `editor_buffer.is_render_preview` is `True` suppress editor-only visual chrome while keeping search and visual selection active:

| Visual Feature | Source Pane | Render Preview Pane (`is_render_preview=True`) |
| :--- | :--- | :--- |
| Line-number gutter (`number` / `relativenumber`) | User setting | Suppressed |
| `cursorline` / `cursorcolumn` / `colorcolumns` | User setting | Suppressed |
| Soft `wrap_lines` (`:set wrap`) | User setting | Suppressed (Rich pre-wraps to exact pane column width) |
| `ShowUnprintableProcessor` (`:set list`) | User setting | Suppressed (avoids polluting rendered indentation/tables) |
| `HighlightSearchProcessor`, `HighlightSelectionProcessor`, `HighlightMatchingBracketProcessor` | Active | Active (`/` search and `v`/`y` yank work normally) |

Lifecycle detachment avoids circular reference leaks:
* `LiveRenderController` stores `weakref.ref` handles to `editor`, `source_eb`, `preview_eb`, and `preview_window`[^src-render].
* Closing the preview via `:render!`, `:q`, `:clo`, `:only`, or `:bd` triggers `LiveRenderController.close()` (directly or via `EditorBuffer.close_live_render_previews()` called from `WindowArrangement._auto_close_new_empty_buffers()` and `close_buffer()`)[^src-eb][^src-wa].
* `close()` cancels any pending `asyncio.TimerHandle`, removes `_on_source_cursor_changed` from `source_eb.buffer.on_cursor_position_changed`, and detaches the controller from `source_eb._live_render_controllers`, `preview_eb._owned_render_controller`, and `editor._live_render_controllers`[^src-render].

---

## 4. Format Detection & Rendering Backends

### 4.1 Four-Tier Format Detection Precedence

`detect_render_format(location, filetype, text, custom_commands)`[^src-render] resolves the effective format in strict deterministic order:

1. **Explicit filetype (`:set filetype=...` or detected `eb.filetype`)**: checks `custom_commands` first, then `_FT_TO_FORMAT` (`markdown`/`md`, `rst`/`restructuredtext`, `json`, `csv`, `tsv`, `ansi`).
2. **Filename extension (`location`)**: checks `custom_commands` first, then `_EXT_TO_FORMAT` (`md`/`markdown`/`mdown`/`mkd`, `rst`/`rest`, `json`, `csv`, `tsv`, `ansi`).
3. **Content heuristics (first 4,096 chars only)**:
   * Raw ANSI SGR escape sequences (`\x1b\[[0-9;]*m`) → `'ansi'`.
   * `sniff_filetype_from_text(sample)` (shared with [`vith/lexer.py`](../vith/lexer.py)) → checked against `custom_commands` and `_FT_TO_FORMAT`.
   * reStructuredText directives (`^\.\.\s+[A-Za-z][\w-]*::`) → `'rst'`.
   * Markdown ATX headings or fenced code blocks (`^(?:#{1,6}\s+\S|```|~~~)`) → `'markdown'`.
4. **Fallback**: known code/config filetype (`ft` or `sniffed`) → `'syntax'` (`rich.syntax.Syntax`); unnamed/untitled prose → `'markdown'`.

### 4.2 Supported Rendering Backends

| Format | Backend | Optional Dependency | Safety & Boundedness Controls |
| :--- | :--- | :--- | :--- |
| `markdown` | `rich.markdown.Markdown(text, code_theme=code_theme, hyperlinks=False)` | `rich` | `hyperlinks=False` prevents OSC-8 escape noise in `prompt_toolkit`; fenced code blocks highlighted via Pygments (`monokai` theme). |
| `rst` | `rich_rst.RestructuredText(text, code_theme=code_theme, show_errors=False, guess_lexer=False, allow_file_access=False)` | `rich`, `rich-rst` | `allow_file_access=False` blocks `.. include::` filesystem reads; `show_errors=False` suppresses docutils stderr dumps; exceptions wrap into `ParseIncompleteError`. |
| `json` | `json.loads(text)` pre-check + `rich.json.JSON(text)` | `rich` | Malformed mid-edit JSON raises `ParseIncompleteError` so the previous valid tree remains visible (`json • stale`). |
| `csv` / `tsv` | `csv.reader` → `rich.table.Table(box=box.ROUNDED)` | `rich` | Capped at `_CSV_MAX_ROWS = 5000` with a dim italic truncation note to bound memory & render latency on huge spreadsheets. |
| `ansi` | `prompt_toolkit.formatted_text.ANSI` + `_coalesce_line_fragments` | None (built-in) | Works even without `vith[render]` installed. |
| `syntax` | `rich.syntax.Syntax(text, lexer_name, theme=code_theme, word_wrap=True)` | `rich` | Fallback for arbitrary code/config buffers (`:render` on `.py`, `.toml`, `.yaml`, etc.). |
| `[render.commands]` | `subprocess.run(command, shell=True, input=text, env={..., COLUMNS: width}, timeout=5.0)` | None (external CLI) | `_EXTERNAL_CMD_TIMEOUT = 5.0` seconds; stdout parsed via `ansi_to_render_result`; timeout/non-zero empty output raises `ParseIncompleteError`. |

### 4.3 Missing Dependency Behavior

Before opening any split, `open_or_update_live_render()` calls `_required_package_for_format(fmt, custom_commands)`. If `rich` or `rich-rst` is not installed (`importlib.util.find_spec(...) is None`), `vith` displays an actionable status message (`Optional package '<pkg>' is required for :render (<fmt>). Install with: pip install 'vith[render]'`) and leaves the window layout untouched[^src-render].

---

## 5. Direct Rich → `prompt_toolkit` Style Bridge & Lexer Fast Path

### 5.1 Avoiding ANSI Serialization Round-Trips

Rather than asking Rich to serialize styled text into ANSI escape strings and then re-parsing ANSI back into `prompt_toolkit` fragments, `_render_rich_object()`[^src-render] invokes Rich's structured layout pipeline directly:

```python
console = Console(
    file=io.StringIO(),
    width=safe_width,
    force_terminal=True,
    color_system='truecolor',
    legacy_windows=False,
)
options = console.options.update_width(safe_width)
raw_lines = console.render_lines(renderable, options, pad=False, new_lines=False)
```

> **Rich 15.0.0 `TERM=dumb` width caveat**: Passing `width=safe_width` to `Console(...)` alone still allows `console.options.max_width` to default to `80` under `TERM=dumb` or overridden `COLUMNS` environments. Explicitly passing `options = console.options.update_width(safe_width)` into `console.render_lines(...)` guarantees exact column wrapping across all terminal environments[^src-render].

Each `Segment` is filtered (`seg.control is None`, non-empty, stripped of stray `\r`/`\n`) and mapped directly to a `(ptk_style, text)` tuple via `@lru_cache(maxsize=1024)` `_rich_style_to_ptk()`:

| Rich `Style` Property | `prompt_toolkit` Token |
| :--- | :--- |
| `color` (`ColorType.STANDARD`, 0–15) | `ansiblack` … `ansiwhite` (`_ANSI_16_NAMES`) |
| `color` (`EIGHT_BIT` / `TRUECOLOR` / `WINDOWS`) | `#rrggbb` via `color.get_truecolor().hex` |
| `bgcolor` | Same as foreground prefixed with `bg:` |
| `bold`, `italic`, `underline`, `strike`, `dim`, `reverse`, `blink` | Same attribute names |
| `conceal` | `hidden` |

Adjacent segments sharing the same resolved style string are merged by `_coalesce_line_fragments()`, reducing fragment count per line and keeping `prompt_toolkit`'s renderer fast[^src-render].

### 5.2 1:1 Text/Style Alignment & `DocumentLexer` Integration

`_build_render_result()` derives `plain_text` strictly by joining `''.join(frag[1] for frag in line)` with `'\n'`, guaranteeing `len(plain_text.split('\n')) == len(styled_lines)` and character-exact index alignment for `/` search highlighting and visual `v`/`y` yanking.

In [`vith/lexer.py`](../vith/lexer.py)[^src-lexer]:
* `DocumentLexer.invalidation_hash()` appends `_rendered_version` (guarded by `isinstance(rendered_ver, int)`) whenever a buffer has pre-rendered lines, forcing `prompt_toolkit` to repaint immediately on style-only updates even when `plain_text` length is unchanged.
* `DocumentLexer.lex_document()` checks `isinstance(rendered_lines, list)` (type-checked so `MagicMock` test doubles never take this branch) and returns an $O(1)$ row lookup closure `get_line(lineno) -> rendered_lines[lineno]`.
* `_update_preview_document()` calls `note_document_text(plain_text)` so Thai/Lao content emitted by Markdown/RST/CSV renders latches `vith`'s `SARA AM` differential renderer gate automatically[^src-render].

---

## 6. Asynchronous Scheduling, Coalescing & Fault Tolerance

`LiveRenderController`[^src-render] decouples editing latency from rendering work using a single-flight debounced state machine:

| Parameter / State | Default / Formula | Role |
| :--- | :--- | :--- |
| `render_debounce_ms` | `150` ms (configurable) | Quiet period after a keystroke before dispatching a background render. |
| `max_wait_s` | `max(debounce_s * 3.0, 0.50)` | Ceiling measured from `_first_dirty_at` so sustained continuous typing still refreshes the preview at least every ~0.5 s. |
| `_in_flight` / `_pending` | `bool` | Guarantees at most **one** executor render runs per preview pane at a time; edits arriving mid-render set `_pending = True` without spawning concurrent worker threads. |
| `version` (`_text_version`) | `int` | Captured in `_snapshot()` before worker launch; on `finish()`, if `_pending` is set or `source_eb.buffer._text_version != version`, one coalesced follow-up render is scheduled immediately. |
| `render_sync()` fallback | Sync when no event loop | When `asyncio.get_running_loop()` raises `RuntimeError` (e.g. synchronous unit tests or non-loop callers), falls back cleanly to synchronous rendering. |

### 6.1 Error & Stale-State Semantics

* **`ParseIncompleteError` (mid-edit JSON, broken RST directive, timed-out CLI command)**:
  * If `_has_good_render` is `True`: keeps the existing rendered preview on screen untouched and updates only the status title to `[Render: <name> (<fmt> • stale)]`[^src-render].
  * If `_has_good_render` is `False` (file was already malformed on first open): displays a single red italic diagnostic line in the preview pane.
  * As soon as the next edit produces valid syntax, `_apply_result()` replaces the preview and clears `• stale`.
* **Resize tracking**: `Editor.after_render` invokes `ctrl.on_after_render()`, which compares `_compute_width()` (preferring `preview_window.pt_window.render_info.window_width`, falling back to terminal columns halved for vertical splits) against `_last_width` and schedules a re-wrap render whenever the split or terminal resizes[^src-editor][^src-render].

---

## 7. Heading/Landmark-Anchored Position & Scroll Synchronization

### 7.1 Why Proportional Line Ratios Drift

Rendered documents do not expand uniformly: a 10-line Markdown table expands into 25+ bordered terminal rows, a long paragraph wraps to 4–6 lines in a narrow split, while a 20-line code block stays nearly 1:1. Naive `preview_row = round(src_row / src_lines * preview_lines)` drifts tens of lines away from the section the user is editing.

### 7.2 Off-Thread Landmark Extraction & Indexed Anchor Matching

At the end of every worker-thread render pass, `_attach_anchors()` builds a sorted monotonic anchor table `tuple[tuple[int, int], ...]` mapping `(source_row, rendered_row)`[^src-render]:

1. **Structural landmark extraction (`_extract_source_landmarks`)**:
   * **Markdown**: ATX headings (`#`–`######`, ignoring lines inside ```` ``` ```` / `~~~` fences) and Setext headings (`===` / `---` underlines), stripping inline links and emphasis markers (`**`, `*`, `` ` ``, `~`).
   * **reStructuredText**: underlined section titles (`=`, `-`, `~`, `^`, `_`, `*`, `+`, `#`, `:`, `.`, `"`, `'`, `` ` ``).
   * **JSON**: top-level object keys (`^\s{0,2}"([^"]+)"\s*:`).
   * **CSV / TSV**: up to 64 evenly sampled rows matched by first cell (within `_CSV_MAX_ROWS`).
2. **Linear-time indexed matching (`_build_line_anchors`)**:
   * Indexes normalized rendered lines once in $O(R)$ into three structures: `exact_styled` (normalized text of `bold`/`underline` lines → sorted row list), `exact_any` (all non-empty lines → sorted row list), and `styled_rows` (sorted indices of heading-styled rows).
   * Matches each landmark monotonically after `search_start` via:
     1. $O(\log R)$ exact styled hit (`bisect_left` on `exact_styled[norm_text]`),
     2. Bounded styled substring scan (up to 64 heading rows after `search_start`),
     3. Bounded plain fallback scan (`max_fallback_scan = 256` rows) so unmatched or heavily reformatted headings **never** degrade to $O(H \cdot R)$.
   * Always pins endpoints `(0, 0)` and `(src_last, ren_last)`.
3. **Piecewise-linear interpolation (`map_source_row_to_preview`)**:
   * Uses `bisect_right(anchors, (clamped, preview_line_count + 1))` in $O(\log A)$ to locate the bracketing anchors `(s0, r0)` and `(s1, r1)` and linearly interpolates between them (falling back gracefully to global proportional mapping when no internal landmarks exist)[^src-render].

### 7.3 `prompt_toolkit` Viewport Alignment & Auto-Pause

In `prompt_toolkit`, `Window._scroll_without_linewrapping()` always clamps `vertical_scroll` to keep `buffer.cursor_position` visible inside the viewport. Setting `vertical_scroll` alone snaps back on the next paint if the preview buffer's cursor stays at line 0. Therefore `LiveRenderController.sync_scroll()` updates **both**[^src-render]:

1. Moves `preview_eb.buffer.cursor_position` to `self._preview_line_starts[clamped_row]` ($O(1)$ lookup).
2. Computes the source cursor's visual screen offset `src_screen_y = clamp(src_row - src_vscroll, 0, win_height - 1)` and sets `preview_pt_win.vertical_scroll = clamp(clamped_row - src_screen_y, 0, max_scroll)`, keeping the rendered heading/line horizontally level with the editor cursor.
3. Deduplicates work via `self._last_sync_state = (src_row, src_vscroll, preview_version)`.
4. **Triggers**: runs immediately on `source_eb.buffer.on_cursor_position_changed` (arrow/`j`/`k`/`G`/`/`/typing) and on `on_after_render()` (catching viewport-only scrolls like `zt`, `zz`, `zb`, `Ctrl-E`, `Ctrl-Y`, and mouse wheel).
5. **Auto-pause**: immediately returns `False` when `active_tab.active_window is preview_win` or `editor.render_sync_scroll` is `False`, so manually scrolling, searching, or yanking inside the preview pane is never fought[^src-render].

---

## 8. Hot-Path Latency Engineering & Benchmarks

During post-implementation profiling on a **5,000-line (242 KB) Markdown** buffer, two main-thread regressions were identified and fixed before release:

1. **Lazy `Document` cache materialization on keystrokes**: Reading `source_eb.buffer.document.cursor_position_row` inside `sync_scroll()` forced `prompt_toolkit`'s `Document._line_start_indexes` to allocate and scan all 5,000 line offsets on every single typed character (`8.6×` slowdown: `0.034 ms/key → 0.295 ms/key`).
   * **Fix (`_source_row_and_line_count`)**: If `doc._cache.line_indexes` is already populated (e.g. during vertical motions), uses it directly ($O(\log N)$ row, $O(1)$ count). Otherwise reuses `ColdTextBuffer._non_cursor_version`[^src-cold-text] + `line_start` in $O(1)$ for consecutive single-line edits/horizontal moves, and falls back to pure-C `str.count('\n', ...)` with a `(nc_ver, text_ver, len(text))` line-count cache (collision-free across `undo`/`redo` branching and `reset()`), never materializing `Document._cache.line_indexes`[^src-render].
2. **Per-motion preview rescans & anchor worst case**: `sync_scroll()` previously called `preview_doc.text.count('\n')` and `translate_row_col_to_index()` on every cursor move (`39×` slowdown on `cursor_down`), and `_build_line_anchors()` linearly scanned all remaining rendered rows on unmatched headings.
   * **Fix**: Precomputes `self._preview_line_starts` once inside `_update_preview_document()` for $O(1)$ cursor offset lookup, and indexes `_build_line_anchors()` with `bisect_left` + bounded fallback scanning[^src-render].

### 8.1 Reproducible Measurements

Measured on **Python 3.15.2 (arm64)**, `prompt_toolkit 3.0.53`, `Pygments 2.21.0`, `rich 15.0.0`, `rich-rst 2.2.0` against a **5,002-line / 242,323-char Markdown** document (250 sections, mixed English/Thai):

| Operation | Without `:render` | With `:render` (pre-opt) | With `:render` (shipped) |
| :--- | ---: | ---: | ---: |
| Cold `Editor()` init (`rich`/`docutils` loaded) | `6.26 ms` (0) | `7.40 ms` (0) | **`6.26 ms` (0)** |
| Open vertical preview split (`:render-split-v`) | — | — | **`2.02 ms`** (main thread) |
| Initial 5k-line Markdown render (worker thread) | — | — | **`222.75 ms`** (off-thread; 9,501 rows, 252 anchors) |
| `200 × insert_text('a')` | `21.74 ms` (`0.109 ms/key`) | `58.94 ms` (`0.295 ms/key`) | **`22.96 ms` (`0.115 ms/key`, `+5.6%`)** |
| `Document._cache` after live typing (`lines`, `line_indexes`) | `(False, False)` | `(False, True)` | **`(False, False)`** |
| `100 × insert_text('\n')` | `10.77 ms` (`0.108 ms/key`) | — | **`17.46 ms` (`0.175 ms/key`, `_cache=(False, False)`)** |
| `500 × cursor_down()` (with live preview sync) | `18.12 ms` (`0.036 ms/key`) | `46.51 ms` (`0.093 ms/key`) | **`2.03 ms` (`0.004 ms/key`)** |
| `500 × cursor_right()` | `0.36 ms` (`0.001 ms/key`) | `2.35 ms` (`0.005 ms/key`) | **`0.39 ms` (`0.001 ms/key`)** |
| `500 × on_after_render()` | — | — | **`0.71 ms` (`0.0014 ms/frame`)** |
| `500 × DocumentLexer` preview line lookups | — | — | **`0.02 ms` (`0.04 µs/line`)** |
| Worst-case `_build_line_anchors` (500 unmatched × 10k rows) | — | — | **`6.91 ms`** (off-thread) |

---

## 9. Configuration Reference (`config.toml`)

Parsed and validated in [`vith/rc_file.py`](../vith/rc_file.py)[^src-rc] (with `'render'` added to `_TOML_DETECTION_KEYS`) and stored on [`Editor`](../vith/editor.py)[^src-editor]:

```toml
[render]
default_split = "vertical"   # "vertical" | "horizontal"
debounce_ms = 150            # non-negative integer (milliseconds)
sync_scroll = true           # boolean: heading/landmark-anchored position sync

[render.commands]
# Optional per-filetype/extension CLI renderers (buffer on stdin, ANSI on stdout,
# COLUMNS env var set to preview pane width, 5.0 s timeout).
# html = "lynx -dump -stdin"
```

| TOML Key | Editor Attribute | Type & Validation | Default |
| :--- | :--- | :--- | :--- |
| `[render].default_split` | `editor.render_default_split` | `'vertical'` (or `v` / `vert`) or `'horizontal'` (or `h` / `horiz`); invalid string logs an error message | `'vertical'` |
| `[render].debounce_ms` | `editor.render_debounce_ms` | `int` in `0..10000` (rejects `bool`, negatives and values above `10000`) | `150` |
| `[render].sync_scroll` | `editor.render_sync_scroll` | `bool` | `True` |
| `[render.commands].<fmt>` | `editor.render_commands[fmt]` | Non-empty `str` command (keys normalized lowercase) | `{}` |

---

## 10. Module Touchpoints & Verification Matrix

| Module | Responsibility |
| :--- | :--- |
| [`vith/render.py`](../vith/render.py) | Format detection, Rich/rich-rst/ANSI/CSV/external rendering, style & fragment bridge, landmark anchor builder, `LiveRenderController`, split open/close helpers. |
| [`vith/commands/render.py`](../vith/commands/render.py) | `:render[!]`, `:render-split-v[!]`, `:render-split-h[!]` Ex command handlers. |
| [`vith/commands/__init__.py`](../vith/commands/__init__.py) | Registration & re-export of render command handlers. |
| [`vith/commands/options.py`](../vith/commands/options.py) | Immediate preview refresh hook in `set_filetype()`. |
| [`vith/window_arrangement.py`](../vith/window_arrangement.py) | `after=True` / `focus_new=False` split placement, `Window` return value, buffer-close preview cleanup. |
| [`vith/editor_buffer.py`](../vith/editor_buffer.py) | Preview state fields (`is_render_preview`, `_rendered_lines`, `_rendered_version`, controller refs), `close_live_render_previews()`, and change/reload/write notifications. |
| [`vith/lexer.py`](../vith/lexer.py) | `_rendered_version` cache invalidation and $O(1)$ `isinstance(rendered_lines, list)` fast path. |
| [`vith/layout.py`](../vith/layout.py) | Preview pane chrome suppression (gutter, cursorline/column, wrap, unprintable markers). |
| [`vith/editor.py`](../vith/editor.py) | Render config defaults, `_live_render_controllers` registry, `after_render` resize/scroll hook, `pre_run` initial dispatch. |
| [`vith/rc_file.py`](../vith/rc_file.py) | TOML `[render]` and `[render.commands]` schema validation. |
| [`vith/help.py`](../vith/help.py) & [`examples/config/config.toml`](../examples/config/config.toml) | Built-in `:help` command reference and sample TOML config. |
| [`pyproject.toml`](../pyproject.toml) | Optional `render = ["rich>=13.0.0", "rich-rst>=1.3.0"]` extra. |
| [`tests/test_render.py`](../tests/test_render.py) | 11 test functions covering style conversion, ANSI coalescing, format precedence, Markdown/Thai/RST/JSON/CSV/external rendering, missing-dep degradation, split placement/focus/re-orientation/close, async debounce/stale-recovery/resize/`:only` cleanup, `[render]` TOML validation, `:set ft` refresh, heading-anchored position/scroll sync, and lazy `Document._cache` + undo-branch row-accuracy regression guards. |

### 10.1 Shipped Verification Gates (rev `407:cc09ab108f1b`)

* **`scripts/code-format.sh`**: clean
* **`scripts/lint-check-and-fix.sh`**: `All checks passed!`
* **`scripts/type-check.sh`**: `Success: no issues found in 88 source files` (Python 3.10 floor)
* **`scripts/runtest.sh`**: **`558 passed`**, **89.24% total coverage** (`>= 85%` gate; `vith/commands/render.py` 92%, `vith/render.py` 85%)
* **`tools/bench_plans.py`**: all Thai/Lao and speed-latency baseline benchmarks clean

---

[^src-render]: [`../vith/render.py`](../vith/render.py)
[^src-cmd-render]: [`../vith/commands/render.py`](../vith/commands/render.py)
[^src-cmd-init]: [`../vith/commands/__init__.py`](../vith/commands/__init__.py)
[^src-editor]: [`../vith/editor.py`](../vith/editor.py)
[^src-eb]: [`../vith/editor_buffer.py`](../vith/editor_buffer.py)
[^src-wa]: [`../vith/window_arrangement.py`](../vith/window_arrangement.py)
[^src-lexer]: [`../vith/lexer.py`](../vith/lexer.py)
[^src-layout]: [`../vith/layout.py`](../vith/layout.py)
[^src-rc]: [`../vith/rc_file.py`](../vith/rc_file.py)
[^src-cold-text]: [`../vith/cold_text.py`](../vith/cold_text.py)
[^src-pyproject]: [`../pyproject.toml`](../pyproject.toml)
