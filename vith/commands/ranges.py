"""
Shared Ex line-address/range parsing, resolution, and buffer-line mutation
helpers for range-capable commands (`:s`, `:r`, `:d`, `:y`, `:pu`, `:m`, `:t`,
`:j`, `:sort`, `:g`/`:v`, `:retab`, `:[range]!`, `:[range]w`, `:norm`, and bare
address jumps).
"""

from __future__ import annotations

import re
from typing import Any

from prompt_toolkit.document import Document

from vith.thai import note_document_text

__all__ = (
    'ADDR_GRAMMAR',
    'MARK_NOT_SET_TEXT',
    'MODIFIABLE_OFF_TEXT',
    'NO_SUCH_LINE_TEXT',
    'RANGE_GRAMMAR',
    'is_buffer_read_only',
    'parse_range',
    'replace_buffer_lines',
    'resolve_insert_address',
    'resolve_line_address',
    'resolve_range',
    'resolve_range_str',
)

MODIFIABLE_OFF_TEXT = "E21: Cannot make changes, 'modifiable' is off"
NO_SUCH_LINE_TEXT = 'E19: No such line in current buffer'
MARK_NOT_SET_TEXT = 'E20: Mark not set'

#: Grammar fragments compatible with `prompt_toolkit.contrib.regular_languages`.
ADDR_GRAMMAR = r"((\d+|\.|\$|'<|'>)([+-]\d*)?|[+-]\d*)"
RANGE_GRAMMAR = r"(%|" + ADDR_GRAMMAR + r"(," + ADDR_GRAMMAR + r")?)"

_LINE_ADDRESS_RE = re.compile(
    r"^(?:(?P<base>\d+|\.|\$|'<|'>)(?P<offset>[+-]\d*)?|(?P<rel>[+-]\d*))$"
)


def _parse_offset(offset_text: str | None) -> int:
    if not offset_text:
        return 0
    if offset_text == '+':
        return 1
    if offset_text == '-':
        return -1
    return int(offset_text)


def parse_range(range_str: str | None) -> tuple[str | None, str | None]:
    """
    Split a raw Ex range expression (e.g. `'%'`, `'1,3'`, `"'<,'>"`, `'.+1'`)
    into `(range_start, range_end)`.
    """
    if not range_str:
        return None, None
    if ',' in range_str:
        range_start, range_end = range_str.split(',', 1)
        return range_start, range_end
    return range_str, None


def resolve_line_address(
    address: str,
    cursor_row: int,
    line_count: int,
    visual_marks: tuple[int, int] | None,
) -> tuple[int | None, str | None]:
    """
    Resolve a single line address (`N`, `0`, `.`, `$`, `'<`, `'>`, `+N`, `-N`,
    with optional `+/-N` offset) to a 0-based row index, or return an error
    message. Bare `0` maps to row `0` (line 1) for `:0s` / `:0` Vim parity.
    """
    match = _LINE_ADDRESS_RE.match(address.strip())
    if match is None:
        return None, NO_SUCH_LINE_TEXT

    rel = match.group('rel')
    if rel is not None:
        return cursor_row + _parse_offset(rel), None

    base = match.group('base')
    offset_text = match.group('offset')
    offset = _parse_offset(offset_text)

    if base == '.':
        base_row = cursor_row
    elif base == '$':
        base_row = line_count - 1
    elif base in ("'<", "'>"):
        if visual_marks is None:
            return None, MARK_NOT_SET_TEXT
        base_row = visual_marks[0] if base == "'<" else visual_marks[1]
    elif base == '0' and not offset_text:
        # `:0s` / `:0,1s` maps line 0 to the first line, matching Vim (docs/bugs/083.md).
        return 0, None
    else:
        base_row = int(base) - 1

    return base_row + offset, None


