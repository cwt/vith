"""
Buffer line-editing Ex commands (`:u`, `:red`, `:d`, `:y`, `:pu`, `:m`,
`:t`/`:co`, `:j`, `:sort`, `:retab`).
"""

from __future__ import annotations

import re
from typing import Any

from vith.thai import note_document_text

from .common import (
    active_writable_buffer,
    apply_deleted_lines,
    insert_lines_at,
    report_yanked_lines,
    resolve_command_range,
    split_text_to_lines,
    store_lines_in_register,
)
from .ranges import parse_range, replace_buffer_lines, resolve_insert_address
from .registry import _cmd, cmd


@cmd('u')
@cmd('undo')
def undo_command(editor: Any) -> None:
    """
    Undo the latest change in the active buffer (`:u` / `:undo`).
    """
    eb = active_writable_buffer(editor)
    if eb is None:
        return

    before_text = eb.buffer.text
    eb.buffer.undo()
    if eb.buffer.text == before_text:
        editor.show_message('Already at oldest change')
    else:
        note_document_text(eb.buffer.text)


@cmd('red')
@cmd('redo')
def redo_command(editor: Any) -> None:
    """
    Redo the latest undone change in the active buffer (`:red` / `:redo`).
    """
    eb = active_writable_buffer(editor)
    if eb is None:
        return

    redo_stack = getattr(eb.buffer, '_redo_stack', None)
    if not redo_stack:
        editor.show_message('Already at newest change')
        return

    before_text = eb.buffer.text
    while redo_stack:
        eb.buffer.redo()
        if eb.buffer.text != before_text:
            note_document_text(eb.buffer.text)
            return

    editor.show_message('Already at newest change')


def _register_name(variables: Any, editor: Any) -> str | None:
    """
    Resolve a Vim register name from an Ex command argument (docs/bugs/147.md).
    Returns `''` for the unnamed register, a single-character name, or None
    when the argument is invalid (after reporting E488).
    """
    raw = variables.get('cmd_arg')
    if raw is None or str(raw).strip() == '':
        return ''
    name = str(raw).strip()
    if len(name) == 1 and (name.isalnum() or name in ('"', '0')):
        return name
    editor.show_message(f'E488: Trailing characters: {raw}')
    return None


@_cmd('delete')
@_cmd('d')
def delete_lines(editor: Any, variables: Any) -> None:
    """
    Delete addressed lines into a register (`:[range]d [reg]`).
    """
    if variables.get('force'):
        editor.show_message('No ! allowed')
        return
    eb = active_writable_buffer(editor)
    if eb is None:
        return

    bounds = resolve_command_range(editor, eb, variables.get('cmd_range'), default_range='.')
    if bounds is None:
        return

    register = _register_name(variables, editor)
    if register is None:
        return

    lines = list(eb.buffer.document.lines)
    start_row, end_row = bounds
    deleted = lines[start_row : end_row + 1]
    remaining = lines[:start_row] + lines[end_row + 1 :]
    apply_deleted_lines(editor, eb, remaining, deleted, start_row, register=register)


@_cmd('yank')
@_cmd('y')
def yank_lines(editor: Any, variables: Any) -> None:
    """
    Copy addressed lines into a register (`:[range]y [reg]`).
    """
    if variables.get('force'):
        editor.show_message('No ! allowed')
        return
    eb = editor.window_arrangement.active_editor_buffer
    if eb is None:
        return

    bounds = resolve_command_range(editor, eb, variables.get('cmd_range'), default_range='.')
    if bounds is None:
        return

    register = _register_name(variables, editor)
    if register is None:
        return

    lines = eb.buffer.document.lines
    start_row, end_row = bounds
    yanked = list(lines[start_row : end_row + 1])
    store_lines_in_register(editor, yanked, register=register)
    report_yanked_lines(editor, len(yanked))


@_cmd('put')
@_cmd('pu')
def put_lines(editor: Any, variables: Any) -> None:
    """
    Insert clipboard text after the addressed line, or before with `!`
    (`:[addr]pu[!]`).
    """
    register = _register_name(variables, editor)
    if register is None:
        return
    eb = active_writable_buffer(editor)
    if eb is None:
        return

    doc = eb.buffer.document
    range_start, range_end = parse_range(variables.get('cmd_range'))
    addr_token = range_end or range_start
    if addr_token == '%':
        addr_token = '$'

    insert_index, err = resolve_insert_address(
        addr_token,
        doc.cursor_position_row,
        len(doc.lines),
        getattr(eb, 'visual_marks', None),
    )
    if err is not None or insert_index is None:
        editor.show_message(err)
        return

    if variables.get('force'):
        insert_index = max(0, insert_index - 1)

    if register:
        raw_text = editor.registers.get(register)
        if raw_text is None:
            editor.show_message('E353: Nothing in register %s' % register)
            return
    else:
        clip_data = editor.application.clipboard.get_data()
        if clip_data is None or clip_data.text == '':
            editor.show_message('E353: Nothing in register "')
            return
        raw_text = clip_data.text

    clip_text, segment = split_text_to_lines(raw_text)
    if not segment:
        segment = ['']

    new_lines, first_row = insert_lines_at(eb, insert_index, segment)
    cursor_row = first_row + len(segment) - 1
    replace_buffer_lines(
        editor,
        eb,
        new_lines,
        cursor_row,
        first_non_blank=True,
        inserted_text=clip_text,
    )


