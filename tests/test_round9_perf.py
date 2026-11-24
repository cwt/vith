"""Regressions for the Round 9 performance inventory (docs/bugs/151.md)."""

from __future__ import annotations

import time

from prompt_toolkit.filters import to_filter
from prompt_toolkit.layout.screen import Char, Screen

import vith.commands.editing as editing_module
import vith.layout as layout_module
import vith.thai as thai
from vith.commands.editing import _retab_line
from vith.commands.global_cmd import _IndexedLineChunks
from vith.commands.handler import handle_command
from vith.commands.ranges import splice_buffer_lines
from vith.editor_buffer import EditorBuffer
from vith.layout import BufferListOverlay
from vith.thai.classify import is_sara_am
from vith.thai.patches import (
    _SARA_AM_POISON,
    _sanitize_and_invalidate_sara_am,
)


def _overlay_text_callable(overlay):
    obj = overlay
    while not hasattr(obj, 'text'):
        obj = obj.content
    return obj.text


# --- 151 #1: splice_buffer_lines --------------------------------------------


def test_splice_buffer_lines_covers_replace_insert_delete_and_guards(editor):
    editor.load_initial_files([None])
    eb = editor.window_arrangement.active_editor_buffer
    eb.buffer.text = 'L0\nL1\nL2\nL3\nL4'

    # Middle replacement
    assert splice_buffer_lines(editor, eb, 1, 2, ['X1', 'X2', 'X3'], 1) is True
    assert eb.buffer.document.lines == ['L0', 'X1', 'X2', 'X3', 'L3', 'L4']
    handle_command(editor, ':u')
    assert eb.buffer.document.lines == ['L0', 'L1', 'L2', 'L3', 'L4']

    # Start replacement & end replacement
    assert splice_buffer_lines(editor, eb, 0, 0, ['HEAD'], 0) is True
    assert eb.buffer.document.lines == ['HEAD', 'L1', 'L2', 'L3', 'L4']
    assert splice_buffer_lines(editor, eb, 4, 4, ['TAIL'], 4) is True
    assert eb.buffer.document.lines == ['HEAD', 'L1', 'L2', 'L3', 'TAIL']

    # Pure insertions: before row 0, middle, and after EOF
    assert splice_buffer_lines(editor, eb, 0, -1, ['TOP'], 0) is True
    assert eb.buffer.document.lines[0] == 'TOP'
    assert splice_buffer_lines(editor, eb, 2, 1, ['MID'], 2) is True
    assert eb.buffer.document.lines[2] == 'MID'
    end_idx = len(eb.buffer.document.lines)
    assert splice_buffer_lines(editor, eb, end_idx, end_idx - 1, ['BOT'], end_idx) is True
    assert eb.buffer.document.lines[-1] == 'BOT'

    # Pure deletions: first, middle, last, no-op empty splice, and whole buffer
    eb.buffer.text = 'A\nB\nC\nD'
    assert splice_buffer_lines(editor, eb, 0, 0, [], 0) is True
    assert eb.buffer.document.lines == ['B', 'C', 'D']
    assert splice_buffer_lines(editor, eb, 1, 1, [], 1) is True
    assert eb.buffer.document.lines == ['B', 'D']
    assert splice_buffer_lines(editor, eb, 1, 1, [], 0) is True
    assert eb.buffer.document.lines == ['B']
    assert splice_buffer_lines(editor, eb, 1, 0, [], 0) is True
    assert eb.buffer.document.lines == ['B']
    assert splice_buffer_lines(editor, eb, 0, 0, [], 0) is True
    assert eb.buffer.text == ''

    # Read-only guard and Thai gate latch
    eb.buffer.text = 'ascii'
    eb.buffer.read_only = to_filter(True)
    assert splice_buffer_lines(editor, eb, 0, 0, ['nope'], 0) is False
    assert eb.buffer.text == 'ascii'

    eb.buffer.read_only = to_filter(False)
    thai.reset_thai_text_gate()
    assert splice_buffer_lines(editor, eb, 0, 0, ['  สวัสดี'], 0) is True
    assert thai._THAI_GATE.value is True
    assert eb.buffer.document.cursor_position_col == 2


# --- 151 #2: EditorLayout preserves inactive-tab frames and prunes closed ---


