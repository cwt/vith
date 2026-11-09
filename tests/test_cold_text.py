"""
Tests for compressed cold-text undo/redo history (`vith/cold_text.py`).
"""

from __future__ import annotations

import builtins

import vith.commands.commands as commands_module
from vith.cold_text import (
    ColdTextBuffer,
    ColdTextStack,
    format_cold_text_stats,
    make_cold_text_compressor,
)
from vith.commands.handler import handle_command
from vith.editor_buffer import EditorBuffer
from vith.rc_file import run_rc_file


def _make_stack() -> ColdTextStack:
    compressor, _name, _level = make_cold_text_compressor('zstd', 1)
    return ColdTextStack(compressor)


def test_default_compressor_is_configured_as_zstd(editor):
    assert editor.cold_text_compression == 'zstd'
    assert editor.cold_text_compression_level == 1

    compressor, name, level = make_cold_text_compressor('zstd', 1)
    assert name in ('zstd', 'zlib-fast')
    assert level == 1
    assert compressor is not None


def test_cold_text_stack_roundtrip():
    stack = _make_stack()
    text = 'def example():\n    return 42\n' * 1000

    stack.push(text, 10, 1)
    stack.push('second', 2, 2)

    assert len(stack) == 2
    assert stack.top_version == 2

    popped_text, popped_cursor, popped_version = stack.pop()
    assert (popped_text, popped_cursor, popped_version) == ('second', 2, 2)
    assert stack.top_version == 1
    assert len(stack) == 1

    popped_text, popped_cursor, popped_version = stack.pop()
    assert popped_text == text
    assert (popped_cursor, popped_version) == (10, 1)
    assert not stack


def test_cold_text_stack_stats_compress_large_text():
    stack = _make_stack()
    text = 'def example():\n    return 42\n' * 1000
    stack.push(text, 0, 1)

    stats = stack.stats
    assert stats['count'] == 1
    assert stats['compressed_size_bytes'] < stats['raw_size_bytes']


def test_cold_text_buffer_undo_redo(editor_buffer):
    buffer = editor_buffer.buffer
    assert isinstance(buffer, ColdTextBuffer)

    buffer.text = 'one'
    buffer.save_to_undo_stack()
    buffer.text = 'two'
    buffer.save_to_undo_stack()
    buffer.text = 'three'
    buffer.save_to_undo_stack()

    stats = buffer.cold_text_stats()
    assert stats['compressor'] in ('zstd', 'zlib-fast')
    assert stats['undo']['count'] == 3
    assert stats['total']['count'] == 3

    buffer.undo()
    assert buffer.text == 'two'
    buffer.undo()
    assert buffer.text == 'one'
    buffer.redo()
    assert buffer.text == 'two'
    buffer.redo()
    assert buffer.text == 'three'


def test_cursor_only_change_does_not_add_snapshot(editor_buffer):
    buffer = editor_buffer.buffer
    buffer.text = 'abcdef'
    buffer.save_to_undo_stack()
    assert buffer.cold_text_stats()['undo']['count'] == 1

    buffer.cursor_position = 3
    buffer.save_to_undo_stack()
    assert buffer.cold_text_stats()['undo']['count'] == 1


def test_config_selects_compression_algorithm(editor):
    editor.cold_text_compression = 'zlib-fast'
    editor.cold_text_compression_level = 1

    eb = EditorBuffer(editor, text='hello')
    assert eb.buffer.cold_compression_name == 'zlib-fast'
    assert eb.buffer.cold_compression_level == 1


def test_vithrc_sets_compression_algorithm(editor, tmp_path):
    rc_file = tmp_path / 'vithrc'
    rc_file.write_text(
        "def configure(editor):\n"
        "    editor.cold_text_compression = 'zlib-fast'\n"
        "    editor.cold_text_compression_level = 1\n"
    )

    run_rc_file(editor, str(rc_file))

    eb = EditorBuffer(editor, text='hello')
    assert eb.buffer.cold_compression_name == 'zlib-fast'
    assert eb.buffer.cold_compression_level == 1


def test_format_cold_text_stats():
    stats = {
        'compressor': 'zstd',
        'level': 1,
        'undo': {
            'count': 2,
            'raw_size_bytes': 1000,
            'compressed_size_bytes': 200,
            'compression_ratio_pct': 80.0,
        },
        'redo': {
            'count': 0,
            'raw_size_bytes': 0,
            'compressed_size_bytes': 0,
            'compression_ratio_pct': None,
        },
        'total': {
            'count': 2,
            'raw_size_bytes': 1000,
            'compressed_size_bytes': 200,
            'compression_ratio_pct': 80.0,
        },
    }

    lines = format_cold_text_stats(stats)
    assert lines[0] == 'Cold-text compression: zstd level 1'
    assert 'ratio 20.0% of raw' in lines[1]
    assert 'n/a' in lines[2]
    assert 'total:' in lines[3]


def test_compression_command_prints_stats(editor, editor_buffer, monkeypatch, capsys):
    editor_buffer.buffer.text = 'hello'
    editor_buffer.buffer.save_to_undo_stack()
    editor.window_arrangement._add_editor_buffer(editor_buffer)
    editor.sync_with_prompt_toolkit()

    def fake_run_in_terminal(handler):
        handler()

    def fake_input(_prompt=''):
        return ''

    monkeypatch.setattr(commands_module, 'run_in_terminal', fake_run_in_terminal)
    monkeypatch.setattr(builtins, 'input', fake_input)

    handle_command(editor, ':compression')

    output = capsys.readouterr().out
    assert 'Cold-text compression:' in output
    assert 'undo:' in output
    assert 'total:' in output


