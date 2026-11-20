"""
`:global` and `:vglobal` Ex commands (`:[range]g[!]/pat/[cmd]`,
`:[range]v/pat/[cmd]`).
"""

from __future__ import annotations

import re
from typing import Any

from .common import (
    apply_deleted_lines,
    report_yanked_lines,
    resolve_command_range,
    store_lines_in_register,
)
from .normal import execute_normal_on_rows
from .ranges import (
    MODIFIABLE_OFF_TEXT,
    is_buffer_read_only,
    replace_buffer_lines,
    resolve_insert_address,
)
from .registry import _cmd
from .substitution import (
    apply_substitute_to_rows,
    is_unsafe_regex,
    parse_substitute_args,
    prepare_substitute,
    should_ignore_case,
    split_delimited_fields,
)

_GLOBAL_SUB_RE = re.compile(r'^(?P<name>substitute|s)(?P<args>\s*[^a-zA-Z0-9_\\\s"].*)?$')
_GLOBAL_MOVE_COPY_RE = re.compile(r'^(?P<name>move|copy|co|m|t)\s*(?P<dest>.+)?$')
_GLOBAL_NORM_RE = re.compile(r'^(?P<name>normal|norm)(?P<force>!?)\s+(?P<keys>.+)$')


def _parse_global_args(global_args: str) -> tuple[str, str]:
    """
    Parse `/<pattern>/<sub_cmd>` (supporting arbitrary non-alphanumeric
    delimiters with `\\<delim>` unescaping) into `(pattern_text, sub_cmd)`.
    """
    stripped = global_args.lstrip()
    delim = stripped[0]
    fields = split_delimited_fields(stripped[1:], delim, max_splits=1)
    sub_cmd = fields[1].strip() if len(fields) > 1 else ''
    return fields[0], sub_cmd


def _global_execute_substitute(
    editor: Any, eb: Any, lines: list[str], marked_rows: list[int], sub_args: str | None
) -> None:
    search, replace, flags = parse_substitute_args(sub_args)
    prepared = prepare_substitute(editor, search, replace, flags)
    if prepared is None:
        return
    apply_substitute_to_rows(editor, eb, lines, marked_rows, prepared)


def _global_execute_move_or_copy(
    editor: Any,
    eb: Any,
    lines: list[str],
    marked_rows: list[int],
    is_move: bool,
    dest_addr: str | None,
) -> None:
    if is_buffer_read_only(eb.buffer):
        editor.show_message(MODIFIABLE_OFF_TEXT)
        return
    if not dest_addr or not dest_addr.strip():
        editor.show_message('E14: Invalid address')
        return

    visual_marks = getattr(eb, 'visual_marks', None)
    tagged: list[tuple[int | None, str]] = [(idx, text) for idx, text in enumerate(lines)]
    last_row = marked_rows[0]
    # Track each original row's current position instead of re-scanning the
    # growing list for every marked line (docs/bugs/149.md).
    positions = {row: row for row in marked_rows}

    for line_id in marked_rows:
        cur_pos = positions[line_id]
        dest, err = resolve_insert_address(dest_addr, cur_pos, len(tagged), visual_marks)
        if err is not None or dest is None:
            editor.show_message(err)
            return
        if is_move:
            item = tagged.pop(cur_pos)
            for other, pos in positions.items():
                if other != line_id and pos > cur_pos:
                    positions[other] = pos - 1
            insert_at = dest if dest <= cur_pos else dest - 1
            tagged.insert(insert_at, item)
            for other, pos in positions.items():
                if pos >= insert_at:
                    positions[other] = pos + 1
            positions[line_id] = insert_at
            last_row = insert_at
        else:
            tagged.insert(dest, (None, tagged[cur_pos][1]))
            for other, pos in positions.items():
                if pos >= dest:
                    positions[other] = pos + 1
            last_row = dest

    result_lines = [text for _, text in tagged]
    replace_buffer_lines(editor, eb, result_lines, last_row, first_non_blank=True)


@_cmd('vglobal')
@_cmd('global')
@_cmd('g')
@_cmd('v')
def global_command(editor: Any, variables: Any) -> None:
    """
    Execute an Ex sub-command on lines matching (`:g` / `:global`) or not
    matching (`:g!` / `:v` / `:vglobal`) a regex pattern (`:[range]g[!]/pat/[cmd]`).
    """
    command = variables.get('command')
    force = bool(variables.get('force'))
    if command in ('v', 'vglobal') and force:
        editor.show_message('No ! allowed')
        return

    global_args = variables.get('global_args')
    if not global_args:
        editor.show_message('E148: Regular expression missing from global')
        return

    eb = editor.window_arrangement.active_editor_buffer
    if eb is None:
        return

    bounds = resolve_command_range(editor, eb, variables.get('cmd_range'), default_range='%')
    if bounds is None:
        return

    pattern_text, sub_cmd = _parse_global_args(global_args)
    search_state = editor.application.current_search_state
    if not pattern_text:
        pattern_text = search_state.text
    if not pattern_text:
        editor.show_message('No previous regular expression')
        return

    if is_unsafe_regex(editor, pattern_text):
        return

    regex_flags = re.IGNORECASE if should_ignore_case(editor, pattern_text) else 0
    try:
        pattern = re.compile(pattern_text, flags=regex_flags)
    except re.error as exc:
        editor.show_message('Invalid regex: %s' % exc)
        return

    invert = command in ('v', 'vglobal') or force
    start_row, end_row = bounds
    lines = list(eb.buffer.document.lines)
    marked_rows = [
        row
        for row in range(start_row, end_row + 1)
        if (pattern.search(lines[row]) is None) == invert
    ]

    if not marked_rows:
        editor.show_message('Pattern not found')
        return

    search_state.text = pattern_text
    editor.clear_hlsearch_suppression()

    sub = sub_cmd.strip() or 'p'
    if sub in ('p', 'print'):
        output = '\n'.join(lines[row] for row in marked_rows)
        editor.show_info(output, title='[Global]')
        return

    if sub in ('d', 'delete'):
        if is_buffer_read_only(eb.buffer):
            editor.show_message(MODIFIABLE_OFF_TEXT)
            return
        marked_set = set(marked_rows)
        deleted = [lines[row] for row in marked_rows]
        remaining = [line for idx, line in enumerate(lines) if idx not in marked_set]
        apply_deleted_lines(editor, eb, remaining, deleted, marked_rows[0])
        return

    if sub in ('y', 'yank'):
        yanked = [lines[row] for row in marked_rows]
        store_lines_in_register(editor, yanked)
        report_yanked_lines(editor, len(yanked))
        return

    sub_match = _GLOBAL_SUB_RE.match(sub)
    if sub_match is not None:
        _global_execute_substitute(editor, eb, lines, marked_rows, sub_match.group('args'))
        return

    mc_match = _GLOBAL_MOVE_COPY_RE.match(sub)
    if mc_match is not None:
        is_move = mc_match.group('name') in ('m', 'move')
        _global_execute_move_or_copy(
            editor, eb, lines, marked_rows, is_move, mc_match.group('dest')
        )
        return

    norm_match = _GLOBAL_NORM_RE.match(sub)
    if norm_match is not None:
        execute_normal_on_rows(
            editor,
            eb,
            marked_rows,
            norm_match.group('keys'),
            ignore_user_mappings=bool(norm_match.group('force')),
            track_line_delta=True,
        )
        return

    editor.show_message(f'Not an editor command: {sub}')
