import os
import re

from prompt_toolkit.application import get_app
from prompt_toolkit.filters import (
    Condition,
    has_focus,
    is_read_only,
    vi_insert_mode,
    vi_navigation_mode,
    vi_selection_mode,
)
from prompt_toolkit.filters.app import (
    vi_replace_single_mode,
    vi_waiting_for_text_object_mode,
)
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.key_binding.vi_state import InputMode
from prompt_toolkit.keys import Keys

from .thai import next_cell_offset, prev_cell_offset

__all__ = ('create_key_bindings',)


def _keep_keysync_disabled(event):
    """
    Never record the partially-typed key buffer with the redo binding
    (progressive typing should stay redo-friendly).
    """
    return False


def _positive_count(arg: int | None) -> int:
    """Normalize a Vi repeat argument (`None`, `0`, or negative) to `>= 1`."""
    return max(1, int(arg or 1))


def _end_of_line_cell_position(doc, count):
    """
    Absolute cursor position of the start of the last cell cluster on the
    line `count` rows below the current one (Vim's counted `$`).
    """
    count = _positive_count(count)
    target_row = min(len(doc.lines) - 1, doc.cursor_position_row + count - 1)
    line = doc.lines[target_row]
    col = prev_cell_offset(line, len(line), 1)
    return doc.translate_row_col_to_index(target_row, col)


def _snap_word_end_to_cell(doc, position):
    """
    Given an absolute position produced by `find_next_word_ending` (the cell
    behind the exclusive word end), return the absolute position of its base
    character. The backward cell walk is applied *within that word's line*
    (`prev_cell_offset` has a line-local `line, col` contract).
    """
    row, col = doc.translate_index_to_position(position)
    col = prev_cell_offset(doc.lines[row], col, 1)
    return doc.translate_row_col_to_index(row, col)


def _name_under_cursor(doc):
    """
    Return the whitespace-delimited token under (or nearest to) the cursor of
    `doc` (``''`` when the line has no tokens). For the file explorer this is
    the file/directory name under the cursor, not the whole line — lines may
    list several entries (docs/bugs/071.md).
    """
    line = doc.current_line
    col = doc.cursor_position_col
    last_token = ''
    for match in re.finditer(r'\S+', line):
        last_token = match.group(0)
        if col <= match.end():
            return last_token
    return last_token


def _delete_cell_clusters(event, backward):
    """
    Delete `event.arg` cell clusters before/after the cursor on the current line.
    """
    buff = event.current_buffer
    doc = buff.document
    line = doc.current_line
    col = doc.cursor_position_col
    count = _positive_count(event.arg)
    if backward:
        span = col - prev_cell_offset(line, col, count)
    else:
        span = next_cell_offset(line, col, count) - col
    if span > 0:
        text = buff.delete_before_cursor(count=span) if backward else buff.delete(count=span)
        event.app.clipboard.set_text(text)


def _replace_cell_clusters(event):
    """
    Replace `event.arg` cell clusters at the cursor position with a single
    `event.data` character (Vim `r` semantics: `2rx` replaces two characters
    with one `x`; the cursor lands on the replacement).
    """
    buff = event.current_buffer
    doc = buff.document
    line = doc.current_line
    col = doc.cursor_position_col
    span = next_cell_offset(line, col, _positive_count(event.arg)) - col
    if span > 0:
        buff.delete(count=span)
    buff.insert_text(event.data, overwrite=False)
    buff.cursor_position += buff.document.get_cursor_left_position(count=1)
    event.app.vi_state.input_mode = InputMode.NAVIGATION


def _insert_indentation(editor, buffer_):
    """
    Insert one indentation step at `buffer_`'s cursor (tabstop spaces with
    `editor.expand_tab`, otherwise a literal tab). Shared by the `<C-t>`
    indent binding and the Insert-mode `tab` binding (docs/bugs/073.md).
    """
    if editor.expand_tab:
        buffer_.insert_text(' ' * editor.tabstop)
    else:
        buffer_.insert_text('\t')


@Condition
def whitespace_before_cursor_on_line():
    """
    Filter which evaluates to True when the characters before the cursor are
    whitespace, or we are at the start of the line.
    """
    b = get_app().current_buffer
    before_cursor = b.document.current_line_before_cursor

    return bool(not before_cursor or before_cursor[-1].isspace())


@Condition
def tilde_operator_enabled():
    "True while `:set tildeop` is active."
    return bool(get_app().vi_state.tilde_operator)