def test_editor_layout_retains_inactive_tab_frames_and_prunes_closed(editor):
    editor.load_initial_files([None])
    wa = editor.window_arrangement
    eb0 = wa.active_editor_buffer
    eb0.buffer.text = '\n'.join(f'line {i}' for i in range(120))
    editor.sync_with_prompt_toolkit()

    win0 = wa.tab_pages[0].active_window
    pt_win0 = win0.pt_window
    pt_win0.vertical_scroll = 37
    lexer0 = pt_win0.content.lexer

    # Open a second tab: Tab 0's frame stays cached in `_frames`.
    wa.create_tab(None)
    editor.sync_with_prompt_toolkit()
    assert len(editor.editor_layout._frames) == 2

    # Switch back to Tab 0: same pt_window, scroll offset, and DocumentLexer.
    wa.active_tab_index = 0
    editor.sync_with_prompt_toolkit()
    assert win0.pt_window is pt_win0
    assert win0.pt_window.vertical_scroll == 37
    assert win0.pt_window.content.lexer is lexer0

    # Closing Tab 1 prunes its frame so closed tabs do not leak.
    wa.active_tab_index = 1
    wa.close_tab()
    editor.sync_with_prompt_toolkit()
    assert len(editor.editor_layout._frames) == 1
    assert (win0, eb0) in editor.editor_layout._frames

    # Switching buffer in the window prunes the old `(window, eb0)` entry.
    eb_other = EditorBuffer(editor, text='other')
    wa._add_editor_buffer(eb_other)
    wa.show_editor_buffer(eb_other)
    editor.sync_with_prompt_toolkit()
    assert (win0, eb0) not in editor.editor_layout._frames
    assert (win0, eb_other) in editor.editor_layout._frames


# --- 151 #3: :g move/copy fast paths & chunked sequence ---------------------


def test_global_move_and_copy_static_endpoints_and_dynamic_addresses(editor):
    editor.load_initial_files([None])
    eb = editor.window_arrangement.active_editor_buffer

    # Static `0` and `$` fast paths (both move and copy, partial and all).
    eb.buffer.text = 'A\nB1\nC\nB2\nD'
    handle_command(editor, ':g/B/m 0')
    assert eb.buffer.document.lines == ['B2', 'B1', 'A', 'C', 'D']
    assert eb.buffer.document.cursor_position_row == 0

    eb.buffer.text = 'A\nB1\nC\nB2\nD'
    handle_command(editor, ':g/B/t 0')
    assert eb.buffer.document.lines == ['B2', 'B1', 'A', 'B1', 'C', 'B2', 'D']
    assert eb.buffer.document.cursor_position_row == 0

    eb.buffer.text = 'A\nB1\nC\nB2\nD'
    handle_command(editor, ':g/B/m $')
    assert eb.buffer.document.lines == ['A', 'C', 'D', 'B1', 'B2']
    assert eb.buffer.document.cursor_position_row == 4

    eb.buffer.text = 'A\nB1\nC\nB2\nD'
    handle_command(editor, ':g/B/t $')
    assert eb.buffer.document.lines == ['A', 'B1', 'C', 'B2', 'D', 'B1', 'B2']
    assert eb.buffer.document.cursor_position_row == 6

    # Dynamic/relative destinations across enough lines to split/remove chunks.
    count = 300
    original = [f'm{i}' if i % 2 == 0 else f'k{i}' for i in range(count)]

    eb.buffer.text = '\n'.join(original)
    handle_command(editor, ':g/^m/m 1')
    expected_m1 = (
        [original[0]]
        + [original[i] for i in reversed(range(2, count, 2))]
        + [original[i] for i in range(1, count, 2)]
    )
    assert eb.buffer.document.lines == expected_m1

    eb.buffer.text = '\n'.join(original)
    handle_command(editor, ':g/^m/t .')
    expected_dup = []
    for line in original:
        expected_dup.append(line)
        if line.startswith('m'):
            expected_dup.append(line)
    assert eb.buffer.document.lines == expected_dup

    # Relative move `m +1` swaps each matched line down by one.
    eb.buffer.text = 'm0\nk1\nm2\nk3'
    handle_command(editor, ':g/^m/m +1')
    assert eb.buffer.document.lines == ['k1', 'm0', 'k3', 'm2']

    # Direct chunk edge case: popping all items then inserting into empty structure.
    chunks = _IndexedLineChunks(['a'], [0])
    item = chunks.pop_at(0)
    assert len(chunks) == 0
    chunks.insert_at(0, item)
    assert chunks.to_lines() == ['a']


def test_global_move_scales_subquadratically(editor):
    editor.load_initial_files([None])
    eb = editor.window_arrangement.active_editor_buffer
    eb.buffer.text = '\n'.join(f'row {i}' for i in range(2000))

    t0 = time.perf_counter()
    handle_command(editor, ':g/^/m 0')
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert eb.buffer.document.lines[0] == 'row 1999'
    assert eb.buffer.document.lines[-1] == 'row 0'
    # Was ~185 ms before; O(n) fast path completes in a few milliseconds.
    assert elapsed_ms < 100


# --- 151 #4: visible-buffer Thai gate decay/re-latch & single-pass scan -----