def _resolve_move_copy_dest(editor: Any, eb: Any, dest_addr: str | None) -> int | None:
    if not dest_addr:
        editor.show_message('E14: Invalid address')
        return None
    doc = eb.buffer.document
    dest, err = resolve_insert_address(
        dest_addr,
        doc.cursor_position_row,
        len(doc.lines),
        getattr(eb, 'visual_marks', None),
    )
    if err is not None or dest is None:
        editor.show_message(err)
        return None
    return dest


@_cmd('move')
@_cmd('m')
def move_lines(editor: Any, variables: Any) -> None:
    """
    Move addressed lines after destination `{addr}` (`:[range]m {addr}`).
    """
    eb = active_writable_buffer(editor)
    if eb is None:
        return

    bounds = resolve_command_range(editor, eb, variables.get('cmd_range'), default_range='.')
    if bounds is None:
        return

    dest = _resolve_move_copy_dest(editor, eb, variables.get('dest_addr'))
    if dest is None:
        return

    start_row, end_row = bounds
    line1 = start_row + 1
    line2 = end_row + 1
    if line1 <= dest < line2:
        editor.show_message('E134: Cannot move a range of lines into itself')
        return

    lines = list(eb.buffer.document.lines)
    moved = lines[start_row : end_row + 1]
    remaining = lines[:start_row] + lines[end_row + 1 :]
    insert_at = dest if dest <= start_row else dest - len(moved)
    remaining[insert_at:insert_at] = moved
    cursor_row = insert_at + len(moved) - 1
    replace_buffer_lines(editor, eb, remaining, cursor_row, first_non_blank=True)


@_cmd('copy')
@_cmd('co')
@_cmd('t')
def copy_lines(editor: Any, variables: Any) -> None:
    """
    Copy addressed lines after destination `{addr}` (`:[range]t {addr}` / `:co`).
    """
    eb = active_writable_buffer(editor)
    if eb is None:
        return

    bounds = resolve_command_range(editor, eb, variables.get('cmd_range'), default_range='.')
    if bounds is None:
        return

    dest = _resolve_move_copy_dest(editor, eb, variables.get('dest_addr'))
    if dest is None:
        return

    start_row, end_row = bounds
    lines = list(eb.buffer.document.lines)
    copied = lines[start_row : end_row + 1]
    lines[dest:dest] = copied
    cursor_row = dest + len(copied) - 1
    replace_buffer_lines(
        editor,
        eb,
        lines,
        cursor_row,
        first_non_blank=True,
        inserted_text='\n'.join(copied),
    )


@_cmd('join')
@_cmd('j')
def join_lines(editor: Any, variables: Any) -> None:
    """
    Join addressed lines (`:[range]j[!] [count]` / `:join[!]`).
    """
    eb = active_writable_buffer(editor)
    if eb is None:
        return

    bounds = resolve_command_range(editor, eb, variables.get('cmd_range'), default_range='.')
    if bounds is None:
        return

    lines = list(eb.buffer.document.lines)
    start_row, end_row = bounds
    cmd_arg = variables.get('cmd_arg')
    if cmd_arg:
        count = int(cmd_arg)
        if count < 1:
            editor.show_message(f'E488: Trailing characters: {cmd_arg}')
            return
        start_row = end_row
        end_row = min(len(lines) - 1, start_row + max(1, count - 1))
    elif start_row == end_row:
        end_row = min(len(lines) - 1, start_row + 1)

    if start_row >= end_row:
        return

    segment = lines[start_row : end_row + 1]
    if variables.get('force'):
        joined = ''.join(segment)
    else:
        joined = segment[0]
        for part in segment[1:]:
            stripped = part.lstrip()
            if not stripped:
                continue
            if joined and not joined[-1].isspace():
                joined = joined + ' ' + stripped
            else:
                joined = joined + stripped

    new_lines = lines[:start_row] + [joined] + lines[end_row + 1 :]
    replace_buffer_lines(editor, eb, new_lines, start_row, first_non_blank=True)


_FIRST_INT_RE = re.compile(r'-?\d+')