def create_key_bindings(editor):
    """
    Create custom key bindings.

    This starts with the key bindings, defined by `prompt-toolkit`, but adds
    the ones which are specific for the editor.
    """
    kb = KeyBindings()

    # Filters.
    @Condition
    def vi_buffer_focussed():
        app = get_app()
        if app.layout.has_focus(editor.search_buffer) or app.layout.has_focus(
            editor.command_buffer
        ):
            return False
        return True

    @Condition
    def command_buffer_is_empty():
        return editor.command_buffer.text == ''

    @Condition
    def in_file_explorer_mode():
        return bool(
            editor.current_editor_buffer and editor.current_editor_buffer.in_file_explorer_mode
        )

    in_insert_mode = vi_insert_mode & vi_buffer_focussed
    in_navigation_mode = vi_navigation_mode & vi_buffer_focussed

    @kb.add('c-t', filter=~in_insert_mode)
    def disable_default_transpose_chars(event):
        """
        Suppress prompt_toolkit's default Control-T (`transpose-chars`) outside
        document insert mode, while `indent_line` below handles `in_insert_mode`.
        """
        pass

    @kb.add('c-t', filter=in_insert_mode)
    def indent_line(event):
        """
        Indent current line.
        """
        b = event.current_buffer

        # Move to start of line.
        pos = b.document.get_start_of_line_position(after_whitespace=True)
        b.cursor_position += pos

        # Insert tab (shared with the Insert-mode `tab` binding).
        _insert_indentation(editor, b)

        # Restore cursor.
        b.cursor_position -= pos

    @kb.add('c-r', filter=in_navigation_mode, save_before=_keep_keysync_disabled)
    def redo(event):
        """
        Redo.
        """
        event.app.current_buffer.redo()

    @kb.add(':', filter=in_navigation_mode)
    def enter_command_mode(event):
        """
        Entering command mode.
        """
        editor.enter_command_mode()

    @kb.add(
        'tab',
        filter=vi_insert_mode
        & ~has_focus(editor.command_buffer)
        & whitespace_before_cursor_on_line,
    )
    def insert_indentation(event):
        """
        Insert one indentation step (tabstop spaces with `expand_tab`,
        otherwise a literal tab). Despite the whitespace-only-before-cursor
        filter this deliberately does NOT trigger autocompletion — Tab is
        reserved for indentation here (docs/bugs/071.md).
        """
        _insert_indentation(editor, event.app.current_buffer)

    @kb.add('escape', filter=has_focus(editor.command_buffer))
    @kb.add('c-c', filter=has_focus(editor.command_buffer))
    @kb.add(
        'backspace',
        filter=has_focus(editor.command_buffer) & command_buffer_is_empty,
    )
    def leave_command_mode(event):
        """
        Leaving command mode.
        """
        editor.leave_command_mode()

    @kb.add('c-w', 'c-w', filter=in_navigation_mode)
    def focus_next_window(event):
        editor.window_arrangement.cycle_focus()
        editor.sync_with_prompt_toolkit()

    @kb.add('c-w', 'n', filter=in_navigation_mode)
    def horizontal_split(event):
        """
        Split horizontally.
        """
        editor.window_arrangement.hsplit(None)
        editor.sync_with_prompt_toolkit()

    @kb.add('c-w', 'v', filter=in_navigation_mode)
    def vertical_split(event):
        """
        Split vertically.
        """
        editor.window_arrangement.vsplit(None)
        editor.sync_with_prompt_toolkit()

    @kb.add('g', 't', filter=in_navigation_mode)
    def focus_next_tab(event):
        editor.window_arrangement.go_to_next_tab()
        editor.sync_with_prompt_toolkit()

    @kb.add('g', 'T', filter=in_navigation_mode)
    def focus_previous_tab(event):
        editor.window_arrangement.go_to_previous_tab()
        editor.sync_with_prompt_toolkit()

    @kb.add('f1')
    def show_help(event):
        editor.show_help()

    @kb.add('enter', filter=in_file_explorer_mode)
    def open_path(event):
        """
        Open file/directory in file explorer mode.
        """
        current_editor_buffer = editor.current_editor_buffer
        if current_editor_buffer is None:
            # Transient focus state (docs/bugs/066.md): nothing to open.
            return
        name_under_cursor = _name_under_cursor(event.current_buffer.document)
        if not name_under_cursor.strip() or name_under_cursor.lstrip().startswith('"'):
            return
        new_path = os.path.normpath(os.path.join(current_editor_buffer.location, name_under_cursor))

        editor.window_arrangement.open_buffer(new_path, show_in_current_window=True)
        editor.sync_with_prompt_toolkit()

    @kb.add('-', filter=in_file_explorer_mode)
    def to_parent_directory(event):
        current_editor_buffer = editor.current_editor_buffer
        if current_editor_buffer is None:
            return
        new_path = os.path.normpath(os.path.join(current_editor_buffer.location, '..'))

        editor.window_arrangement.open_buffer(new_path, show_in_current_window=True)
        editor.sync_with_prompt_toolkit()

    # Cell-cluster Vi editing and motion overrides (Phase 3):
    # Ensure `x`, `X`, `s`, `r`, `~`, `$`, `e`, and `E` operate on whole
    # visual cell clusters (`base + comb*`) rather than slicing raw codepoints.

    @kb.add('x', filter=vi_navigation_mode)
    def delete_cell_after_cursor(event):
        """
        Delete `event.arg` cell clusters at/after the cursor on the current line.
        """
        _delete_cell_clusters(event, backward=False)

    @kb.add('X', filter=vi_navigation_mode)
    def delete_cell_before_cursor(event):
        """
        Delete `event.arg` cell clusters before the cursor on the current line.
        """
        _delete_cell_clusters(event, backward=True)

    @kb.add('s', filter=vi_navigation_mode & ~is_read_only)
    def substitute_cell(event):
        """
        Substitute `event.arg` cell clusters at the cursor and enter insert mode.
        """
        buff = event.current_buffer
        doc = buff.document
        line = doc.current_line
        col = doc.cursor_position_col
        span = next_cell_offset(line, col, _positive_count(event.arg)) - col
        text = buff.delete(count=span) if span > 0 else ''
        event.app.clipboard.set_text(text)
        event.app.vi_state.input_mode = InputMode.INSERT

    @kb.add(Keys.Any, filter=vi_replace_single_mode)
    def replace_single_cell(event):
        """
        Replace `event.arg` cell clusters at the cursor position with a
        single `event.data` character (Vim `r` semantics; see the helper).
        """
        _replace_cell_clusters(event)

    @kb.add('~', filter=vi_navigation_mode & ~tilde_operator_enabled)
    def reverse_case_cell(event):
        """
        Swap case of the current cell cluster and advance one cell cluster.
        """
        buff = event.current_buffer
        doc = buff.document
        line = doc.current_line
        col = doc.cursor_position_col
        if col >= len(line):
            return
        end_col = next_cell_offset(line, col, _positive_count(event.arg))
        span = end_col - col
        if span > 0:
            cluster = line[col:end_col]
            buff.delete(count=span)
            buff.insert_text(cluster.swapcase(), overwrite=False)

    @kb.add('$', filter=(vi_navigation_mode | vi_selection_mode) & ~vi_waiting_for_text_object_mode)
    def move_to_end_of_line_cell(event):
        """
        Move cursor to the start of the last cell cluster `event.arg` lines
        below the current one (Vim counted `$`).
        Operator-pending `$` (`d$`, `c$`, `y$`) remains handled by `vi.py` so
        that trailing combining marks are included in the operator range.
        """
        buff = event.current_buffer
        doc = buff.document
        buff.cursor_position = _end_of_line_cell_position(doc, _positive_count(event.arg))

    def _move_to_end_of_word_cell(event, WORD):
        "Move cursor to the base character of the last cell cluster of the word."
        buff = event.current_buffer
        doc = buff.document
        end = doc.find_next_word_ending(count=_positive_count(event.arg), WORD=WORD)
        if end:
            buff.cursor_position = _snap_word_end_to_cell(doc, buff.cursor_position + end)

    @kb.add('e', filter=(vi_navigation_mode | vi_selection_mode) & ~vi_waiting_for_text_object_mode)
    def move_to_end_of_word_cell(event):
        """
        Move cursor to the base character of the last cell cluster of the word.
        """
        _move_to_end_of_word_cell(event, WORD=False)

    @kb.add('E', filter=(vi_navigation_mode | vi_selection_mode) & ~vi_waiting_for_text_object_mode)
    def move_to_end_of_big_word_cell(event):
        """
        Move cursor to the base character of the last cell cluster of the WORD.
        """
        _move_to_end_of_word_cell(event, WORD=True)

    return kb