def test_thai_gate_decays_on_ascii_tab_and_relatches_on_switch_back(editor):
    thai.reset_thai_text_gate()
    editor.load_initial_files([None])
    wa = editor.window_arrangement
    eb_thai = wa.active_editor_buffer
    eb_thai.buffer.text = 'สวัสดี'
    editor.sync_with_prompt_toolkit()
    assert thai._THAI_GATE.value is True

    # Switch to a new ASCII tab: visible buffer is ASCII -> gate decays.
    wa.create_tab(None)
    wa.active_editor_buffer.buffer.text = 'plain ascii'
    editor.sync_with_prompt_toolkit()
    assert thai._THAI_GATE.value is False

    # Switch back to the already-open Thai tab: gate re-latches immediately.
    wa.active_tab_index = 0
    editor.sync_with_prompt_toolkit()
    assert thai._THAI_GATE.value is True

    # Non-ASCII search/command buffers keep the gate armed even on an ASCII tab.
    wa.active_tab_index = 1
    editor.search_buffer.text = 'คำค้น'
    editor.sync_with_prompt_toolkit()
    assert thai._THAI_GATE.value is True

    editor.search_buffer.text = ''
    editor.sync_with_prompt_toolkit()
    assert thai._THAI_GATE.value is False

    # A hidden buffer with a non-ASCII filename still keeps the gate armed
    # (tab bar and `:b` overlay render buffer locations).
    eb_thai.location = 'บันทึก.txt'
    thai.note_document_text(eb_thai.location)
    editor.sync_with_prompt_toolkit()
    assert thai._THAI_GATE.value is True


def test_sara_am_single_pass_clears_stray_mark_and_poisons_neighbor():
    assert is_sara_am(ord('a')) is False
    assert is_sara_am(ord('\u0e33')) is True

    prev_screen = Screen(default_char=Char(' '))
    new_screen = Screen(default_char=Char(' '))
    prev_screen.data_buffer[0][0] = Char('ท')
    prev_screen.data_buffer[0][1] = Char('ำ')
    new_screen.data_buffer[0][0] = Char('ข')
    new_screen.data_buffer[0][1] = Char('ำ')
    new_screen.data_buffer[0][2] = Char('\u0e34')  # stray width-0 mark

    _sanitize_and_invalidate_sara_am(new_screen, prev_screen)
    assert new_screen.data_buffer[0][2].char == ' '
    assert prev_screen.data_buffer[0][1] is _SARA_AM_POISON


# --- 151 #5: BufferListOverlay caches compiled search pattern ---------------


def test_buffer_list_overlay_caches_compiled_regex_across_frames(editor, monkeypatch):
    import re as re_module

    wa = editor.window_arrangement
    wa._add_editor_buffer(EditorBuffer(editor, 'alpha.txt'))
    wa._add_editor_buffer(EditorBuffer(editor, 'beta.txt'))
    editor.sync_with_prompt_toolkit()

    compiles = []
    real_compile = re_module.compile

    def counting_compile(pattern, flags=0):
        compiles.append(pattern)
        return real_compile(pattern, flags)

    monkeypatch.setattr(layout_module.re, 'compile', counting_compile)

    overlay = BufferListOverlay(editor)
    get_tokens = _overlay_text_callable(overlay)
    editor.command_buffer.text = ':b al'
    get_tokens()
    get_tokens()
    get_tokens()
    assert compiles == ['al']

    editor.command_buffer.text = ':b be'
    get_tokens()
    get_tokens()
    assert compiles == ['al', 'be']

    editor.command_buffer.text = ':b '
    get_tokens()
    assert compiles == ['al', 'be']


# --- 151 #6: _retab_line fast paths & Unicode parity ------------------------


def test_retab_line_fast_paths_skip_get_cwidth_on_ascii_and_keep_unicode(monkeypatch):
    calls = []
    real_get_cwidth = editing_module.get_cwidth

    def counting_get_cwidth(ch):
        calls.append(ch)
        return real_get_cwidth(ch)

    monkeypatch.setattr(editing_module, 'get_cwidth', counting_get_cwidth)

    # Tab-free line returns immediately (same object, zero `get_cwidth` calls).
    plain = '    hello world'
    assert _retab_line(plain, 4, 4, expand_tab=True, force=False) is plain
    assert _retab_line(plain, 4, 4, expand_tab=False, force=False) is plain
    assert calls == []

    # Printable ASCII line with tabs advances by chunk length (zero `get_cwidth`).
    assert _retab_line('ab\tcd\te', 4, 4, expand_tab=True, force=False) == 'ab  cd  e'
    assert calls == []

    # Non-ASCII (CJK wide + Thai combining mark) still uses exact `get_cwidth`.
    # 'ก้' has width 1 (base + combining mark), so '\t' at col 1 -> 3 spaces.
    # '你' has width 2, so '\t' at col 6 -> 2 spaces.
    assert _retab_line('ก้\t你\tx', 4, 4, expand_tab=True, force=False) == 'ก้   你  x'
    assert calls != []