def test_cold_text_stack_update_top_cursor_affects_pop():
    # 071: `update_top_cursor` records the new cursor in metadata; `pop`
    # must hand back the metadata cursor so cursor-only saves survive undo.
    stack = _make_stack()
    stack.push('first', 10, 1)
    stack.update_top_cursor(7)
    assert stack.pop() == ('first', 7, 1)


def test_cursor_only_save_updates_undo_cursor(editor_buffer):
    # 071: undo after a cursor-only save must restore the *recorded* cursor
    # (the one at update time), not the stale push-time cursor.
    buffer = editor_buffer.buffer
    buffer.text = 'abcdef'
    buffer.cursor_position = 0
    buffer.save_to_undo_stack()  # push ('abcdef', cursor=0)

    buffer.cursor_position = 2
    buffer.save_to_undo_stack()  # cursor-only: update top -> cursor=2
    assert buffer.cold_text_stats()['undo']['count'] == 1

    buffer.cursor_position = 5  # move away without saving
    buffer.text = 'other'
    buffer.save_to_undo_stack()  # push ('other', ...)

    buffer.undo()  # pops 'other' (no text delta) then restores 'abcdef'
    assert buffer.text == 'abcdef'
    assert buffer.cursor_position == 2


def test_vertical_cursor_keeps_visual_column_over_thai(editor_buffer):
    # 078: j/k keep the *visual* column (cell-cluster ordinal) across Thai
    # rows; ptk's codepoint-based preferred_column drifted and landed on
    # line ends.
    buffer = editor_buffer.buffer
    buffer.text = "ที่ดิน\nกำแพง\nสหัส\n"
    doc = buffer.document

    buffer.cursor_position = 5  # row 0, cp col 5 = น (visual column 2)
    buffer.cursor_down()
    assert buffer.cursor_position == doc.translate_row_col_to_index(1, 2)  # แ
    buffer.cursor_down()
    assert buffer.cursor_position == doc.translate_row_col_to_index(2, 3)  # row 2 visual col 2
    buffer.cursor_up()
    assert buffer.cursor_position == doc.translate_row_col_to_index(1, 2)
    buffer.cursor_up()
    assert buffer.cursor_position == doc.translate_row_col_to_index(0, 5)  # original cell

    # Multi-row count with a shorter intermediate row keeps the ordinal:
    buffer.cursor_position = 0
    buffer.cursor_down(count=2)
    assert buffer.cursor_position == doc.translate_row_col_to_index(2, 0)


def test_vertical_cursor_ascii_parity(editor_buffer):
    # 078: ASCII rows keep ptk's codepoint == visual column identity.
    buffer = editor_buffer.buffer
    buffer.text = "abcde\nfoo\nxyzwv\n"
    buffer.cursor_position = 3
    buffer.cursor_down()
    assert buffer.cursor_position == buffer.document.translate_row_col_to_index(1, 3)
    buffer.cursor_down()
    assert buffer.cursor_position == buffer.document.translate_row_col_to_index(2, 3)


def test_redo_does_not_push_duplicate_snapshot_to_undo_stack(editor_buffer):
    # 110 item 7: `undo()` and `redo()` restore `self._text_version` to the
    # snapshot's recorded version so a subsequent cursor-only `save_to_undo_stack`
    # or `redo()` updates in-place instead of pushing a duplicate snapshot.
    buffer = editor_buffer.buffer
    buffer.text = "step 1"
    v1 = buffer._text_version
    buffer.save_to_undo_stack()
    buffer.text = "step 2"
    v2 = buffer._text_version
    buffer.save_to_undo_stack()
    buffer.text = "step 3"

    buffer.undo()
    assert buffer.text == "step 2"
    assert buffer._text_version == v2

    # Saving cursor position before redo and then redoing must not push two
    # copies of "step 2" onto `_undo_stack`:
    buffer.save_to_undo_stack(clear_redo_stack=False)
    count_after_save = len(buffer._undo_stack)
    buffer.redo()
    assert buffer.text == "step 3"
    assert len(buffer._undo_stack) == count_after_save

    buffer.undo()
    assert buffer.text == "step 2"
    assert buffer._text_version == v2
    buffer.undo()
    assert buffer.text == "step 1"
    assert buffer._text_version == v1


def test_update_top_cursor_on_empty_stack_is_noop():
    # 111 item 11: calling `update_top_cursor` on an empty `ColdTextStack`
    # returns cleanly instead of raising `IndexError`.
    stack = _make_stack()
    stack.update_top_cursor(42)
    assert len(stack) == 0


def test_vertical_cursor_caches_ordinal_and_invalidates_on_text_change(editor_buffer):
    # 113 item 4: `_cursor_vertical` stores `(src_col, src_row, ordinal)` on
    # `_vith_cell_column` and invalidates it when `_text_changed` fires.
    buffer = editor_buffer.buffer
    buffer.text = "ที่ดิน\nกำแพง\nสหัส\n"
    buffer.cursor_position = 5  # row 0, col 5, ordinal 2
    buffer.cursor_down()
    assert buffer._vith_cell_column == (5, 0, 2)
    buffer.cursor_down()
    assert buffer._vith_cell_column == (5, 0, 2)

    buffer.text = "เปลี่ยน\nบรรทัด\n"
    assert buffer._vith_cell_column is None
