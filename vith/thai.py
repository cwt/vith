"""
Thai (`U+0E00..U+0E7F`) and Lao (`U+0E80..U+0EFF`) script support for `vith`.

Adapts the cell-cluster and differential renderer architecture from `szn`
(`src/thai.zig`, `src/mode_copy.zig`, `src/server/render.zig`):

1. Character-wise Vi motions and edits (`h`, `l`, `j`, `k`, `x`, `X`, `s`,
   `r`, `~`, `$`, `a`, `<Esc>`) step by terminal cell clusters (`base + comb*`)
   so the cursor never parks on a zero-width combining mark (`GC=Mn`) and edits
   never slice a cluster in half.
2. Word-wise Vi motions and text objects (`w`, `b`, `e`, `W`, `B`, `E`, `iw`,
   `aw`, `*`, `#`) segment unspaced Thai/Lao text using a two-tier strategy:
   Tier 1 (`libthai` dictionary breaker via `ctypes`, optional at runtime) with
   Tier 2 (`find_thai_cluster_end` orthographic syllable cluster heuristic) as
   an always-available fallback.
3. Differential terminal rendering (`prompt_toolkit.renderer._output_screen_diff`)
   cleans up stray zero-width combining marks and invalidates the neighbor cell
   whenever Thai/Lao `SARA AM` (`U+0E33` / `U+0EB3`) or its preceding consonant
   at `x - 1` changes.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import re
from bisect import bisect_right
from typing import Any, Callable, Iterable

import prompt_toolkit.renderer as ptk_renderer
from prompt_toolkit.document import Document
from prompt_toolkit.filters.app import vi_mode, vi_navigation_mode, vi_selection_mode
from prompt_toolkit.selection import SelectionType

try:
    from prompt_toolkit.key_binding.bindings.vi import TextObject as _PtkTextObject
    from prompt_toolkit.key_binding.bindings.vi import TextObjectType as _PtkTextObjectType
except Exception:  # pragma: no cover
    _PtkTextObject = None  # type: ignore[assignment,misc]
    _PtkTextObjectType = None  # type: ignore[assignment,misc]
from prompt_toolkit.data_structures import Point
from prompt_toolkit.key_binding.key_processor import KeyPressEvent, KeyProcessor
from prompt_toolkit.layout.containers import Window
from prompt_toolkit.layout.screen import Char, Screen
from prompt_toolkit.utils import get_cwidth

try:
    from prompt_toolkit.layout.containers import _CHAR_CACHE as _PtkCharCache
except ImportError:  # pragma: no cover - private cache may be renamed upstream
    _PtkCharCache = None  # type: ignore[assignment,misc]

try:
    # prompt_toolkit.token exports this same class-name token used by the
    # renderer itself (prompt_toolkit/renderer.py:809); import it instead of
    # hard-coding the magic substring.
    from prompt_toolkit.token import ZeroWidthEscape as _PtkZeroWidthEscape
except ImportError:  # pragma: no cover - token may be renamed upstream
    _PtkZeroWidthEscape = "[ZeroWidthEscape]"  # type: ignore[assignment,misc]

# Split modules (docs/bugs/075.md): `vith.thai` re-exports their public
# names so `from vith.thai import ...` keeps working for tests, tools and
# rc files.
from vith.thai_classify import (  # noqa: F401
    _THAI_BASE_CPS,
    _THAI_COMBINING_CPS,
    _THAI_DIGIT_CPS,
    _THAI_SYMBOL_CPS,
    is_combining_char,
    is_lao,
    is_sara_am,
    is_thai,
    is_thai_base,
    is_thai_combining,
    is_thai_following_vowel,
    is_thai_leading_vowel,
    is_thai_or_lao,
    is_thai_right_attaching,
)
from vith.thai_clusters import (  # noqa: F401
    cell_starts,
    cell_stops,
    next_cell_offset,
    prev_cell_offset,
    snap_to_cell_start,
)
from vith.thai_words import (  # noqa: F401
    _ORIGINAL_DOCUMENT_METHODS,
    _ORIGINAL_KEY_PROCESSOR_METHODS,
    _iter_word_spans,
    _word_spans_for_line,
    find_boundaries_of_current_word,
    find_next_word_beginning,
    find_next_word_ending,
    find_previous_word_beginning,
    find_previous_word_ending,
    find_start_of_previous_word,
    find_thai_cluster_end,
    find_thai_word_breaks,
)

__all__ = (
    "is_thai",
    "is_lao",
    "is_thai_or_lao",
    "is_thai_combining",
    "is_combining_char",
    "is_sara_am",
    "is_thai_leading_vowel",
    "is_thai_following_vowel",
    "is_thai_right_attaching",
    "is_thai_base",
    "snap_to_cell_start",
    "next_cell_offset",
    "prev_cell_offset",
    "cell_starts",
    "cell_stops",
    "find_thai_cluster_end",
    "find_thai_word_breaks",
    "find_next_word_beginning",
    "find_next_word_ending",
    "find_previous_word_beginning",
    "find_previous_word_ending",
    "find_start_of_previous_word",
    "find_boundaries_of_current_word",
    "install_prompt_toolkit_thai_patches",
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 4. Document Cell-Cluster Cursor Movement Wrappers (Phase 2)
# ---------------------------------------------------------------------------


def _doc_get_cursor_left_position(self: Document, count: int = 1) -> int:
    if count < 0:
        return self.get_cursor_right_position(-count)
    line = self.current_line
    col = self.cursor_position_col
    if line.isascii():
        return -min(col, count)
    return prev_cell_offset(line, col, count) - col


def _doc_get_cursor_right_position(self: Document, count: int = 1) -> int:
    if count < 0:
        return self.get_cursor_left_position(-count)
    line = self.current_line
    col = self.cursor_position_col
    if line.isascii():
        return min(count, len(line) - col)
    return next_cell_offset(line, col, count) - col


def _vertical_cursor_position(
    self: Document, target_row: int, preferred_column: int | None = None
) -> int:
    """
    Index delta for moving to `target_row` with the *same visual column*
    (cell-cluster ordinal, docs/bugs/078.md). `preferred_column` is a
    codepoint column recorded on a previous row — for the common live-cursor
    case (`preferred_column is None`, or a column of the current row) the
    conversion via `_cell_ordinal` on the current row is exact; for sticky
    columns recorded on a different Thai row this is a best-effort mapping
    (the sticky-correct path is the `ColdTextBuffer.cursor_up/down`
    override).
    """
    column = self.cursor_position_col if preferred_column is None else preferred_column
    current_line = self.lines[self.cursor_position_row]
    ordinal = _cell_ordinal(current_line, min(column, len(current_line)))
    target_line = self.lines[target_row]
    target_col = _start_of_cell_ordinal(target_line, ordinal)
    return self.translate_row_col_to_index(target_row, target_col) - self.cursor_position


def _doc_get_cursor_up_position(
    self: Document, count: int = 1, preferred_column: int | None = None
) -> int:
    assert count >= 1
    return _vertical_cursor_position(
        self, max(0, self.cursor_position_row - count), preferred_column
    )


def _doc_get_cursor_down_position(
    self: Document, count: int = 1, preferred_column: int | None = None
) -> int:
    assert count >= 1
    target_row = min(len(self.lines) - 1, self.cursor_position_row + count)
    return _vertical_cursor_position(self, target_row, preferred_column)


def _kp_fix_vi_cursor_position(self: KeyProcessor, event: KeyPressEvent) -> None:
    """
    Cell-cluster-aware replacement for `KeyProcessor._fix_vi_cursor_position`.
    In Vi navigation mode, ensures the cursor never rests past EOL or on a
    zero-width combining mark after a key handler runs.
    """
    app = event.app
    buff = app.current_buffer
    preferred_column = buff.preferred_column
    doc = buff.document
    line = doc.current_line

    if (vi_navigation_mode() or vi_selection_mode()) and len(line) > 0:
        col = doc.cursor_position_col
        if vi_navigation_mode() and doc.is_cursor_at_the_end_of_line:
            target_col = prev_cell_offset(line, len(line), 1)
        else:
            target_col = snap_to_cell_start(line, col)
        if target_col != col:
            buff.cursor_position += target_col - col
            buff.preferred_column = preferred_column


def _make_kp_start_timeout_wrapper(
    orig_start_timeout: Callable[[KeyProcessor], None],
) -> Callable[[KeyProcessor], None]:
    def _kp_start_timeout(self: KeyProcessor) -> None:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # When called synchronously from unit tests without a running
            # event loop (Python 3.10+ behaviour, error since 3.14), skip
            # spawning the background timeout coroutine entirely.
            return
        orig_start_timeout(self)

    return _kp_start_timeout


# ---------------------------------------------------------------------------
# 5. Differential Renderer Patch for Stray Marks & SARA AM (Phase 4)
# ---------------------------------------------------------------------------

_SARA_AM_POISON = Char(char="\x00", style="__vith_sara_am_invalidate__")
_SARA_AM_DEFAULT_CHAR = Char()

# C-speed detection for strings that may land on screen; ASCII strings are
# free (`str.isascii()`), so the gate never needs to scan a screen grid to
# decide whether one is worth the SARA-AM neighbor refresh.
_HAS_THAI_RE = re.compile("[\u0e00-\u0eff]")


class _ThaiTextGate:
    """
    Sticky "this session may render Thai/Lao text" flag.

    `prompt_toolkit` exposes no cheap hook that says whether a rendered
    `Screen` still contains Thai cells; scanning the whole grid per frame
    costs ~1.3 ms on a 50x200 terminal with plain-ASCII content
    (``docs/bugs/002.md``). Instead, vith reports the strings that reach
    the screen (buffer text, buffer locations, command/search lines) via
    `note_document_text` and the renderer wrapper skips the neighbor scan
    entirely while the flag is still ``False``. Once latched ``True`` the
    flag stays on for the rest of the session, so sessions that ever showed
    Thai/Lao keep the full (correct) renderer behavior.
    """

    __slots__ = ("value",)

    def __init__(self) -> None:
        self.value = False

    def note(self, text: str | None) -> None:
        """Record a string that may be rendered (sticky-latch on Thai)."""
        if self.value or not text or text.isascii():
            return
        if _HAS_THAI_RE.search(text):
            self.value = True

    def reset(self) -> None:
        """Reset for tests; production sessions never call this."""
        self.value = False


_THAI_GATE = _ThaiTextGate()


def note_document_text(text: str | None) -> None:
    """
    Report a string that may be rendered on screen (buffer content, buffer
    location, command or search line) to the renderer gate.
    """
    _THAI_GATE.note(text)


def reset_thai_text_gate() -> None:
    """Reset the sticky renderer gate (used by the test suite)."""
    _THAI_GATE.reset()


def reset_thai_text_gate_if_all_buffers_ascii(window_arrangement) -> None:
    """
    Decay the sticky Thai-gate latch once every open buffer currently holds
    ASCII-only content (mixed-session renderer cost; used to stay latched
    forever, docs/bugs/072.md — improving on the old 075 row 7). Called
    from `Editor.sync_with_prompt_toolkit`, i.e. on buffer switches and
    command executions, not per keystroke; the next Thai keystroke
    re-latches immediately via `note_document_text`.
    """
    if not _THAI_GATE.value:
        return
    editor = window_arrangement.editor
    for editor_buffer in window_arrangement.editor_buffers:
        if not editor_buffer.buffer.text.isascii():
            return
        # The status bar, overlay and report messages also render Thai from
        # non-text sources (buffer *locations*, `editor.message`): a session
        # whose buffers are all-ASCII but with a Thai-named file / Thai error
        # banner still draws Thai cells, so the sanitizer must stay armed
        # for them (docs/bugs/082.md).
        if editor_buffer.location and not editor_buffer.location.isascii():
            return
    message = getattr(editor, "message", None) or ""
    if not message.isascii():
        return
    _THAI_GATE.reset()


def _cell_has_sara_am(cell: Char) -> bool:
    ch = cell.char
    if not ch:
        return False
    first = ord(ch[0])
    if first == 0x0E33 or first == 0x0EB3:
        return True
    return "\u0e33" in ch or "\u0eb3" in ch


def _sanitize_and_invalidate_sara_am(
    new_screen: Screen, previous_screen: Screen | None, width: int
) -> None:
    """
    1. Clear any stray zero-width combining mark cells (`cell.width == 0 and cell.char != ""`)
       left at the end of `FormattedTextControl` or wrapped lines.
    2. Port `szn` commit `71ba808` (`src/server/render.zig:664-694`): when Thai/Lao
       `SARA AM` (`U+0E33` / `U+0EB3`) at column `x` or its preceding cell `x - 1`
       changes, poison the neighbor in `previous_screen` so `_output_screen_diff`
       re-emits both cells together.
    """
    row_indices = set(new_screen.data_buffer.keys())
    if previous_screen is not None:
        row_indices.update(previous_screen.data_buffer.keys())

    for y in row_indices:
        new_row = new_screen.data_buffer.get(y)
        if new_row is None:
            # Row only exists in previous_screen; nothing to sanitize here.
            continue

        # 1. Clear stray width-0 combining cells not overwritten by a trailing
        #    character. (Only values are replaced, so mutating while
        #    iterating the dict is safe and avoids a per-row list copy;
        #    docs/bugs/072.md.)
        for x, cell in new_row.items():
            if cell.width == 0 and cell.char != "":
                new_row[x] = Char(char=" ", style=cell.style)

        # 2. SARA AM neighbor invalidation for differential rendering.
        if previous_screen is None or y not in previous_screen.data_buffer:
            continue
        prev_row = previous_screen.data_buffer[y]
        # Deterministic ascending order without a double set union +
        # `sorted(rs(...))` chain per row: distinct keys >= 1 sorted once
        # (the poisoned keys 1011/1045 can only be added below the current
        # `x`, never after the loop global order matters; docs/bugs/072.md).
        all_xs = {x for x in new_row.keys() if x >= 1}
        for x in prev_row:
            if x >= 1:
                all_xs.add(x)
        for x in sorted(all_xs):
            if x not in prev_row or x not in new_row:
                # Use defaults without inflating defaultdicts.
                prev_cell = prev_row.get(x, _SARA_AM_DEFAULT_CHAR)
                cur_cell = new_row.get(x, _SARA_AM_DEFAULT_CHAR)
            else:
                prev_cell = prev_row[x]
                cur_cell = new_row[x]
            if _cell_has_sara_am(prev_cell) or _cell_has_sara_am(cur_cell):
                prev_left = prev_row.get(x - 1, _SARA_AM_DEFAULT_CHAR)
                cur_left = new_row.get(x - 1, _SARA_AM_DEFAULT_CHAR)
                left_changed = (cur_left.char != prev_left.char) or (
                    cur_left.style != prev_left.style
                )
                cur_changed = (cur_cell.char != prev_cell.char) or (
                    cur_cell.style != prev_cell.style
                )
                if cur_changed and not left_changed:
                    prev_row[x - 1] = _SARA_AM_POISON
                elif left_changed and not cur_changed:
                    prev_row[x] = _SARA_AM_POISON


_AUTOWRAP_ARM_TAG = "\x00_VITH_THAI_AUTOWRAP_\x00"


def _named_arg_resolver(params: list[str], is_method: bool):
    """
    Build a resolver that maps a wrapper's positional `args`/`kwargs` to
    parameter names captured from the *verified* prompt_toolkit signature.
    Resolving by name (instead of bare `args[i]` indices) means an inserted
    prompt_toolkit parameter shifts with its name rather than silently
    re-binding; `is_method=True` accounts for the bound `self` argument.
    """
    skip = 1 if is_method else 0
    positions = {name: idx - skip for idx, name in enumerate(params)}

    def resolve(name: str, args: tuple[Any, ...], kwargs: dict[str, Any], default: Any = None):
        idx = positions.get(name)
        if idx is not None and idx >= 0 and idx < len(args):
            return args[idx]
        return kwargs.get(name, default)

    return resolve


def _install_window_copy_body_patch(applied: list[tuple[Any, str, object]] | None = None) -> bool:
    """
    Wrap `prompt_toolkit.layout.containers.Window._copy_body` so zero-width
    combining marks (`GC=Mn`, `char_width == 0`) immediately following a base
    character in the rightmost column of a `Window` (`x == width - 1`, or
    `x == width - 2` for a double-width base) are merged into that rightmost
    cell instead of being dropped when `x` advances to `width`.
    """
    orig_copy_body = getattr(Window, "_copy_body", None)
    if orig_copy_body is None:
        logger.warning("Window._copy_body not found; skipping right-margin patch.")
        return False
    if getattr(orig_copy_body, "_vith_thai_patched", False):
        return True

    # The wrapper resolves `has_focus` by parameter name against this full
    # expected prefix (Window._copy_body in prompt_toolkit 3.0.53); a
    # signature drift fails loudly (patch skipped) instead of mis-binding.
    expected_params = (
        "self",
        "ui_content",
        "new_screen",
        "write_position",
        "move_x",
        "width",
        "vertical_scroll",
        "horizontal_scroll",
        "wrap_lines",
        "highlight_lines",
        "vertical_scroll_2",
        "always_hide_cursor",
        "has_focus",
        "align",
        "get_line_prefix",
    )
    try:
        params = list(inspect.signature(orig_copy_body).parameters.keys())
    except (TypeError, ValueError):
        logger.warning("Could not inspect Window._copy_body signature; skipping patch.")
        return False

    if len(params) < len(expected_params) or params[: len(expected_params)] != list(
        expected_params
    ):
        logger.warning(
            "Unexpected Window._copy_body parameters %r; skipping right-margin patch.",
            params,
        )
        return False
    resolve_arg = _named_arg_resolver(params, is_method=True)

    def _patched_copy_body(
        self: Any,
        ui_content: Any,
        new_screen: Screen,
        write_position: Any,
        move_x: int,
        width: int,
        *args: Any,
        **kwargs: Any,
    ) -> tuple[dict[int, tuple[int, int]], dict[tuple[int, int], tuple[int, int]]]:
        visible_line_to_row_col, rowcol_to_yx = orig_copy_body(
            self,
            ui_content,
            new_screen,
            write_position,
            move_x,
            width,
            *args,
            **kwargs,
        )
        if width <= 0 or write_position.height <= 0 or not rowcol_to_yx:
            return visible_line_to_row_col, rowcol_to_yx

        xpos = write_position.xpos + move_x
        ypos = write_position.ypos
        right_x = xpos + width - 1
        data_buffer = new_screen.data_buffer

        # O(window_height) fast-path: only inspect `rowcol_to_yx` if at least
        # one visible row actually has a non-ASCII base character in the
        # rightmost column of this window.
        candidate_rows: set[int] = set()
        for sy in range(ypos, ypos + write_position.height):
            if sy in data_buffer:
                row_buf = data_buffer[sy]
                cell = row_buf.get(right_x)
                if cell is not None and cell.char and not cell.char.isascii():
                    candidate_rows.add(sy)
                    continue
                if width >= 2:
                    cell2 = row_buf.get(right_x - 1)
                    if cell2 is not None and cell2.width == 2 and not cell2.char.isascii():
                        candidate_rows.add(sy)

        if not candidate_rows:
            return visible_line_to_row_col, rowcol_to_yx

        has_focus = bool(resolve_arg("has_focus", args, kwargs, False))
        line_chars_cache: dict[int, str] = {}
        for (lineno, col), (sy, sx) in list(rowcol_to_yx.items()):
            if sy not in candidate_rows:
                continue
            if sx != right_x and not (
                width >= 2 and sx == right_x - 1 and data_buffer[sy][sx].width == 2
            ):
                continue
            chars = line_chars_cache.get(lineno)
            if chars is None:
                chars = "".join(
                    t
                    for style, t, *_ in ui_content.get_line(lineno)
                    if _PtkZeroWidthEscape not in style
                )
                line_chars_cache[lineno] = chars
            if col >= len(chars) or get_cwidth(chars[col]) == 0:
                continue
            j = col + 1
            n = len(chars)
            while j < n and get_cwidth(chars[j]) == 0:
                j += 1
            if j > col + 1:
                comb = chars[col + 1 : j]
                _THAI_GATE.note(comb)
                prev_char = data_buffer[sy][sx]
                if not prev_char.char.endswith(comb):
                    if _PtkCharCache is not None:
                        data_buffer[sy][sx] = _PtkCharCache[prev_char.char + comb, prev_char.style]
                    else:
                        data_buffer[sy][sx] = Char(prev_char.char + comb, prev_char.style)
                for c_idx in range(col + 1, j):
                    rowcol_to_yx[lineno, c_idx] = (sy, sx)
                # Keep the base character last in insertion order so prompt_toolkit's
                # mouse reverse-lookup `yx_to_rowcol = {v: k for k, v in
                # rowcol_to_yx.items()}` (layout/containers.py:1828) resolves
                # `(sy, sx)` to `(lineno, col)`, not a combining mark column.
                del rowcol_to_yx[lineno, col]
                rowcol_to_yx[lineno, col] = (sy, sx)
                cpos = ui_content.cursor_position
                if has_focus and cpos is not None and cpos.y == lineno and col < cpos.x < j:
                    new_screen.set_cursor_position(self, Point(x=sx, y=sy))

        return visible_line_to_row_col, rowcol_to_yx

    setattr(_patched_copy_body, "_vith_thai_patched", True)
    setattr(_patched_copy_body, "_vith_original", orig_copy_body)
    Window._copy_body = _patched_copy_body  # type: ignore[method-assign]
    if applied is not None:
        applied.append((Window, "_copy_body", orig_copy_body))
    return True


def _install_renderer_patch(applied: list[tuple[Any, str, object]] | None = None) -> bool:
    """
    Wrap `prompt_toolkit.renderer._output_screen_diff` with signature verification.
    Returns `True` if installed (or already installed), `False` if skipped.
    """
    orig_diff = getattr(ptk_renderer, "_output_screen_diff", None)
    if orig_diff is None:
        logger.warning("prompt_toolkit.renderer._output_screen_diff not found; skipping patch.")
        return False
    if getattr(orig_diff, "_vith_thai_patched", False):
        return True

    try:
        params = list(inspect.signature(orig_diff).parameters.keys())
    except (TypeError, ValueError):
        logger.warning("Could not inspect _output_screen_diff signature; skipping patch.")
        return False

    # The wrapper resolves `output`/`screen`/`previous_screen`/`is_done`/`size`
    # by parameter name against this full expected prefix
    # (`_output_screen_diff` in prompt_toolkit 3.0.53); a signature drift
    # fails loudly (patch skipped) instead of mis-binding arguments.
    expected_params = (
        "app",
        "output",
        "screen",
        "current_pos",
        "color_depth",
        "previous_screen",
        "last_style",
        "is_done",
        "full_screen",
        "attrs_for_style_string",
        "style_string_has_style",
        "size",
        "previous_width",
    )
    try:
        params = list(inspect.signature(orig_diff).parameters.keys())
    except (TypeError, ValueError):
        logger.warning("Could not inspect _output_screen_diff signature; skipping patch.")
        return False

    if len(params) < len(expected_params) or params[: len(expected_params)] != list(
        expected_params
    ):
        logger.warning(
            "Unexpected _output_screen_diff parameters %r; skipping renderer patch.",
            params,
        )
        return False
    resolve_arg = _named_arg_resolver(params, is_method=False)

    def _wrapped_output_screen_diff(*args: Any, **kwargs: Any) -> Any:
        output = resolve_arg("output", args, kwargs)
        screen = resolve_arg("screen", args, kwargs)
        previous_screen = resolve_arg("previous_screen", args, kwargs)
        is_done = bool(resolve_arg("is_done", args, kwargs, False))
        size = resolve_arg("size", args, kwargs)
        # Skip the full-screen scan while the sticky gate says this session
        # has never rendered Thai/Lao text (see `docs/bugs/002.md`); once a
        # Thai-capable string was observed, always sanitize (unchanged
        # semantics).
        if not _THAI_GATE.value or screen is None:
            return orig_diff(*args, **kwargs)

        width = size.columns if size is not None else 80
        _sanitize_and_invalidate_sara_am(screen, previous_screen, width)

        # In VT100 terminals and multiplexers (such as `szn`), `_output_screen_diff`
        # runs with `DECAWM` disabled (`\x1b[?7l`). When a base character is written
        # at the rightmost terminal column (`width - 1`), `DECAWM` off prevents the
        # cursor from entering the pending-wrap state (`cursor.x == width`), causing
        # subsequent zero-width combining marks (`width == 0`) to attach to `width - 2`
        # instead of `width - 1`. Temporarily enable `DECAWM` (`\x1b[?7h`) before
        # emitting a multi-codepoint cluster in the last column and restore `\x1b[?7l`
        # + clamp cursor to column `width` (`\x1b[{width}G`) immediately after.
        saved_escapes: list[tuple[int, int, str]] = []
        if (
            not is_done
            and output is not None
            and width >= 1
            and hasattr(output, "write")
            and hasattr(output, "write_raw")
        ):
            max_y = size.rows if size is not None else screen.height
            for y, new_row in screen.data_buffer.items():
                if 0 <= y < max_y:
                    target_col = -1
                    cell = new_row.get(width - 1)
                    if cell is not None and len(cell.char) > 1:
                        target_col = width - 1
                    elif width >= 2:
                        cell2 = new_row.get(width - 2)
                        if cell2 is not None and cell2.width == 2 and len(cell2.char) > 1:
                            target_col = width - 2
                    if target_col >= 0:
                        zw_row = screen.zero_width_escapes[y]
                        old_esc = zw_row.get(target_col, "")
                        saved_escapes.append((y, target_col, old_esc))
                        zw_row[target_col] = old_esc + _AUTOWRAP_ARM_TAG

        if not saved_escapes or output is None:
            return orig_diff(*args, **kwargs)

        if getattr(output, "_vith_write_swap_active", False):
            # Another `Application` already swapped `write`/`write_raw` on
            # this output object (re-entrant or nested `run_in_terminal`):
            # serialize by running the diff without a second swap.
            return orig_diff(*args, **kwargs)

        orig_write = output.write
        orig_write_raw = output.write_raw
        armed = False
        reset_seq = f"\x1b[?7l\x1b[{width}G"

        def _wrapped_write_raw(data: str) -> None:
            nonlocal armed
            if _AUTOWRAP_ARM_TAG in data:
                data = data.replace(_AUTOWRAP_ARM_TAG, "\x1b[?7h")
                armed = True
            orig_write_raw(data)

        def _wrapped_write(data: str) -> None:
            nonlocal armed
            orig_write(data)
            if armed:
                armed = False
                orig_write_raw(reset_seq)

        output.write = _wrapped_write  # type: ignore[method-assign]
        output.write_raw = _wrapped_write_raw  # type: ignore[method-assign]
        output._vith_write_swap_active = True
        try:
            return orig_diff(*args, **kwargs)
        finally:
            output._vith_write_swap_active = False
            output.write = orig_write  # type: ignore[method-assign]
            output.write_raw = orig_write_raw  # type: ignore[method-assign]
            for y, col_idx, old_esc in saved_escapes:
                zw_row = screen.zero_width_escapes.get(y)
                if zw_row is not None:
                    if old_esc:
                        zw_row[col_idx] = old_esc
                    else:
                        zw_row.pop(col_idx, None)

    setattr(_wrapped_output_screen_diff, "_vith_thai_patched", True)
    setattr(_wrapped_output_screen_diff, "_vith_original", orig_diff)
    ptk_renderer._output_screen_diff = _wrapped_output_screen_diff  # type: ignore[assignment]
    if applied is not None:
        applied.append((ptk_renderer, "_output_screen_diff", orig_diff))
    return True


# ---------------------------------------------------------------------------
# 6. Version-Gated prompt_toolkit Patch Installer (Section 4.1)
# ---------------------------------------------------------------------------

# The patch-original registries live in `thai_words` (leaf) so both the
# word walkers and the wrappers here share one storage; imported at the
# top of this module (docs/bugs/075.md).


def _thai_selection_ranges(self: Document) -> Iterable[tuple[int, int]]:
    """Thai-aware wrapper for `Document.selection_ranges`."""
    orig = _ORIGINAL_DOCUMENT_METHODS.get("selection_ranges")
    if orig is None:
        # Fallback to the (unpatched) bound method.
        orig = type(self).selection_ranges
    selection = self.selection
    block = selection is not None and selection.type == SelectionType.BLOCK
    v_low = v_high = 0
    if block and vi_mode() and selection is not None:
        # Visual block rectangle in cell-cluster ordinals (Vim semantics,
        # mirroring docs/bugs/077.md, keeping 079: the "I"/"A" multi-cursor
        # positions must sit on cell boundaries — inserting on a combining
        # mark splits the cluster).
        cursor_pos = self.cursor_position
        original_pos = selection.original_cursor_position
        # Operator wrappers (prompt_toolkit `TextObject.cut` et al.) move the
        # cursor one codepoint back before re-reading the selection; when it
        # thereby parks on a combining mark of its own cluster, block `d`
        # would cut one cell wider than the on-screen highlight (block
        # corner runs one cell up/left). Compensate that cursor-side corner
        # (docs/bugs/084.md).
        cursor_row, cursor_col = self.translate_index_to_position(cursor_pos)
        cursor_row = min(cursor_row, len(self.lines) - 1)
        cursor_line = self.lines[cursor_row]
        if 0 < cursor_col < len(cursor_line) and is_combining_char(cursor_line[cursor_col]):
            cursor_pos += 1
        cursor_pos = min(cursor_pos, len(self.text))
        from_pos, to_pos = sorted((cursor_pos, original_pos))
        row_from, col_from = self.translate_index_to_position(from_pos)
        row_to, col_to = self.translate_index_to_position(to_pos)
        line_from = self.lines[min(row_from, len(self.lines) - 1)]
        line_to = self.lines[min(row_to, len(self.lines) - 1)]
        v_low = min(_cell_ordinal(line_from, col_from), _cell_ordinal(line_to, col_to))
        v_high = max(_cell_ordinal(line_from, col_from), _cell_ordinal(line_to, col_to))
    for from_, to in orig(self):
        if vi_mode() and to > from_ and to <= len(self.text):
            if block:
                row, _col = self.translate_index_to_position(from_)
                line = self.lines[min(row, len(self.lines) - 1)]
                start_col = _start_of_cell_ordinal(line, v_low)
                end_col = _start_of_cell_ordinal(line, v_high + 1)
                from_ = self.translate_row_col_to_index(row, start_col)
                end_abs = self.translate_row_col_to_index(row, end_col)
                to = end_abs
                yield from_, to
                continue
            last_idx = to - 1
            row, col = self.translate_index_to_position(last_idx)
            if row < len(self.lines):
                line = self.lines[row]
                cluster_end_col = next_cell_offset(line, col, 1)
                new_to = self.translate_row_col_to_index(row, cluster_end_col)
                if new_to > to:
                    to = new_to
        yield from_, to


def _cell_ordinal(line: str, col: int) -> int:
    """
    Visual cell-cluster ordinal that contains codepoint column `col`
    (combining marks overprint their base, so every Thai/Lao cluster is
    exactly one terminal cell: its ordinal == its visual column).

    Empty and ASCII lines shortcut (columns == ordinals); `col` may sit
    mid-cluster — the ordinal of the cluster's base is returned
    (docs/bugs/077.md).
    """
    if not line or line.isascii():
        return col
    starts = cell_starts(line)
    if not starts:
        return 0
    if col >= len(line):
        # A codepoint parked at/past the line end is the empty slot *after*
        # the last cell (Vim curswant semantics), not the last cell itself.
        return len(starts)
    return max(0, bisect_right(starts, col) - 1)


def _start_of_cell_ordinal(line: str, ordinal: int) -> int:
    """
    Line-relative codepoint column of the start of visual cell `ordinal`
    (`len(line)` when the row has fewer cells). ASCII shortcut keeps the
    codepoint/visual identity.
    """
    n = len(line)
    if not line or line.isascii():
        return min(ordinal, n)
    starts = cell_starts(line)
    return starts[ordinal] if ordinal < len(starts) else n


def _thai_selection_range_at_line(self: Document, row: int) -> tuple[int, int] | None:
    """Thai-aware wrapper for `Document.selection_range_at_line`."""
    orig = _ORIGINAL_DOCUMENT_METHODS.get("selection_range_at_line")
    if orig is None:
        orig = type(self).selection_range_at_line
    res = orig(self, row)
    if res is None:
        return None
    # prompt_toolkit returns LINE-RELATIVE columns `(from_column, to_column)`
    # with an exclusive upper bound (vi mode already adds +1 for inclusion).
    # Extend the highlight over a trailing combining mark of the last
    # selected cell — in line-relative coordinates (the former absolute
    # mix-up produced out-of-mapping columns on rows > 0 and crashed
    # `HighlightSelectionProcessor` with a KeyError; docs/bugs/076.md).
    start, end = res
    selection = self.selection
    if selection is None:
        return start, end
    if vi_mode() and end > start:
        line = self.lines[row]
        if selection.type == SelectionType.BLOCK:
            # Visual rectangle: prompt_toolkit's per-row columns are
            # codepoint columns, which count upper/lower vowels and tone
            # marks — so a block over multiple Thai lines skews instead of
            # covering whole cells (docs/bugs/077.md). The corners define
            # the rectangle in VISUAL columns (cluster ordinals, like Vim):
            # map each row's span back to codepoints, whole cells only.
            from_, to_ = sorted((self.cursor_position, selection.original_cursor_position))
            _, col_from = self.translate_index_to_position(from_)
            _, col_to = self.translate_index_to_position(to_)
            row_from, _ = self.translate_index_to_position(from_)
            row_to, _ = self.translate_index_to_position(to_)
            line_from = self.lines[min(row_from, len(self.lines) - 1)]
            line_to = self.lines[min(row_to, len(self.lines) - 1)]
            v_low = min(_cell_ordinal(line_from, col_from), _cell_ordinal(line_to, col_to))
            v_high = max(_cell_ordinal(line_from, col_from), _cell_ordinal(line_to, col_to))
            start = _start_of_cell_ordinal(line, v_low)
            cell_end = _start_of_cell_ordinal(line, v_high + 1)
            # If the block extends to or past EOL and prompt_toolkit flagged an EOL space
            # (end > len(line)), keep that trailing space marker; otherwise, end strictly
            # at the visual cell boundary (taking max(end, cell_end) unconditionally bled
            # prompt_toolkit's codepoint column into adjacent characters on rows with
            # fewer combining marks than the corner row, e.g. SARA AM; docs/bugs/092.md).
            if end > len(line) and cell_end >= len(line):
                end = end
            else:
                end = cell_end
            return start, end
        # Char selections: keep the highlight on whole cells for the LAST
        # selected cell (docs/bugs/085.md) — whatever cell the inclusive
        # upper bound `end - 1` falls in, extend to the end of its cluster.
        # (The previous mark-only gate missed base cursors mid-cluster —
        # highlight showed `ที่ด` while yank/cut took `ที่ดิ`.)
        cursor_col = end - 1
        if 0 <= cursor_col < len(line):
            cluster_end_col = next_cell_offset(line, cursor_col, 1)
            if cluster_end_col > end:
                end = cluster_end_col
    return start, end


_DOCUMENT_PATCH_SPECS: tuple[tuple[str, tuple[str, ...], Callable[..., object]], ...] = (
    ("get_cursor_left_position", ("self", "count"), _doc_get_cursor_left_position),
    ("get_cursor_right_position", ("self", "count"), _doc_get_cursor_right_position),
    (
        "get_cursor_up_position",
        ("self", "count", "preferred_column"),
        _doc_get_cursor_up_position,
    ),
    (
        "get_cursor_down_position",
        ("self", "count", "preferred_column"),
        _doc_get_cursor_down_position,
    ),
    ("find_next_word_beginning", ("self", "count", "WORD"), find_next_word_beginning),
    (
        "find_next_word_ending",
        ("self", "include_current_position", "count", "WORD"),
        find_next_word_ending,
    ),
    (
        "find_previous_word_beginning",
        ("self", "count", "WORD"),
        find_previous_word_beginning,
    ),
    (
        "find_previous_word_ending",
        ("self", "count", "WORD"),
        find_previous_word_ending,
    ),
    (
        "find_start_of_previous_word",
        ("self", "count", "WORD", "pattern"),
        find_start_of_previous_word,
    ),
    (
        "find_boundaries_of_current_word",
        ("self", "WORD", "include_leading_whitespace", "include_trailing_whitespace"),
        find_boundaries_of_current_word,
    ),
    ("selection_ranges", ("self",), _thai_selection_ranges),
    ("selection_range_at_line", ("self", "row"), _thai_selection_range_at_line),
)


def _thai_install_selftest() -> None:
    """Validate the patched Document primitives; raise on any mismatch."""
    test_doc = Document("ที่ดิน", cursor_position=0)
    if test_doc.get_cursor_right_position(1) != 3:
        raise RuntimeError(
            "Thai Document self-test failed: Document('ที่ดิน', 0).get_cursor_right_position(1) != 3"
        )


def install_prompt_toolkit_thai_patches() -> bool:
    """
    Install Thai/Lao cell-cluster and renderer patches onto `prompt_toolkit`
    with signature checks, startup self-test, and graceful fallback.
    """
    # Every patch applied in this call, so the except-path can roll back to
    # an entirely unpatched prompt_toolkit (docs/bugs/005.md): (target,
    # attribute, original function) triples.
    applied: list[tuple[Any, str, object]] = []
    try:
        for method_name, expected_params, replacement in _DOCUMENT_PATCH_SPECS:
            current = getattr(Document, method_name, None)
            if current is None:
                raise RuntimeError(f"Document.{method_name} is missing")
            if getattr(current, "_vith_thai_patched", False):
                continue

            sig_params = tuple(inspect.signature(current).parameters.keys())
            if sig_params[: len(expected_params)] != expected_params:
                raise RuntimeError(
                    f"Document.{method_name} signature {sig_params!r} "
                    f"does not match expected {expected_params!r}"
                )

            _ORIGINAL_DOCUMENT_METHODS.setdefault(method_name, current)
            setattr(replacement, "_vith_thai_patched", True)
            setattr(Document, method_name, replacement)
            applied.append((Document, method_name, current))

        # Patch KeyProcessor._fix_vi_cursor_position and _start_timeout.
        kp_fix = getattr(KeyProcessor, "_fix_vi_cursor_position", None)
        if kp_fix is not None and not getattr(kp_fix, "_vith_thai_patched", False):
            kp_params = tuple(inspect.signature(kp_fix).parameters.keys())
            if kp_params[:2] == ("self", "event"):
                _ORIGINAL_KEY_PROCESSOR_METHODS.setdefault("_fix_vi_cursor_position", kp_fix)
                setattr(_kp_fix_vi_cursor_position, "_vith_thai_patched", True)
                setattr(KeyProcessor, "_fix_vi_cursor_position", _kp_fix_vi_cursor_position)
                applied.append((KeyProcessor, "_fix_vi_cursor_position", kp_fix))

        kp_timeout = getattr(KeyProcessor, "_start_timeout", None)
        if kp_timeout is not None and not getattr(kp_timeout, "_vith_thai_patched", False):
            _ORIGINAL_KEY_PROCESSOR_METHODS.setdefault("_start_timeout", kp_timeout)
            wrapped_timeout = _make_kp_start_timeout_wrapper(kp_timeout)
            setattr(wrapped_timeout, "_vith_thai_patched", True)
            setattr(KeyProcessor, "_start_timeout", wrapped_timeout)
            applied.append((KeyProcessor, "_start_timeout", kp_timeout))

        # Patch TextObject.operator_range for Thai/Lao cell clusters.
        if _PtkTextObject is not None:
            _orig_operator_range = getattr(_PtkTextObject, "operator_range", None)
            if _orig_operator_range is not None and not getattr(
                _orig_operator_range, "_vith_thai_patched", False
            ):
                # Register the patch for rollback before installing it, so a
                # failure after this point cannot leave a half-patched
                # `TextObject` behind (the rollback gap from docs/bugs/005.md).
                applied.append((_PtkTextObject, "operator_range", _orig_operator_range))

                def _thai_operator_range(self, document, _orig=_orig_operator_range):
                    start, end = _orig(self, document)
                    doc = document
                    # Snap the lower bound off a combining mark onto its cell start.
                    abs_start = doc.cursor_position + start
                    if 0 <= abs_start < len(doc.text):
                        row, col = doc.translate_index_to_position(abs_start)
                        if row < len(doc.lines):
                            line = doc.lines[row]
                            if col < len(line) and is_combining_char(line[col]):
                                new_col = prev_cell_offset(line, col, 1)
                                new_abs = doc.translate_row_col_to_index(row, new_col)
                                start += new_abs - abs_start
                    # For inclusive motions, extend the upper bound to the end of
                    # the cell cluster instead of a single codepoint.
                    if _PtkTextObjectType is not None and self.type == _PtkTextObjectType.INCLUSIVE:
                        abs_end_char = doc.cursor_position + end - 1
                        if 0 <= abs_end_char < len(doc.text):
                            row, col = doc.translate_index_to_position(abs_end_char)
                            if row < len(doc.lines):
                                line = doc.lines[row]
                                if col < len(line):
                                    cluster_size = next_cell_offset(line, col, 1) - col
                                    if cluster_size > 1:
                                        end += cluster_size - 1
                    return start, end

                setattr(_thai_operator_range, "_vith_thai_patched", True)
                _PtkTextObject.operator_range = _thai_operator_range  # type: ignore[method-assign]

        # Window copy-body and renderer patches. They fail soft (warn + skip)
        # on genuine signature drift; an unexpected raise inside them still
        # reaches the rollback below because each install appends its own
        # (target, attribute, original) triple on success.
        _install_window_copy_body_patch(applied)
        _install_renderer_patch(applied)

        # Startup self-test required by Section 4.1:
        _thai_install_selftest()
    except Exception as exc:
        for target, attribute, orig_fn in applied:
            setattr(target, attribute, orig_fn)
        logger.warning("Falling back to unpatched prompt_toolkit Document: %s", exc)
        return False

    return True
