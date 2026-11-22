"""
`prompt_toolkit` monkey-patches for Thai (`U+0E00..U+0E7F`) and Lao
(`U+0E80..U+0EFF`) script support in `vith`.

Installs version-gated wrappers onto `Document`, `KeyProcessor`, `TextObject`,
`Window._copy_body`, and `renderer._output_screen_diff` so cursor motion,
visual selections, right-margin combining marks, and differential terminal
rendering (`SARA AM` neighbor invalidation and stray-mark cleanup) operate on
visual cell clusters rather than raw codepoints.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import re
from bisect import bisect_right
from typing import Any, Callable, Iterable

import prompt_toolkit.renderer as ptk_renderer
from prompt_toolkit.clipboard import ClipboardData
from prompt_toolkit.document import Document
from prompt_toolkit.filters.app import vi_mode, vi_navigation_mode, vi_selection_mode
from prompt_toolkit.selection import PasteMode, SelectionType

try:
    from prompt_toolkit.key_binding.bindings.vi import TextObject as _PtkTextObject
    from prompt_toolkit.key_binding.bindings.vi import TextObjectType as _PtkTextObjectType
except Exception:  # pragma: no cover
    _PtkTextObject = None  # type: ignore[assignment,misc]
    _PtkTextObjectType = None  # type: ignore[assignment,misc]
from prompt_toolkit.data_structures import Point
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.key_binding.key_processor import KeyPressEvent, KeyProcessor
from prompt_toolkit.layout.containers import Window
from prompt_toolkit.layout.processors import (
    TabsProcessor,
    Transformation,
    TransformationInput,
    explode_text_fragments,
)
from prompt_toolkit.layout.screen import Char, Screen
from prompt_toolkit.utils import get_cwidth, to_int, to_str

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

from .classify import (
    is_combining_char,
    is_sara_am,
)
from .clusters import (  # noqa: F401
    DEFAULT_TABSTOP,
    _cell_ordinal,
    _end_of_visual_col,
    _line_visual_width,
    _map_column_across_lines,
    _start_of_cell_ordinal,
    current_line_and_col,
    next_cell_offset,
    prev_cell_offset,
    snap_to_cell_start,
)
from .words import (
    _ORIGINAL_DOCUMENT_METHODS,
    _ORIGINAL_KEY_PROCESSOR_METHODS,
    find_boundaries_of_current_word,
    find_next_word_beginning,
    find_next_word_ending,
    find_previous_word_beginning,
    find_previous_word_ending,
    find_start_of_previous_word,
)

__all__ = (
    "install_prompt_toolkit_thai_patches",
    "is_thai_gate_latched",
    "note_document_text",
    "reset_thai_text_gate",
    "reset_thai_text_gate_if_all_buffers_ascii",
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 4. Document Cell-Cluster Cursor Movement Wrappers (Phase 2)
# ---------------------------------------------------------------------------


def _doc_get_cursor_left_position(self: Document, count: int = 1) -> int:
    if count < 0:
        return self.get_cursor_right_position(-count)
    text = self.text
    if text.isascii():
        pos = self.cursor_position
        col = pos - (text.rfind("\n", 0, pos) + 1)
        return -min(col, count)
    line, col = current_line_and_col(self)
    if line.isascii():
        return -min(col, count)
    return prev_cell_offset(line, col, count) - col


def _doc_get_cursor_right_position(self: Document, count: int = 1) -> int:
    if count < 0:
        return self.get_cursor_left_position(-count)
    text = self.text
    if text.isascii():
        pos = self.cursor_position
        line_end = text.find("\n", pos)
        if line_end == -1:
            line_end = len(text)
        return min(count, line_end - pos)
    line, col = current_line_and_col(self)
    if line.isascii():
        return min(count, len(line) - col)
    return next_cell_offset(line, col, count) - col


def _document_tabstop(doc: Document) -> int:
    """
    Resolve the effective `tabstop` for `doc` from the owning `EditorBuffer`
    (when running inside a `vith` `Application`), defaulting to Vim's `8`.
    """
    try:
        from prompt_toolkit.application.current import get_app

        app = get_app()
    except Exception:
        return DEFAULT_TABSTOP

    buff = getattr(app, "current_buffer", None)
    if buff is not None:
        eb_ref = getattr(buff, "_vith_editor_buffer_ref", None)
        if eb_ref is not None:
            eb = eb_ref()
            if eb is not None:
                if buff.document is doc:
                    return max(1, int(eb.tabstop))
                editor = eb.editor
                if editor is not None:
                    for other_eb in editor.window_arrangement.editor_buffers:
                        if other_eb.buffer.document is doc:
                            return max(1, int(other_eb.tabstop))
                return max(1, int(eb.tabstop))
    return DEFAULT_TABSTOP


def _vertical_cursor_position(
    self: Document, target_row: int, preferred_column: int | None = None
) -> int:
    """
    Index delta for moving to `target_row` with the *same visual column*
    (display column honoring combining marks, CJK widths, and tab stops;
    docs/bugs/078.md, 141.md). `preferred_column` is a codepoint column
    recorded on a previous row — for the common live-cursor case
    (`preferred_column is None`, or a column of the current row) the
    conversion via `_cell_ordinal` on the current row is exact; for sticky
    columns recorded on a different row this is a best-effort mapping (the
    sticky-correct path is `ColdTextBuffer.cursor_up/down`).
    """
    column = self.cursor_position_col if preferred_column is None else preferred_column
    tabstop = _document_tabstop(self)
    target_col = _map_column_across_lines(
        self.lines[self.cursor_position_row],
        column,
        self.lines[target_row],
        tabstop=tabstop,
    )
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


def _doc_get_column_cursor_position(self: Document, column: int) -> int:
    """
    Visual-column-aware replacement for `Document.get_column_cursor_position`
    (used by Vim's `{count}|` motion). Maps 0-based visual display column
    `column` onto the starting codepoint of that cell, honoring combining
    marks, CJK widths, and tab stops.
    """
    line, current_column = current_line_and_col(self)
    if column <= 0:
        return -current_column
    target_col = _start_of_cell_ordinal(line, column, tabstop=_document_tabstop(self))
    return target_col - current_column


def _kp_fix_vi_cursor_position(self: KeyProcessor, event: KeyPressEvent) -> None:
    """
    Cell-cluster-aware replacement for `KeyProcessor._fix_vi_cursor_position`.
    In Vi navigation mode, ensures the cursor never rests past EOL or on a
    zero-width combining mark after a key handler runs.
    """
    is_nav = vi_navigation_mode()
    if not is_nav and not vi_selection_mode():
        return

    buff = event.app.current_buffer
    doc = buff.document
    text = doc.text
    pos = doc.cursor_position
    if text.isascii():
        if is_nav and (pos == len(text) or text[pos] == "\n"):
            line_start = text.rfind("\n", 0, pos) + 1
            if pos > line_start:
                preferred_column = buff.preferred_column
                buff.cursor_position = pos - 1
                buff.preferred_column = preferred_column
        return

    preferred_column = buff.preferred_column
    line, col = current_line_and_col(doc)
    if len(line) > 0:
        if is_nav and col == len(line):
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


def is_thai_gate_latched() -> bool:
    """Return whether the sticky Thai/Lao renderer gate is currently latched."""
    return _THAI_GATE.value


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
    Synchronize the sticky Thai-gate latch with currently rendered content
    (docs/bugs/072.md, 082.md, 151.md).

    Called from `Editor.sync_with_prompt_toolkit` (on buffer/tab switches and
    command executions, not per keystroke):

    - Re-latches the gate if any *visible* buffer in the active tab holds
      Thai/Lao text (necessary after decaying while viewing an ASCII tab).
    - Decays the gate to ``False`` when every visible buffer's text, every
      open buffer's location (shown by `TabsControl` and `BufferListOverlay`),
      `editor.message`, `command_buffer`, and `search_buffer` are all ASCII.
    """
    active_tab = getattr(window_arrangement, "active_tab", None)
    visible_buffers = (
        active_tab.visible_editor_buffers()
        if active_tab is not None
        else window_arrangement.editor_buffers
    )

    has_non_ascii_visible = False
    for editor_buffer in visible_buffers:
        text = editor_buffer.buffer.text
        if not text.isascii():
            has_non_ascii_visible = True
            note_document_text(text)

    if not _THAI_GATE.value or has_non_ascii_visible:
        return

    editor = window_arrangement.editor
    # The tab bar (`TabsControl`) renders short names for all tabs and
    # `BufferListOverlay` renders all open buffer locations; keep the gate
    # armed whenever any open buffer has a non-ASCII location (docs/bugs/082.md).
    for editor_buffer in window_arrangement.editor_buffers:
        if editor_buffer.location and not editor_buffer.location.isascii():
            return

    message = getattr(editor, "message", None) or ""
    if not message.isascii():
        return

    for aux_name in ("command_buffer", "search_buffer"):
        aux_buf = getattr(editor, aux_name, None)
        aux_text = getattr(aux_buf, "text", "") if aux_buf is not None else ""
        if aux_text and not aux_text.isascii():
            return

    _THAI_GATE.reset()


def _cell_has_sara_am(cell: Char) -> bool:
    ch = cell.char
    if not ch or ch.isascii():
        return False
    if is_sara_am(ord(ch[0])):
        return True
    return "\u0e33" in ch or "\u0eb3" in ch


def _sanitize_and_invalidate_sara_am(
    new_screen: Screen, previous_screen: Screen | None = None
) -> None:
    """
    1. Clear any stray zero-width combining mark cells (`cell.width == 0 and cell.char != ""`)
       left at the end of `FormattedTextControl` or wrapped lines.
    2. Port `szn` commit `71ba808` (`src/server/render.zig:664-694`): when Thai/Lao
       `SARA AM` (`U+0E33` / `U+0EB3`) at column `x` or its preceding cell `x - 1`
       changes, poison the neighbor in `previous_screen` so `_output_screen_diff`
       re-emits both cells together.
    """
    for y, new_row in new_screen.data_buffer.items():
        # Single pass over `new_row`: clear stray width-0 combining cells and
        # collect columns `x >= 1` holding SARA AM when differential
        # invalidation applies (docs/bugs/114.md, 115.md, 151.md).
        has_prev = previous_screen is not None and y in previous_screen.data_buffer
        sara_am_xs: set[int] = set()
        for x, cell in list(new_row.items()):
            if cell.width == 0 and cell.char != "":
                new_row[x] = Char(char=" ", style=cell.style)
            elif has_prev and x >= 1 and _cell_has_sara_am(cell):
                sara_am_xs.add(x)

        # 2. SARA AM neighbor invalidation for differential rendering.
        if not has_prev or previous_screen is None:
            continue
        prev_row = previous_screen.data_buffer[y]

        for x, cell in prev_row.items():
            if x >= 1 and _cell_has_sara_am(cell):
                sara_am_xs.add(x)
        if not sara_am_xs:
            continue

        for x in sorted(sara_am_xs):
            prev_cell = prev_row.get(x, _SARA_AM_DEFAULT_CHAR)
            cur_cell = new_row.get(x, _SARA_AM_DEFAULT_CHAR)
            prev_left = prev_row.get(x - 1, _SARA_AM_DEFAULT_CHAR)
            cur_left = new_row.get(x - 1, _SARA_AM_DEFAULT_CHAR)
            left_changed = (cur_left.char != prev_left.char) or (cur_left.style != prev_left.style)
            cur_changed = (cur_cell.char != prev_cell.char) or (cur_cell.style != prev_cell.style)
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


def _verify_signature(
    fn: Any,
    expected_prefix: tuple[str, ...],
    required_names: tuple[str, ...] = (),
) -> tuple[str, ...] | None:
    """
    Verify that `fn` has a signature starting with `expected_prefix` and
    containing all `required_names` (docs/bugs/111.md, docs/bugs/113.md).
    Returns the parameter name tuple on match, or `None` on mismatch/uninspectable.
    """
    try:
        params = tuple(inspect.signature(fn).parameters.keys())
    except (TypeError, ValueError):
        return None
    if len(params) < len(expected_prefix) or params[: len(expected_prefix)] != expected_prefix:
        return None
    if any(name not in params for name in required_names):
        return None
    return params


def _apply_patch(
    target: Any,
    attr_name: str,
    wrapper: Any,
    orig_fn: Any,
    applied: list[tuple[Any, str, object]] | None = None,
    registry: dict[str, Callable[..., Any]] | None = None,
) -> None:
    """
    Mark `wrapper` as patched, install it on `target`, and record `orig_fn`
    in `registry` and `applied` for rollback (docs/bugs/113.md).
    """
    if registry is not None:
        registry.setdefault(attr_name, orig_fn)
    setattr(wrapper, "_vith_thai_patched", True)
    setattr(wrapper, "_vith_original", orig_fn)
    setattr(target, attr_name, wrapper)
    if applied is not None:
        applied.append((target, attr_name, orig_fn))


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

    # Verify the positional prefix unpacked directly by `_patched_copy_body`
    # and require `has_focus` to be present anywhere in the parameter list so
    # `_named_arg_resolver` can bind it even if upstream inserts new parameters
    # (docs/bugs/111.md).
    expected_prefix = (
        "self",
        "ui_content",
        "new_screen",
        "write_position",
        "move_x",
        "width",
    )
    params = _verify_signature(orig_copy_body, expected_prefix, required_names=("has_focus",))
    if params is None:
        logger.warning("Unexpected Window._copy_body signature; skipping right-margin patch.")
        return False
    resolve_arg = _named_arg_resolver(list(params), is_method=True)

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

        # O(min(len(data_buffer), window_height)) fast-path (docs/bugs/114.md):
        # only inspect `rowcol_to_yx` if at least one populated visible row
        # actually has a non-ASCII base character in the rightmost column.
        candidate_rows: set[int] = set()
        y_end = ypos + write_position.height
        rows_to_check = (
            (sy for sy in data_buffer if ypos <= sy < y_end)
            if len(data_buffer) < write_position.height
            else (sy for sy in range(ypos, y_end) if sy in data_buffer)
        )
        for sy in rows_to_check:
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
            cell = (
                data_buffer.get(sy, {}).get(sx) if isinstance(data_buffer.get(sy), dict) else None
            )
            if cell is None:
                continue
            if sx != right_x and not (
                width >= 2 and sx == right_x - 1 and getattr(cell, 'width', 0) == 2
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
                prev_char = cell
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

    _apply_patch(Window, "_copy_body", _patched_copy_body, orig_copy_body, applied)
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

    # Verify that the parameters inspected by `_wrapped_output_screen_diff`
    # exist in `_output_screen_diff`'s signature so `_named_arg_resolver` can
    # bind them by name even if upstream inserts/reorders other parameters
    # (docs/bugs/111.md).
    required_names = (
        "output",
        "screen",
        "previous_screen",
        "is_done",
        "size",
    )
    params = _verify_signature(orig_diff, ("app",), required_names=required_names)
    if params is None:
        logger.warning("Unexpected _output_screen_diff signature; skipping renderer patch.")
        return False
    resolve_arg = _named_arg_resolver(list(params), is_method=False)

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
        _sanitize_and_invalidate_sara_am(screen, previous_screen)

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
                        escapes = screen.zero_width_escapes
                        setdefault = getattr(escapes, 'setdefault', None)
                        if setdefault is not None:
                            zw_row = setdefault(y, {})
                        else:
                            # Exotic mapping without `setdefault` (e.g. a
                            # plain-mapping test double): never direct-index
                            # a missing row (docs/bugs/110.md, 111.md).
                            zw_row = escapes.get(y)
                            if zw_row is None:
                                zw_row = {}
                                escapes[y] = zw_row
                        old_esc = zw_row.get(target_col, "")
                        saved_escapes.append((y, target_col, old_esc))
                        zw_row[target_col] = old_esc + _AUTOWRAP_ARM_TAG

        if not saved_escapes or output is None:
            return orig_diff(*args, **kwargs)

        try:
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
        finally:
            for y, col_idx, old_esc in saved_escapes:
                zw_row = screen.zero_width_escapes.get(y)
                if zw_row is not None:
                    if old_esc:
                        zw_row[col_idx] = old_esc
                    else:
                        zw_row.pop(col_idx, None)

    _apply_patch(
        ptk_renderer, "_output_screen_diff", _wrapped_output_screen_diff, orig_diff, applied
    )
    return True


def _install_tabs_processor_patch(applied: list[tuple[Any, str, object]] | None = None) -> bool:
    """
    Wrap `prompt_toolkit.layout.processors.TabsProcessor.apply_transformation`
    so tab stop position calculation advances according to the visual display
    width of preceding characters (using `get_cwidth`, zero for combining marks,
    two for fullwidth characters) rather than raw codepoint counts (where
    `pos += 1` misaligns tabs following Thai combining characters or CJK text).
    """
    orig_apply = getattr(TabsProcessor, "apply_transformation", None)
    if orig_apply is None:
        logger.warning("TabsProcessor.apply_transformation not found; skipping tabs patch.")
        return False
    if getattr(orig_apply, "_vith_thai_patched", False):
        return True

    expected_prefix = ("self", "ti")
    params = _verify_signature(orig_apply, expected_prefix)
    if params is None:
        logger.warning(
            "Unexpected TabsProcessor.apply_transformation signature; skipping tabs patch."
        )
        return False

    def _patched_apply_transformation(self: Any, ti: TransformationInput) -> Transformation:
        # Fast path: lines without `\t` need no fragment explosion, width scan,
        # `tabstop`/`char2` callback evaluation, or position maps (docs/bugs/156.md).
        raw_fragments = ti.fragments
        has_tab = False
        for fragment in raw_fragments:
            if "\t" in fragment[1]:
                has_tab = True
                break
        if not has_tab:
            return Transformation(raw_fragments)

        tabstop = max(1, to_int(self.tabstop))
        style = self.style

        # Create separator for tabs.
        separator1 = to_str(self.char1)
        separator2 = to_str(self.char2)

        # Transform fragments.
        fragments = explode_text_fragments(raw_fragments)
        n_frags = len(fragments)
        src_to_disp: list[int] = [0] * (n_frags + 2)
        result_fragments: StyleAndTextTuples = []
        append = result_fragments.append
        pos = 0
        visual_col = 0

        for i, fragment_and_text in enumerate(fragments):
            src_to_disp[i] = pos
            ch = fragment_and_text[1]

            if ch == "\t":
                # Calculate how many characters we have to insert based on visual column.
                count = tabstop - (visual_col % tabstop)
                if count == 0:
                    count = tabstop

                # Insert tab.
                append((style, separator1))
                if count > 1:
                    append((style, separator2 * (count - 1)))
                pos += count
                visual_col += count
            else:
                append(fragment_and_text)
                pos += 1
                if _PtkZeroWidthEscape not in fragment_and_text[0]:
                    if ch and " " <= ch <= "~":
                        visual_col += 1
                    else:
                        visual_col += get_cwidth(ch)

        src_to_disp[n_frags] = pos
        # Add `pos+1` to mapping, because the cursor can be right after the
        # line as well.
        src_to_disp[n_frags + 1] = pos + 1

        def source_to_display(from_position: int) -> int:
            "Maps original cursor position to the new one."
            if 0 <= from_position <= n_frags + 1:
                return src_to_disp[from_position]
            return pos

        def display_to_source(display_pos: int) -> int:
            "Maps display cursor position to the original one."
            if display_pos < 0:
                return 0
            idx = bisect_right(src_to_disp, display_pos) - 1
            return idx if idx >= 0 else 0

        return Transformation(
            result_fragments,
            source_to_display=source_to_display,
            display_to_source=display_to_source,
        )

    _apply_patch(
        TabsProcessor,
        "apply_transformation",
        _patched_apply_transformation,
        orig_apply,
        applied,
    )
    return True


# ---------------------------------------------------------------------------
# 6. Version-Gated prompt_toolkit Patch Installer (Section 4.1)
# ---------------------------------------------------------------------------

# The patch-original registries live in `thai_words` (leaf) so both the
# word walkers and the wrappers here share one storage; imported at the
# top of this module (docs/bugs/075.md).


def _visual_block_ordinal_bounds(
    doc: Document, pos_a: int, pos_b: int, tabstop: int | None = None
) -> tuple[int, int]:
    """
    Return `(v_low, v_high)` visual display-column bounds for a visual-block
    selection between document indices `pos_a` and `pos_b`, expanding tabs to
    `tabstop` (docs/bugs/077.md, 113.md, 141.md).
    """
    ts = _document_tabstop(doc) if tabstop is None else tabstop
    from_pos, to_pos = sorted((pos_a, pos_b))
    row_from, col_from = doc.translate_index_to_position(from_pos)
    row_to, col_to = doc.translate_index_to_position(to_pos)
    line_from = doc.lines[min(row_from, len(doc.lines) - 1)]
    line_to = doc.lines[min(row_to, len(doc.lines) - 1)]
    ord_from = _cell_ordinal(line_from, col_from, tabstop=ts)
    ord_to = _cell_ordinal(line_to, col_to, tabstop=ts)
    return min(ord_from, ord_to), max(ord_from, ord_to)


def _thai_selection_ranges(self: Document) -> Iterable[tuple[int, int]]:
    """Thai/tab-aware wrapper for `Document.selection_ranges`."""
    orig = _ORIGINAL_DOCUMENT_METHODS.get("selection_ranges")
    if orig is None:
        # Fallback to the (unpatched) bound method.
        orig = type(self).selection_ranges
    selection = self.selection
    if selection is not None and selection.type == SelectionType.BLOCK and vi_mode():
        # Visual block rectangle in terminal display columns (honoring combining
        # marks, CJK widths, and tab stops — Vim semantics). Computed directly
        # across `from_line..to_line` instead of delegating row filtering to
        # prompt_toolkit's codepoint-length check, which drops shorter tabbed
        # rows whose visual width still crosses `v_low`.
        ts = _document_tabstop(self)
        cursor_pos = self.cursor_position
        original_pos = selection.original_cursor_position
        from_pos, to_pos = sorted((cursor_pos, original_pos))
        from_line, _ = self.translate_index_to_position(from_pos)
        to_line, _ = self.translate_index_to_position(to_pos)
        v_low, v_high = _visual_block_ordinal_bounds(self, cursor_pos, original_pos, tabstop=ts)
        lines = self.lines
        last_line = min(to_line, len(lines) - 1)
        for row in range(from_line, last_line + 1):
            line = lines[row]
            total_width = _line_visual_width(line, tabstop=ts)
            if v_low > total_width:
                continue
            if v_low == total_width:
                eol_idx = self.translate_row_col_to_index(row, len(line))
                yield eol_idx, eol_idx
                continue
            start_col = _start_of_cell_ordinal(line, v_low, tabstop=ts)
            end_col = _end_of_visual_col(line, v_high, tabstop=ts)
            yield (
                self.translate_row_col_to_index(row, start_col),
                self.translate_row_col_to_index(row, end_col),
            )
        return

    for from_, to in orig(self):
        if vi_mode() and to > from_ and to <= len(self.text):
            last_idx = to - 1
            row, col = self.translate_index_to_position(last_idx)
            if row < len(self.lines):
                line = self.lines[row]
                cluster_end_col = next_cell_offset(line, col, 1)
                new_to = self.translate_row_col_to_index(row, cluster_end_col)
                if new_to > to:
                    to = new_to
        yield from_, to


def _thai_selection_range_at_line(self: Document, row: int) -> tuple[int, int] | None:
    """Thai/tab-aware wrapper for `Document.selection_range_at_line`."""
    orig = _ORIGINAL_DOCUMENT_METHODS.get("selection_range_at_line")
    if orig is None:
        orig = type(self).selection_range_at_line
    res = orig(self, row)
    selection = self.selection
    if selection is None:
        return res
    if selection.type == SelectionType.BLOCK and vi_mode():
        # Visual rectangle in terminal display columns (honoring combining
        # marks, CJK widths, and tab stops — Vim semantics). Evaluated across
        # `from_line..to_line` even when `orig` returned `None` due to a row
        # having fewer codepoints than the corner's codepoint column (e.g.
        # tabs expanding to wider display columns).
        from_pos, to_pos = sorted((self.cursor_position, selection.original_cursor_position))
        from_line, _ = self.translate_index_to_position(from_pos)
        to_line, _ = self.translate_index_to_position(to_pos)
        if not (from_line <= row <= to_line):
            return None
        ts = _document_tabstop(self)
        line = self.lines[row]
        v_low, v_high = _visual_block_ordinal_bounds(
            self, self.cursor_position, selection.original_cursor_position, tabstop=ts
        )
        total_width = _line_visual_width(line, tabstop=ts)
        if v_low >= total_width:
            if res is not None and res[1] > len(line):
                return len(line), len(line) + 1
            return None
        start = _start_of_cell_ordinal(line, v_low, tabstop=ts)
        cell_end = _end_of_visual_col(line, v_high, tabstop=ts)
        # If the block extends to or past EOL and prompt_toolkit flagged an EOL
        # space (`res[1] > len(line)`), keep that trailing space marker;
        # otherwise end strictly at the visual cell boundary (docs/bugs/092.md).
        if res is not None and res[1] > len(line) and cell_end >= len(line):
            return start, res[1]
        return start, cell_end

    if res is None:
        return None
    # prompt_toolkit returns LINE-RELATIVE columns `(from_column, to_column)`
    # with an exclusive upper bound (vi mode already adds +1 for inclusion).
    # Extend the highlight over a trailing combining mark of the last
    # selected cell — in line-relative coordinates (docs/bugs/076.md, 085.md).
    start, end = res
    if vi_mode() and end > start:
        line = self.lines[row]
        cursor_col = end - 1
        if 0 <= cursor_col < len(line):
            cluster_end_col = next_cell_offset(line, cursor_col, 1)
            if cluster_end_col > end:
                end = cluster_end_col
    return start, end


def _thai_paste_clipboard_data(
    self: Document,
    data: ClipboardData,
    paste_mode: PasteMode = PasteMode.EMACS,
    count: int = 1,
) -> Document:
    """
    Thai/tab-aware wrapper for `Document.paste_clipboard_data`.

    Aligns visual-block pastes (`SelectionType.BLOCK`) by terminal display
    column (honoring combining marks, CJK widths, and tab stops) instead of
    raw codepoint offsets, and ensures charwise Vi-after paste (`p`) lands
    after the full current cell cluster rather than splitting combining marks.
    """
    orig = _ORIGINAL_DOCUMENT_METHODS.get("paste_clipboard_data")
    if orig is None:
        orig = type(self).paste_clipboard_data

    if data.type == SelectionType.BLOCK:
        if count <= 0:
            return self
        before = paste_mode in (PasteMode.VI_BEFORE, PasteMode.EMACS)
        ts = _document_tabstop(self)
        lines = self.lines[:]
        start_line = self.cursor_position_row
        start_row_text = lines[start_line]
        cur_col = self.cursor_position_col
        if before:
            anchor_col = snap_to_cell_start(start_row_text, cur_col)
        else:
            anchor_col = next_cell_offset(start_row_text, cur_col, 1)
        target_vcol = _cell_ordinal(start_row_text, anchor_col, tabstop=ts)

        new_cursor_col = anchor_col
        for i, chunk in enumerate(data.text.split("\n")):
            index = i + start_line
            if index >= len(lines):
                lines.append("")
            row_text = lines[index]
            row_width = _line_visual_width(row_text, tabstop=ts)
            if row_width < target_vcol:
                pad = " " * (target_vcol - row_width)
                insert_col = len(row_text) + len(pad)
                lines[index] = row_text + pad + chunk * count
            else:
                insert_col = _start_of_cell_ordinal(row_text, target_vcol, tabstop=ts)
                lines[index] = row_text[:insert_col] + chunk * count + row_text[insert_col:]
            if i == 0:
                new_cursor_col = insert_col

        new_text = "\n".join(lines)
        new_cursor_pos = sum(len(row) + 1 for row in lines[:start_line]) + new_cursor_col
        return Document(text=new_text, cursor_position=new_cursor_pos)

    if data.type == SelectionType.CHARACTERS and paste_mode == PasteMode.VI_AFTER:
        line, col = current_line_and_col(self)
        if col < len(line):
            cell_end = next_cell_offset(line, col, 1)
            if cell_end > col + 1:
                insert_at = self.cursor_position + (cell_end - col)
                inserted = data.text * count
                new_text = self.text[:insert_at] + inserted + self.text[insert_at:]
                new_cursor_position = (
                    insert_at + len(inserted) - 1 if inserted else self.cursor_position
                )
                return Document(text=new_text, cursor_position=new_cursor_position)

    return orig(self, data, paste_mode=paste_mode, count=count)


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
    ("get_column_cursor_position", ("self", "column"), _doc_get_column_cursor_position),
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
    (
        "paste_clipboard_data",
        ("self", "data", "paste_mode", "count"),
        _thai_paste_clipboard_data,
    ),
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

            if _verify_signature(current, expected_params) is None:
                raise RuntimeError(
                    f"Document.{method_name} signature does not match expected {expected_params!r}"
                )

            _apply_patch(
                Document,
                method_name,
                replacement,
                current,
                applied,
                _ORIGINAL_DOCUMENT_METHODS,
            )

        # Patch KeyProcessor._fix_vi_cursor_position and _start_timeout.
        kp_fix = getattr(KeyProcessor, "_fix_vi_cursor_position", None)
        if kp_fix is not None and not getattr(kp_fix, "_vith_thai_patched", False):
            if _verify_signature(kp_fix, ("self", "event")) is not None:
                _apply_patch(
                    KeyProcessor,
                    "_fix_vi_cursor_position",
                    _kp_fix_vi_cursor_position,
                    kp_fix,
                    applied,
                    _ORIGINAL_KEY_PROCESSOR_METHODS,
                )

        kp_timeout = getattr(KeyProcessor, "_start_timeout", None)
        if kp_timeout is not None and not getattr(kp_timeout, "_vith_thai_patched", False):
            wrapped_timeout = _make_kp_start_timeout_wrapper(kp_timeout)
            _apply_patch(
                KeyProcessor,
                "_start_timeout",
                wrapped_timeout,
                kp_timeout,
                applied,
                _ORIGINAL_KEY_PROCESSOR_METHODS,
            )

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
                    # Block selections are inclusive at both corners in Vi mode,
                    # but prompt_toolkit's `TextObject.operator_range` omits
                    # `end += 1` for `BLOCK` while `TextObject.cut` still runs
                    # `to -= 1`, shifting the upper corner one codepoint left
                    # (dropping the last column on `<C-v>...y`/`d`). Include the
                    # upper-corner char (without crossing a newline) so `cut`
                    # lands back on the exact corner.
                    if _PtkTextObjectType is not None and self.type == _PtkTextObjectType.BLOCK:
                        abs_end = doc.cursor_position + end
                        if 0 <= abs_end < len(doc.text) and doc.text[abs_end] != "\n":
                            end += 1
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

        # Window copy-body, renderer, and tabs-processor patches. They fail soft (warn + skip)
        # on genuine signature drift; an unexpected raise inside them still
        # reaches the rollback below because each install appends its own
        # (target, attribute, original) triple on success.
        _install_window_copy_body_patch(applied)
        _install_renderer_patch(applied)
        _install_tabs_processor_patch(applied)

        # Startup self-test required by Section 4.1:
        _thai_install_selftest()
    except Exception as exc:
        for target, attribute, orig_fn in applied:
            setattr(target, attribute, orig_fn)
        logger.warning("Falling back to unpatched prompt_toolkit Document: %s", exc)
        return False

    return True