@_cmd('sort')
def sort_lines(editor: Any, variables: Any) -> None:
    """
    Sort lines in range (`:[range]sort[!] [i][u][r][n]`, default range `%`).
    """
    eb = active_writable_buffer(editor)
    if eb is None:
        return

    bounds = resolve_command_range(editor, eb, variables.get('cmd_range'), default_range='%')
    if bounds is None:
        return

    reverse = bool(variables.get('force'))
    ignore_case = False
    unique = False
    numeric = False
    flags_text = variables.get('cmd_arg') or ''
    for ch in flags_text:
        match ch:
            case 'i':
                ignore_case = True
            case 'u':
                unique = True
            case 'r':
                reverse = True
            case 'n':
                numeric = True
            case _:
                editor.show_message(f'E488: Trailing characters: {ch}')
                return

    start_row, end_row = bounds
    lines = list(eb.buffer.document.lines)
    segment = lines[start_row : end_row + 1]

    def text_key(line: str) -> str:
        return line.casefold() if ignore_case else line

    def sort_key(line: str) -> Any:
        part = text_key(line)
        if numeric:
            match = _FIRST_INT_RE.search(line)
            if match is None:
                return (0, 0, part)
            return (1, int(match.group(0)), part)
        return part

    sorted_segment = sorted(segment, key=sort_key, reverse=reverse)

    if unique:
        deduped: list[str] = []
        seen: set[Any] = set()
        for line in sorted_segment:
            if numeric:
                match = _FIRST_INT_RE.search(line)
                if match is None:
                    dedup_key: Any = (0, text_key(line))
                else:
                    dedup_key = (1, int(match.group(0)))
            else:
                dedup_key = text_key(line)
            if dedup_key not in seen:
                seen.add(dedup_key)
                deduped.append(line)
        sorted_segment = deduped

    new_lines = lines[:start_row] + sorted_segment + lines[end_row + 1 :]
    replace_buffer_lines(editor, eb, new_lines, start_row, first_non_blank=True)


def _encode_retab_ws(start_col: int, end_col: int, new_ts: int, expand_tab: bool) -> str:
    width = end_col - start_col
    if width <= 0:
        return ''
    if expand_tab:
        return ' ' * width
    out: list[str] = []
    col = start_col
    while col < end_col:
        next_stop = col + (new_ts - (col % new_ts))
        if next_stop <= end_col:
            out.append('\t')
            col = next_stop
        else:
            out.append(' ' * (end_col - col))
            col = end_col
    return ''.join(out)


def _retab_line(line: str, old_ts: int, new_ts: int, expand_tab: bool, force: bool) -> str:
    from prompt_toolkit.utils import get_cwidth

    out: list[str] = []
    col = 0
    i = 0
    length = len(line)

    while i < length:
        if line[i] in (' ', '\t'):
            start_col = col
            start_i = i
            has_tab = False
            while i < length and line[i] in (' ', '\t'):
                if line[i] == '\t':
                    has_tab = True
                    col += old_ts - (col % old_ts)
                else:
                    col += 1
                i += 1
            run = line[start_i:i]
            if expand_tab:
                if has_tab:
                    out.append(_encode_retab_ws(start_col, col, new_ts, True))
                else:
                    out.append(run)
            elif has_tab or (force and len(run) >= 2):
                # Vim `:retab` only rewrites whitespace runs containing a tab;
                # `:retab!` also converts space-only runs (docs/bugs/144.md).
                out.append(_encode_retab_ws(start_col, col, new_ts, False))
            else:
                out.append(run)
        else:
            ch = line[i]
            out.append(ch)
            col += max(0, get_cwidth(ch))
            i += 1

    return ''.join(out)


@_cmd('retab')
def retab_lines(editor: Any, variables: Any) -> None:
    """
    Re-expand tabs and spaces in range (`:[range]retab[!] [N]`, default `%`).
    """
    eb = active_writable_buffer(editor)
    if eb is None:
        return

    bounds = resolve_command_range(editor, eb, variables.get('cmd_range'), default_range='%')
    if bounds is None:
        return

    old_ts = eb.tabstop
    cmd_arg = variables.get('cmd_arg')
    if cmd_arg is not None:
        try:
            new_ts = int(cmd_arg)
        except ValueError:
            editor.show_message(f'E474: Invalid argument: {cmd_arg}')
            return
        if new_ts < 1:
            editor.show_message(f'E474: Invalid argument: {cmd_arg}')
            return
    else:
        new_ts = old_ts

    force = bool(variables.get('force'))
    expand_tab = bool(getattr(editor, 'expand_tab', True))
    start_row, end_row = bounds
    lines = list(eb.buffer.document.lines)
    for row in range(start_row, end_row + 1):
        lines[row] = _retab_line(lines[row], old_ts, new_ts, expand_tab, force)

    if cmd_arg is not None:
        eb.tabstop = new_ts

    replace_buffer_lines(editor, eb, lines, start_row, first_non_blank=True)
