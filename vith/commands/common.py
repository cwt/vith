"""
Shared constants and helper utilities used across Ex command submodules.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterable
from typing import Any

from prompt_toolkit.clipboard import ClipboardData
from prompt_toolkit.selection import SelectionType

from .ranges import (
    MODIFIABLE_OFF_TEXT,
    is_buffer_read_only,
    replace_buffer_lines,
    resolve_range_str,
    splice_buffer_lines,
)

NO_WRITE_SINCE_LAST_CHANGE_TEXT = 'No write since last change (add ! to override)'
E37_NO_WRITE_TEXT = 'E37: No write since last change (add ! to override)'
NO_FILE_NAME_TEXT = 'No file name'
SHELL_TIMEOUT = 30


def press_enter_to_continue() -> None:
    """
    Wait for the user to press Enter, degrading gracefully on EOF or
    interrupt (non-TTY stdin; used by `vith.rc_file`, docs/bugs/113.md,
    docs/bugs/115.md).
    """
    try:
        input('\nPress ENTER to continue...')
    except (EOFError, KeyboardInterrupt, OSError):
        pass


def confirm_unsaved_changes(editor: Any, force: bool) -> bool:
    """
    Standard unsaved-changes guard for buffer switch/close/edit commands
    (shared by `:b`, `:bn`, `:bp`, `:bd`/`:bw` and `:e`; docs/bugs/073.md).

    Returns `True` when the operation should proceed.
    """
    eb = editor.window_arrangement.active_editor_buffer
    if eb is None:
        return True
    if not force and eb.has_unsaved_changes:
        editor.show_message(NO_WRITE_SINCE_LAST_CHANGE_TEXT)
        return False
    return True


def buffer_visible_count(window_arrangement: Any, editor_buffer: Any) -> int:
    """Number of windows currently showing `editor_buffer` (across all tabs)."""
    return sum(
        1
        for tab in window_arrangement.tab_pages
        for win in tab.windows()
        if win.editor_buffer is editor_buffer
    )


def has_sole_dirty_buffer(
    window_arrangement: Any,
    windows: Iterable[Any],
    *,
    exclude_eb: Any = None,
) -> bool:
    """
    Return whether any window in `windows` (excluding `exclude_eb`) holds the
    sole visible view of a buffer with unsaved changes.
    """
    for win in windows:
        eb = win.editor_buffer
        if (
            eb is not None
            and eb is not exclude_eb
            and eb.has_unsaved_changes
            and buffer_visible_count(window_arrangement, eb) <= 1
        ):
            return True
    return False


def active_writable_buffer(editor: Any) -> Any | None:
    """
    Return the active `EditorBuffer` if present and writable; report `E21`
    and return `None` when read-only.
    """
    eb = editor.window_arrangement.active_editor_buffer
    if eb is None:
        return None
    if is_buffer_read_only(eb.buffer):
        editor.show_message(MODIFIABLE_OFF_TEXT)
        return None
    return eb


def resolve_command_range(
    editor: Any,
    eb: Any,
    range_str: str | None,
    *,
    default_range: str = '.',
) -> tuple[int, int] | None:
    """
    Resolve `range_str` on `eb`, reporting any range error to `editor`.
    """
    bounds, err = resolve_range_str(range_str, eb, default_range=default_range)
    if err is not None or bounds is None:
        editor.show_message(err)
        return None
    return bounds


def invalidate_app() -> None:
    """Request a prompt_toolkit redraw when an active application exists."""
    try:
        from prompt_toolkit.application.current import get_app

        get_app().invalidate()
    except Exception:
        pass


def show_info_lines(
    editor: Any,
    lines: list[str],
    *,
    title: str,
    empty_message: str,
) -> None:
    """Display `lines` in a read-only split, or show `empty_message` if empty."""
    if not lines:
        editor.show_message(empty_message, record=False)
        return
    editor.show_info('\n'.join(lines), title=title)


def run_shell(editor: Any, cmd: str, payload: str | None = None) -> Any | None:
    """
    Run a shell command with byte-preserving UTF-8 I/O (docs/bugs/137.md).

    Shell output is arbitrary bytes: decoding with the locale's strict policy
    used to raise `UnicodeDecodeError` out of the command handler. Using
    `surrogateescape` on both directions keeps every byte round-trippable
    without ever crashing the TUI. Returns `None` (after a message) on timeout,
    decode failure or launch error.
    """
    try:
        return subprocess.run(
            cmd,
            shell=True,
            input=payload,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding='utf-8',
            errors='surrogateescape',
            timeout=SHELL_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        editor.show_message(f'Command timed out after {SHELL_TIMEOUT}s: {cmd}')
    except UnicodeError as exc:
        editor.show_message(f'Cannot decode shell command output: {exc}')
    except OSError as exc:
        editor.show_message(f'Cannot execute shell command: {exc}')
    return None


def split_text_to_lines(raw_text: str) -> tuple[str, list[str]]:
    """
    Normalize CRLF line endings to LF, strip a single trailing newline, and
    split into lines. An empty string yields `('', [])`.
    """
    normalized = raw_text.replace('\r\n', '\n')
    if normalized == '':
        return '', []
    if normalized.endswith('\n'):
        normalized = normalized[:-1]
    return normalized, normalized.split('\n')


def splice_lines_at(
    editor: Any,
    eb: Any,
    insert_index: int,
    segment: list[str],
    *,
    cursor_at_end: bool = False,
    inserted_text: str = '',
) -> bool:
    """
    Splice `segment` into `eb` at `insert_index` (replacing the lone empty
    line when `eb` is currently empty) without copying untouched buffer lines.
    """
    if eb.buffer.text == '':
        start_row, end_row, first_row = 0, 0, 0
    else:
        start_row, end_row, first_row = insert_index, insert_index - 1, insert_index
    cursor_row = first_row + len(segment) - 1 if cursor_at_end else first_row
    return splice_buffer_lines(
        editor,
        eb,
        start_row,
        end_row,
        segment,
        cursor_row,
        first_non_blank=True,
        inserted_text=inserted_text,
    )


def store_lines_in_register(editor: Any, lines: list[str], register: str = '') -> None:
    """Store `lines` as linewise text in named `register` or the unnamed clipboard."""
    text = '\n'.join(lines)
    if register:
        editor.registers[register] = text
    # Vim updates the unnamed register as well when a named register is given,
    # so a plain `p` pastes the just-yanked/deleted line (docs/bugs/187.md).
    editor.application.clipboard.set_data(ClipboardData(text, SelectionType.LINES))


def report_yanked_lines(editor: Any, count: int) -> None:
    """Display the Vim-style yank summary message."""
    line_word = 'line' if count == 1 else 'lines'
    editor.show_message(f'{count} {line_word} yanked')


def apply_deleted_lines(
    editor: Any,
    eb: Any,
    remaining: list[str],
    deleted: list[str],
    target_row: int,
    *,
    register: str = '',
) -> None:
    """
    Save `deleted` lines into `register`/clipboard, replace buffer lines with
    `remaining`, and report multi-line deletions (`N fewer lines`).
    """
    store_lines_in_register(editor, deleted, register=register)
    if not remaining:
        remaining = ['']
    clamped_row = min(target_row, len(remaining) - 1)
    if not replace_buffer_lines(editor, eb, remaining, clamped_row, first_non_blank=True):
        return
    if len(deleted) >= 2:
        editor.show_message(f'{len(deleted)} fewer lines')


def apply_deleted_range(
    editor: Any,
    eb: Any,
    start_row: int,
    end_row: int,
    deleted: list[str],
    *,
    register: str = '',
) -> None:
    """
    Save contiguous `deleted` lines into `register`/clipboard, splice rows
    `start_row..end_row` out of `eb`, and report multi-line deletions.
    """
    store_lines_in_register(editor, deleted, register=register)
    if not splice_buffer_lines(editor, eb, start_row, end_row, [], start_row, first_non_blank=True):
        return
    if len(deleted) >= 2:
        editor.show_message(f'{len(deleted)} fewer lines')