def resolve_insert_address(
    address: str | None,
    cursor_row: int,
    line_count: int,
    visual_marks: tuple[int, int] | None,
) -> tuple[int | None, str | None]:
    """
    Resolve an Ex destination/insertion address (`:[addr]r`, `:[addr]pu`,
    `:m {addr}`, `:t {addr}`) to a 0-based list insertion index in
    `0..line_count` (`0` inserts before line 1; `line_count` appends at EOF).
    """
    if not address:
        return cursor_row + 1, None

    match = _LINE_ADDRESS_RE.match(address.strip())
    if match is None:
        return None, NO_SUCH_LINE_TEXT

    rel = match.group('rel')
    if rel is not None:
        target = (cursor_row + 1) + _parse_offset(rel)
    else:
        base = match.group('base')
        offset = _parse_offset(match.group('offset'))
        if base == '.':
            base_1based = cursor_row + 1
        elif base == '$':
            base_1based = line_count
        elif base in ("'<", "'>"):
            if visual_marks is None:
                return None, MARK_NOT_SET_TEXT
            base_1based = (visual_marks[0] if base == "'<" else visual_marks[1]) + 1
        else:
            base_1based = int(base)
        target = base_1based + offset

    if not 0 <= target <= line_count:
        return None, NO_SUCH_LINE_TEXT

    return target, None


def resolve_range(
    range_start: str | None,
    range_end: str | None,
    cursor_row: int,
    line_count: int,
    visual_marks: tuple[int, int] | None,
    *,
    default_range: str = '.',
) -> tuple[tuple[int, int] | None, str | None]:
    """
    Resolve Ex range bounds into an inclusive `(start_row, end_row)` 0-based
    tuple, or return `(None, error_message)`.

    `default_range` controls omitted-range behavior: `'.'` (current line) or
    `'%'` (whole buffer).
    """
    if not range_start:
        if range_end:
            return None, NO_SUCH_LINE_TEXT
        if default_range == '%':
            return (0, max(0, line_count - 1)), None
        return (cursor_row, cursor_row), None

    if range_start == '%':
        if range_end:
            return None, NO_SUCH_LINE_TEXT
        return (0, max(0, line_count - 1)), None

    start_row, start_err = resolve_line_address(range_start, cursor_row, line_count, visual_marks)
    if start_err is not None or start_row is None:
        return None, start_err

    if range_end:
        end_row, end_err = resolve_line_address(range_end, cursor_row, line_count, visual_marks)
        if end_err is not None or end_row is None:
            return None, end_err
    else:
        end_row = start_row

    if not 0 <= start_row <= end_row < line_count:
        return None, NO_SUCH_LINE_TEXT

    return (start_row, end_row), None


def resolve_range_str(
    range_str: str | None,
    eb: Any,
    *,
    default_range: str = '.',
) -> tuple[tuple[int, int] | None, str | None]:
    """
    Resolve a raw range string against `EditorBuffer` `eb`.
    """
    doc = eb.buffer.document
    range_start, range_end = parse_range(range_str)
    return resolve_range(
        range_start,
        range_end,
        doc.cursor_position_row,
        len(doc.lines),
        getattr(eb, 'visual_marks', None),
        default_range=default_range,
    )


def is_buffer_read_only(buff: Any) -> bool:
    """
    Evaluate `buff.read_only`, which prompt_toolkit stores as a `Filter`
    callable (`Always` / `Never`), while also tolerating plain booleans.
    """
    read_only = getattr(buff, 'read_only', False)
    if callable(read_only):
        return bool(read_only())
    return bool(read_only)


def replace_buffer_lines(
    editor: Any,
    eb: Any,
    lines: list[str],
    cursor_row: int,
    *,
    col: int = 0,
    first_non_blank: bool = True,
    inserted_text: str = '',
) -> bool:
    """
    Replace `eb.buffer`'s text with `lines` while uniformly enforcing:
    1. Read-only buffer guard (`E21`),
    2. Undo snapshot (`save_to_undo_stack`),
    3. Single `Document` construction,
    4. Sticky Thai/Lao renderer gate (`note_document_text`),
    5. Clamped cursor placement (first non-blank char or explicit `col`).
    """
    if is_buffer_read_only(eb.buffer):
        editor.show_message(MODIFIABLE_OFF_TEXT)
        return False

    eb.buffer.save_to_undo_stack()

    normalized_lines = lines if lines else ['']
    new_text = '\n'.join(normalized_lines)
    doc = Document(new_text)
    eb.buffer.document = doc
    note_document_text(inserted_text or new_text)

    target_row = max(0, min(cursor_row, len(doc.lines) - 1))
    line_text = doc.lines[target_row]
    if first_non_blank:
        target_col = len(line_text) - len(line_text.lstrip())
    else:
        target_col = max(0, min(col, len(line_text)))
    eb.buffer.cursor_position = doc.translate_row_col_to_index(target_row, target_col)
    return True
