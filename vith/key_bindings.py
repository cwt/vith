import os
import re

from prompt_toolkit.application import get_app
from prompt_toolkit.filters import (
    Condition,
    has_focus,
    is_multiline,
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

from .thai import current_line_and_col, next_cell_offset, prev_cell_offset

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
    line, col = current_line_and_col(doc)
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
    line, col = current_line_and_col(buff.document)
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
    line, col = current_line_and_col(buff.document)
    span = next_cell_offset(line, col, _positive_count(event.arg)) - col
    if span > 0:
        buff.delete(count=span)
    buff.insert_text(event.data, overwrite=False)
    buff.cursor_position += buff.document.get_cursor_left_position(count=1)
    _clear_counted_insert(buff)
    event.app.vi_state.input_mode = InputMode.NAVIGATION


def _resolve_editor_buffer(editor, buff=None):
    """
    Resolve the `EditorBuffer` owning `buff` via its weak back-reference,
    falling back to `editor.window_arrangement.active_editor_buffer`
    (docs/bugs/152.md).
    """
    if buff is not None:
        eb_ref = getattr(buff, '_vith_editor_buffer_ref', None)
        if eb_ref is not None:
            eb = eb_ref()
            if eb is not None:
                return eb
    try:
        return editor.window_arrangement.active_editor_buffer
    except Exception:
        return None


def _get_buffer_tabstop(editor, buff=None) -> int:
    """
    Return effective tabstop for `buff` (or active buffer), falling back to `editor.tabstop`.
    """
    eb = _resolve_editor_buffer(editor, buff)
    if eb is not None:
        return eb.tabstop
    return getattr(editor, 'tabstop', 8)


def _get_buffer_shiftwidth(editor, buff=None) -> int:
    """
    Return effective shiftwidth for `buff` (or active buffer), following Vim's
    `shiftwidth=0` convention (fall back to effective `tabstop`).
    """
    eb = _resolve_editor_buffer(editor, buff)
    if eb is not None:
        return eb.shiftwidth
    sw = getattr(editor, 'shiftwidth', 0)
    return sw if sw > 0 else _get_buffer_tabstop(editor, buff)


def _insert_indentation(editor, buffer_, width=None):
    """
    Insert one indentation step at `buffer_`'s cursor (`width` or buffer
    tabstop spaces with `editor.expand_tab`, otherwise a literal tab). Shared
    by the `<C-t>` indent binding and the Insert-mode `tab` binding
    (docs/bugs/073.md).
    """
    indent_width = width if width is not None else _get_buffer_tabstop(editor, buffer_)
    if editor.expand_tab:
        buffer_.insert_text(' ' * indent_width)
    else:
        buffer_.insert_text('\t')


def _clear_counted_insert(buff):
    """Drop a pending counted-insert replay, e.g. on a new insert entry (204)."""
    if hasattr(buff, '_vith_insert_repeat'):
        buff._vith_insert_repeat = None


def _net_inserted_text(original, current):
    """
    Text inserted between `original` and `current`.

    Uses the longest common prefix/suffix instead of slicing from a recorded
    start offset, so deleting or backspacing left of that offset cannot make
    the replay slice the wrong region (docs/bugs/204.md).
    """
    prefix = 0
    limit = min(len(original), len(current))
    while prefix < limit and original[prefix] == current[prefix]:
        prefix += 1
    suffix = 0
    while (
        suffix < len(original) - prefix
        and suffix < len(current) - prefix
        and original[len(original) - 1 - suffix] == current[len(current) - 1 - suffix]
    ):
        suffix += 1
    return current[prefix : len(current) - suffix]


def _begin_counted_insert(event, move_right):
    """
    Enter insert mode remembering `event.arg`, so that leaving insert mode
    can replay the typed text (`5i...<Esc>`, docs/bugs/120.md).
    """
    buff = event.current_buffer
    count = _positive_count(event.arg)
    if move_right:
        buff.cursor_position += buff.document.get_cursor_right_position(count=1)
    buff._vith_insert_repeat = (buff.cursor_position, count, buff.text)
    event.app.vi_state.input_mode = InputMode.INSERT


def _leave_insert_with_repeat(event):
    """
    Leave insert mode, replaying the typed region for counted `i`/`a`
    entries and accepting `Ctrl-[` like `Esc` (docs/bugs/119.md, 120.md).
    """
    buff = event.current_buffer
    state = getattr(buff, '_vith_insert_repeat', None)
    buff._vith_insert_repeat = None
    if state is not None:
        _start, count, original = state
        if count > 1:
            inserted = _net_inserted_text(original, buff.text)
            if inserted:
                buff.insert_text(inserted * (count - 1))
    if event.app.vi_state.input_mode in (InputMode.INSERT, InputMode.REPLACE):
        buff.cursor_position += buff.document.get_cursor_left_position()
    event.app.vi_state.input_mode = InputMode.NAVIGATION
    if bool(buff.selection_state):
        buff.exit_selection()


def _record_word_search(editor, event, direction):
    """
    Jump to the next/previous occurrence of the word under the cursor and
    record it in the search state and history (`*`/`#`, docs/bugs/122.md).
    Movement matches the `prompt_toolkit` default; the addition is the
    history entry for `/<Up>`. Bare `:s` uses its own last-substitute pattern
    (docs/bugs/175.md), not this search register.
    """
    buff = event.current_buffer
    word = buff.document.get_word_under_cursor()
    if not word:
        return
    search_state = event.app.current_search_state
    search_state.text = word
    search_state.direction = direction
    editor.clear_hlsearch_suppression()
    try:
        editor.search_buffer.history.append_string(word)
    except OSError:
        pass
    # KeyPressEvent.arg is `str | None`; normalize it like every other counted
    # binding before passing it to Buffer.apply_search (docs/bugs/168.md).
    buff.apply_search(
        search_state, include_current_position=False, count=_positive_count(event.arg)
    )


def _shift_lines(editor, buff, count, indent):
    """
    Indent (`>>`) or unindent (`<<`) `count` lines from the cursor row,
    using buffer's effective `shiftwidth` (falling back to `tabstop` when `0`)
    and `editor.expand_tab` (docs/bugs/118.md).

    Splices only the shifted rows directly by character offset instead of
    copying and joining all `doc.lines` across the entire file (docs/bugs/166.md).
    """
    from prompt_toolkit.document import Document

    width = _get_buffer_shiftwidth(editor, buff)
    doc = buff.document
    text = doc.text
    pos = doc.cursor_position
    start_idx = text.rfind('\n', 0, pos) + 1
    n_rows = _positive_count(count)
    text_len = len(text)
    indent_prefix = (' ' * width) if editor.expand_tab else '\t'

    shifted_lines = []
    cur = start_idx
    end_idx = start_idx
    for _ in range(n_rows):
        nl = text.find('\n', cur)
        line_end = text_len if nl == -1 else nl
        line = text[cur:line_end]
        if indent:
            shifted_lines.append(indent_prefix + line)
        elif line.startswith('\t'):
            shifted_lines.append(line[1:])
        else:
            shifted_lines.append(line[min(len(line) - len(line.lstrip(' ')), width) :])
        end_idx = line_end
        if nl == -1:
            break
        cur = nl + 1

    buff.save_to_undo_stack()
    replacement = '\n'.join(shifted_lines)
    new_text = text[:start_idx] + replacement + text[end_idx:]
    first_shifted = shifted_lines[0]
    cursor_col = len(first_shifted) - len(first_shifted.lstrip())
    buff.document = Document(new_text, cursor_position=start_idx + cursor_col)


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
    in_selection_mode = vi_selection_mode & vi_buffer_focussed

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
        Indent current line by one `shiftwidth` (falling back to `tabstop` when 0).
        """
        b = event.current_buffer

        # Move to start of line.
        pos = b.document.get_start_of_line_position(after_whitespace=True)
        b.cursor_position += pos

        # Insert indentation honoring shiftwidth/expandtab.
        _insert_indentation(editor, b, width=_get_buffer_shiftwidth(editor, b))

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

    @kb.add(':', filter=in_selection_mode)
    def enter_command_mode_from_selection(event):
        """
        Record visual selection line marks (`'<`, `'>`), exit selection, and
        open the command line pre-filled with `'<,'>`.
        """
        buff = event.current_buffer
        doc = buff.document
        start_idx, end_idx = doc.selection_range()
        start_row = doc.translate_index_to_position(start_idx)[0]
        end_row = doc.translate_index_to_position(end_idx)[0]

        eb = editor.window_arrangement.active_editor_buffer or _resolve_editor_buffer(editor, buff)
        if eb is not None:
            eb.visual_marks = (start_row, end_row)

        buff.exit_selection()
        editor.enter_command_mode(initial_text="'<,'>")

    @kb.add(
        'tab',
        filter=in_insert_mode
        & ~has_focus(editor.command_buffer)
        & ~has_focus(editor.search_buffer),
    )
    def insert_indentation(event):
        """
        Insert one indentation step (tabstop spaces with `expand_tab`,
        otherwise a literal tab) anywhere in Insert mode, like Vim. Tab is
        reserved for indentation — completion uses Ctrl-N/Ctrl-P
        (docs/bugs/071.md). The command/search buffers are excluded so Tab
        keeps its prompt-toolkit behavior there (docs/bugs/136.md).
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

    @kb.add('Z', 'Z', filter=in_navigation_mode)
    def write_and_quit_current(event):
        """
        Write the active buffer if modified and quit (`ZZ` is Vim's `:x`,
        docs/bugs/131.md).
        """
        from .commands import write_if_modified_and_quit

        write_if_modified_and_quit(editor, None)
        editor.sync_with_prompt_toolkit()

    @kb.add('Z', 'Q', filter=in_navigation_mode)
    def quit_current_without_saving(event):
        """
        Quit without saving (`ZQ`, Vim `:q!` parity).
        """
        from .commands import quit

        quit(editor, force=True)
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

    @kb.add('x', filter=vi_navigation_mode & ~is_read_only)
    def delete_cell_after_cursor(event):
        """
        Delete `event.arg` cell clusters at/after the cursor on the current line.
        """
        _delete_cell_clusters(event, backward=False)

    @kb.add('X', filter=vi_navigation_mode & ~is_read_only)
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
        line, col = current_line_and_col(buff.document)
        span = next_cell_offset(line, col, _positive_count(event.arg)) - col
        text = buff.delete(count=span) if span > 0 else ''
        event.app.clipboard.set_text(text)
        _clear_counted_insert(buff)
        event.app.vi_state.input_mode = InputMode.INSERT

    # Counted insert entry/exit (docs/bugs/119.md, 120.md): `i`/`a` remember
    # the repeat count, `Esc`/`Ctrl-[` replay the typed text. The text-object
    # exclusion keeps operator-pending `iw`/`aw` (e.g. `diw`) on the
    # prompt_toolkit path.
    @kb.add('i', filter=in_navigation_mode & ~is_read_only & ~vi_waiting_for_text_object_mode)
    def insert_with_count(event):
        """
        Enter insert mode, replaying typed text `event.arg` times on exit.
        """
        _begin_counted_insert(event, move_right=False)

    @kb.add('a', filter=in_navigation_mode & ~is_read_only & ~vi_waiting_for_text_object_mode)
    def append_with_count(event):
        """
        Move one character right and enter insert mode (counted like `i`).
        """
        _begin_counted_insert(event, move_right=True)

    @kb.add('escape', filter=in_insert_mode)
    def leave_insert_with_repeat(event):
        """
        Leave insert mode (`Esc`; `Ctrl-[` arrives as the same byte and
        prompt_toolkit exposes no separate `c-[` key), replaying counted
        inserts.
        """
        _leave_insert_with_repeat(event)

    @kb.add(Keys.Any, filter=vi_replace_single_mode & ~is_read_only)
    def replace_single_cell(event):
        """
        Replace `event.arg` cell clusters at the cursor position with a
        single `event.data` character (Vim `r` semantics; see the helper).
        """
        _replace_cell_clusters(event)

    @kb.add('~', filter=vi_navigation_mode & ~is_read_only & ~tilde_operator_enabled)
    def reverse_case_cell(event):
        """
        Swap case of the current cell cluster and advance one cell cluster.
        """
        buff = event.current_buffer
        line, col = current_line_and_col(buff.document)
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

    # Tabstop-honoring shifts (docs/bugs/118.md): `>>` / `<<` indent using
    # `editor.tabstop` / `expand_tab` instead of the hardcoded 4-space
    # prompt_toolkit operator. Single `>` / `<` with a motion stays on the
    # default path.
    @kb.add('>', '>', filter=in_navigation_mode & ~is_read_only)
    def shift_line_right(event):
        """
        Indent `event.arg` lines from the cursor row by one tabstop.
        """
        _shift_lines(editor, event.current_buffer, event.arg, indent=True)

    @kb.add('<', '<', filter=in_navigation_mode & ~is_read_only)
    def shift_line_left(event):
        """
        Unindent `event.arg` lines from the cursor row by one tabstop.
        """
        _shift_lines(editor, event.current_buffer, event.arg, indent=False)

    # Autoindent-aware newline / open-line bindings (`:set [no]autoindent`,
    # honoring `:set paste` just like Vim).
    @kb.add('enter', filter=in_insert_mode & is_multiline & ~is_read_only & ~in_file_explorer_mode)
    def insert_newline(event):
        """
        Insert a newline, copying leading whitespace when `autoindent` is
        enabled and `paste_mode` is off.
        """
        event.current_buffer.newline(copy_margin=editor.autoindent and not editor.paste_mode)

    @kb.add('o', filter=in_navigation_mode & ~is_read_only)
    def open_line_below(event):
        """
        Open a line below and enter Insert mode, honoring `autoindent` and
        `paste_mode`.
        """
        event.current_buffer.insert_line_below(
            copy_margin=editor.autoindent and not editor.paste_mode
        )
        _clear_counted_insert(event.current_buffer)
        event.app.vi_state.input_mode = InputMode.INSERT

    @kb.add('O', filter=in_navigation_mode & ~is_read_only)
    def open_line_above(event):
        """
        Open a line above and enter Insert mode, honoring `autoindent` and
        `paste_mode`.
        """
        event.current_buffer.insert_line_above(
            copy_margin=editor.autoindent and not editor.paste_mode
        )
        _clear_counted_insert(event.current_buffer)
        event.app.vi_state.input_mode = InputMode.INSERT

    # Buffer-local Vi marks (`m{a-z}`, `'{a-z}`/`'<`/`'>`, `` `{a-z} ``).
    def _jump_to_mark(event, *, linewise):
        mark = event.data
        buff = event.current_buffer
        eb = _resolve_editor_buffer(editor, buff)
        if eb is None:
            return
        doc = buff.document
        line_count = len(doc.lines)
        if not line_count:
            return
        target_row = None
        target_col = 0
        if mark in ('<', '>') and eb.visual_marks is not None:
            target_row = eb.visual_marks[0] if mark == '<' else eb.visual_marks[1]
        elif mark in eb.marks:
            target_row, target_col = eb.marks[mark]
        else:
            return
        row = max(0, min(target_row, line_count - 1))
        line_text = doc.lines[row]
        if linewise:
            col = len(line_text) - len(line_text.lstrip())
        else:
            col = max(0, min(target_col, max(0, len(line_text) - 1)))
        buff.cursor_position = doc.translate_row_col_to_index(row, col)

    @kb.add('m', Keys.Any, filter=in_navigation_mode & ~vi_waiting_for_text_object_mode)
    def set_buffer_mark(event):
        """Record a buffer-local lowercase mark (`m{a-z}`) at the cursor position."""
        mark = event.data
        if len(mark) != 1 or not ('a' <= mark <= 'z'):
            return
        eb = _resolve_editor_buffer(editor, event.current_buffer)
        if eb is None:
            return
        doc = event.current_buffer.document
        eb.marks[mark] = (doc.cursor_position_row, doc.cursor_position_col)

    @kb.add("'", Keys.Any, filter=in_navigation_mode & ~vi_waiting_for_text_object_mode)
    def jump_to_mark_line(event):
        """Jump to the first non-blank character on a marked line (`'{mark}`)."""
        _jump_to_mark(event, linewise=True)

    @kb.add('`', Keys.Any, filter=in_navigation_mode & ~vi_waiting_for_text_object_mode)
    def jump_to_mark_exact(event):
        """Jump to the exact row and column of a mark (`` `{mark} ``)."""
        _jump_to_mark(event, linewise=False)

    # Word-occurrence search with history (docs/bugs/122.md): like the
    # prompt_toolkit `*` / `#` defaults, plus a search-history entry so
    # `/<Up>` recalls the pattern and bare `:s` reuses it.
    @kb.add('*', filter=in_navigation_mode)
    def search_next_occurrence(event):
        """
        Jump to the next occurrence of the word under the cursor.
        """
        from prompt_toolkit.search import SearchDirection

        _record_word_search(editor, event, SearchDirection.FORWARD)

    @kb.add('#', filter=in_navigation_mode)
    def search_previous_occurrence(event):
        """
        Jump to the previous occurrence of the word under the cursor.
        """
        from prompt_toolkit.search import SearchDirection

        _record_word_search(editor, event, SearchDirection.BACKWARD)

    return kb
