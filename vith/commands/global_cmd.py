"""
`:global` and `:vglobal` Ex commands (`:[range]g[!]/pat/[cmd]`,
`:[range]v/pat/[cmd]`).
"""

from __future__ import annotations

import math
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


class _IndexedLineChunks:
    """
    Chunked line sequence with O(1) chunk lookup per marked `line_id` and
    O(sqrt(n)) index/pop/insert operations for general `:g/.../m` and `:g/.../t`
    destinations (docs/bugs/151.md).
    """

    def __init__(self, lines: list[str], marked_rows: list[int]) -> None:
        marked_set = set(marked_rows)
        chunk_size = max(64, math.isqrt(max(1, len(lines))))
        self._max_chunk = chunk_size * 2
        self._chunks: list[list[tuple[int | None, str]]] = []
        self._chunk_of: dict[int, list[tuple[int | None, str]]] = {}
        self._total = len(lines)

        for start in range(0, len(lines), chunk_size):
            stop = min(start + chunk_size, len(lines))
            chunk: list[tuple[int | None, str]] = [
                (idx if idx in marked_set else None, lines[idx]) for idx in range(start, stop)
            ]
            self._chunks.append(chunk)
            for idx in range(start, stop):
                if idx in marked_set:
                    self._chunk_of[idx] = chunk

    def __len__(self) -> int:
        return self._total

    def index_of(self, line_id: int) -> int:
        target_chunk = self._chunk_of[line_id]
        offset = 0
        for chunk in self._chunks:
            if chunk is target_chunk:
                for local_idx, (lid, _text) in enumerate(chunk):
                    if lid == line_id:
                        return offset + local_idx
                break
            offset += len(chunk)
        raise KeyError(line_id)

    def _locate(self, pos: int) -> tuple[int, int]:
        offset = 0
        for chunk_idx, chunk in enumerate(self._chunks):
            end = offset + len(chunk)
            if pos < end:
                return chunk_idx, pos - offset
            offset = end
        return len(self._chunks) - 1, len(self._chunks[-1])

    def text_at(self, pos: int) -> str:
        chunk_idx, local_idx = self._locate(pos)
        return self._chunks[chunk_idx][local_idx][1]

    def pop_at(self, pos: int) -> tuple[int | None, str]:
        chunk_idx, local_idx = self._locate(pos)
        chunk = self._chunks[chunk_idx]
        item = chunk.pop(local_idx)
        if item[0] is not None:
            self._chunk_of.pop(item[0], None)
        if not chunk:
            del self._chunks[chunk_idx]
        self._total -= 1
        return item

    def insert_at(self, pos: int, item: tuple[int | None, str]) -> None:
        if not self._chunks:
            new_chunk = [item]
            self._chunks.append(new_chunk)
            if item[0] is not None:
                self._chunk_of[item[0]] = new_chunk
            self._total = 1
            return

        if pos >= self._total:
            chunk_idx = len(self._chunks) - 1
            chunk = self._chunks[chunk_idx]
            chunk.append(item)
        else:
            chunk_idx, local_idx = self._locate(pos)
            chunk = self._chunks[chunk_idx]
            chunk.insert(local_idx, item)

        if item[0] is not None:
            self._chunk_of[item[0]] = chunk
        self._total += 1

        if len(chunk) >= self._max_chunk:
            mid = len(chunk) // 2
            right = chunk[mid:]
            del chunk[mid:]
            self._chunks.insert(chunk_idx + 1, right)
            for lid, _text in right:
                if lid is not None:
                    self._chunk_of[lid] = right

    def to_lines(self) -> list[str]:
        return [text for chunk in self._chunks for _lid, text in chunk]


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

    # O(n) single-pass fast path for static numeric destinations (`0..len(lines)`)
    # and `$`, matching Vim's two-pass order exactly (docs/bugs/151.md, 166.md).
    stripped_dest = dest_addr.strip()
    if stripped_dest.isascii() and stripped_dest.isdigit():
        dest_idx = int(stripped_dest)
        if 0 <= dest_idx <= len(lines):
            if is_move:
                marked_set = set(marked_rows)
                unmarked_before = [lines[i] for i in range(dest_idx) if i not in marked_set]
                unmarked_after = [
                    lines[i] for i in range(dest_idx, len(lines)) if i not in marked_set
                ]
                marked_before = [lines[r] for r in marked_rows if r < dest_idx]
                marked_after = [lines[r] for r in marked_rows if r >= dest_idx]
                result_lines = unmarked_before + marked_before + marked_after[::-1] + unmarked_after
                last_row = dest_idx if marked_after else dest_idx - 1
            else:
                copied = [lines[row] for row in reversed(marked_rows)]
                result_lines = lines[:dest_idx] + copied + lines[dest_idx:]
                last_row = dest_idx
            replace_buffer_lines(editor, eb, result_lines, last_row, first_non_blank=True)
            return

    if stripped_dest == '$':
        moved_or_copied = [lines[row] for row in marked_rows]
        if is_move:
            marked_set = set(marked_rows)
            remaining = [line for idx, line in enumerate(lines) if idx not in marked_set]
            result_lines = remaining + moved_or_copied
        else:
            result_lines = list(lines) + moved_or_copied
        replace_buffer_lines(editor, eb, result_lines, len(result_lines) - 1, first_non_blank=True)
        return

    visual_marks = getattr(eb, 'visual_marks', None)
    chunks = _IndexedLineChunks(lines, marked_rows)
    last_row = marked_rows[0]

    for line_id in marked_rows:
        cur_pos = chunks.index_of(line_id)
        dest, err = resolve_insert_address(dest_addr, cur_pos, len(chunks), visual_marks)
        if err is not None or dest is None:
            editor.show_message(err)
            return
        if is_move:
            item = chunks.pop_at(cur_pos)
            insert_at = dest if dest <= cur_pos else dest - 1
            chunks.insert_at(insert_at, item)
            last_row = insert_at
        else:
            chunks.insert_at(dest, (None, chunks.text_at(cur_pos)))
            last_row = dest

    replace_buffer_lines(editor, eb, chunks.to_lines(), last_row, first_non_blank=True)


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
    lines = eb.buffer.document.lines
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
