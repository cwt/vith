"""
Tests for Phase 3 shared range engine (`vith/commands/ranges.py`), generalized
bare address jumps, and range-capable Ex editing commands (`:d`, `:y`, `:pu`,
`:m`, `:t`/`:co`, `:j`, `:sort`, `:retab`, `:g`/`:v`, `:[range]!`, `:[range]w`,
and `:norm`).
"""

from __future__ import annotations

from prompt_toolkit.filters import to_filter

from vith.commands.handler import handle_command
from vith.commands.ranges import (
    parse_range,
    replace_buffer_lines,
    resolve_insert_address,
    resolve_line_address,
    resolve_range,
)
from vith.thai import _THAI_GATE, reset_thai_text_gate


def test_ranges_helpers_parse_and_resolve_addresses():
    assert parse_range(None) == (None, None)
    assert parse_range('%') == ('%', None)
    assert parse_range('2,5') == ('2', '5')
    assert parse_range("'<,'>") == ("'<", "'>")

    # Line address resolution (0-based rows).
    assert resolve_line_address('1', 2, 5, None) == (0, None)
    assert resolve_line_address('0', 2, 5, None) == (0, None)
    assert resolve_line_address('.', 2, 5, None) == (2, None)
    assert resolve_line_address('$', 2, 5, None) == (4, None)
    assert resolve_line_address('+2', 1, 5, None) == (3, None)
    assert resolve_line_address('-', 2, 5, None) == (1, None)
    assert resolve_line_address('+', 2, 5, None) == (3, None)
    assert resolve_line_address('$-1', 0, 5, None) == (3, None)
    assert resolve_line_address("'<", 0, 5, (1, 3)) == (1, None)
    assert resolve_line_address("'>", 0, 5, (1, 3)) == (3, None)
    assert resolve_line_address("'<", 0, 5, None) == (None, 'E20: Mark not set')
    assert resolve_line_address('bad', 0, 5, None) == (None, 'E19: No such line in current buffer')

    # Insertion address resolution (0..line_count).
    assert resolve_insert_address(None, 2, 5, None) == (3, None)
    assert resolve_insert_address('0', 2, 5, None) == (0, None)
    assert resolve_insert_address('$', 2, 5, None) == (5, None)
    assert resolve_insert_address('+', 1, 5, None) == (3, None)
    assert resolve_insert_address('-1', 0, 5, None) == (0, None)
    assert resolve_insert_address('-2', 0, 5, None) == (None, 'E19: No such line in current buffer')

    # Range resolution defaults ('.' vs '%').
    assert resolve_range(None, None, 2, 5, None, default_range='.') == ((2, 2), None)
    assert resolve_range(None, None, 2, 5, None, default_range='%') == ((0, 4), None)
    assert resolve_range('%', None, 2, 5, None) == ((0, 4), None)
    assert resolve_range('2', '4', 0, 5, None) == ((1, 3), None)
    assert resolve_range('4', '2', 0, 5, None) == (None, 'E19: No such line in current buffer')


def test_bare_address_jumps_dollar_relative_marks_and_clamping(editor):
    # Empty arrangement never raises.
    handle_command(editor, ':$')
    handle_command(editor, ':+1')

    editor.load_initial_files([None])
    eb = editor.window_arrangement.active_editor_buffer
    eb.buffer.text = '  line1\n  line2\n  line3\n  line4\n  line5'
    eb.buffer.cursor_position = 0

    # `:$` jumps to last line, first non-blank character.
    handle_command(editor, ':$')
    assert eb.buffer.document.cursor_position_row == 4
    assert eb.buffer.document.cursor_position_col == 2

    # `:-2` moves 2 lines up -> line 3 (row 2).
    handle_command(editor, ':-2')
    assert eb.buffer.document.cursor_position_row == 2

    # `:+1` and `:+` move down 1 line each.
    handle_command(editor, ':+1')
    assert eb.buffer.document.cursor_position_row == 3
    handle_command(editor, ':+')
    assert eb.buffer.document.cursor_position_row == 4

    # `:-` moves up 1 line; `:.` stays on current line.
    handle_command(editor, ':-')
    assert eb.buffer.document.cursor_position_row == 3
    handle_command(editor, ':.')
    assert eb.buffer.document.cursor_position_row == 3

    # Bare `:0` clamps to line 1; bare `:999` clamps to last line.
    handle_command(editor, ':0')
    assert eb.buffer.document.cursor_position_row == 0
    handle_command(editor, ':999')
    assert eb.buffer.document.cursor_position_row == 4

    # Visual marks `'<` and `'>` jump when set, report E20 when unset.
    handle_command(editor, ":'<")
    assert editor.message == 'E20: Mark not set'

    eb.visual_marks = (1, 3)
    handle_command(editor, ":'<")
    assert eb.buffer.document.cursor_position_row == 1
    handle_command(editor, ":'>")
    assert eb.buffer.document.cursor_position_row == 3

    # Out-of-bounds relative jump reports E19 without moving cursor.
    handle_command(editor, ':+50')
    assert editor.message == 'E19: No such line in current buffer'
    assert eb.buffer.document.cursor_position_row == 3


def test_replace_buffer_lines_enforces_readonly_undo_and_thai_gate(editor):
    editor.load_initial_files([None])
    eb = editor.window_arrangement.active_editor_buffer
    eb.buffer.text = 'ascii'

    eb.buffer.read_only = to_filter(True)
    assert replace_buffer_lines(editor, eb, ['changed'], 0) is False
    assert eb.buffer.text == 'ascii'
    assert editor.message == "E21: Cannot make changes, 'modifiable' is off"

    eb.buffer.read_only = to_filter(False)
    reset_thai_text_gate()
    assert not _THAI_GATE.value
    assert replace_buffer_lines(editor, eb, ['  สวัสดี', 'line2'], 0) is True
    assert eb.buffer.text == '  สวัสดี\nline2'
    assert eb.buffer.document.cursor_position_col == 2
    assert _THAI_GATE.value

    handle_command(editor, ':u')
    assert eb.buffer.text == 'ascii'
