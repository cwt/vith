"""
Tests for Phase 1 Ex commands: missing aliases, `:close`, `:undo`/`:redo`,
`:nohlsearch`, conditional write/exit (`:update`, `:xit`, `:wall`, `:xall`),
and buffer/info/diagnostics (`:enew`, `:file`, `:version`, `:messages`).
"""

from __future__ import annotations

from vith.commands import commands as commands_mod
from vith.commands.handler import handle_command
from vith.editor_buffer import EditorBuffer


def _record_exits(editor, monkeypatch):
    exits = []

    def fake_exit(*args, **kwargs):
        exits.append(1)

    monkeypatch.setattr(editor.application, 'exit', fake_exit)
    return exits


def _capture_pager(monkeypatch):
    captured = []

    def fake_run_pager(print_handler):
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            print_handler()
        captured.append(buf.getvalue())

    monkeypatch.setattr(commands_mod, '_run_pager', fake_run_pager)
    return captured


def test_ls_and_files_aliases_invoke_buffer_list_pager(editor, tmp_path, monkeypatch):
    target = tmp_path / 'sample.txt'
    target.write_text('hello\n')
    editor.load_initial_files([str(target)])
    captured = _capture_pager(monkeypatch)

    handle_command(editor, ':ls')
    handle_command(editor, ':files')

    assert len(captured) == 2
    assert 'sample.txt' in captured[0]
    assert captured[0] == captured[1]


def test_bnext_bprev_bprevious_aliases_and_force_guard(editor, tmp_path):
    editor.load_initial_files([str(tmp_path / 'a.txt'), str(tmp_path / 'b.txt')])
    wa = editor.window_arrangement
    first, second = wa.editor_buffers[0], wa.editor_buffers[1]
    assert wa.active_editor_buffer is first

    handle_command(editor, ':bnext')
    assert wa.active_editor_buffer is second

    handle_command(editor, ':bprev')
    assert wa.active_editor_buffer is first

    handle_command(editor, ':bprevious')
    assert wa.active_editor_buffer is second

    second.buffer.text = 'unsaved'
    handle_command(editor, ':bnext')
    assert wa.active_editor_buffer is second
    assert editor.message == 'No write since last change (add ! to override)'

    handle_command(editor, ':bnext!')
    assert wa.active_editor_buffer is first

    first.buffer.text = 'also unsaved'
    handle_command(editor, ':bprev')
    assert wa.active_editor_buffer is first
    assert editor.message == 'No write since last change (add ! to override)'

    handle_command(editor, ':bprevious!')
    assert wa.active_editor_buffer is second


def test_bdelete_and_bwipeout_aliases(editor, tmp_path):
    editor.load_initial_files([str(tmp_path / 'a.txt'), str(tmp_path / 'b.txt')])
    wa = editor.window_arrangement
    assert len(wa.editor_buffers) == 2

    wa.active_editor_buffer.buffer.text = 'dirty'
    handle_command(editor, ':bdelete')
    assert len(wa.editor_buffers) == 2
    assert editor.message == 'No write since last change (add ! to override)'

    handle_command(editor, ':bdelete!')
    assert len(wa.editor_buffers) == 1

    wa.active_editor_buffer.buffer.text = 'dirty again'
    handle_command(editor, ':bwipeout')
    assert editor.message == 'No write since last change (add ! to override)'

    handle_command(editor, ':bwipeout!')
    assert len(wa.editor_buffers) == 1
    assert wa.active_editor_buffer.location is None


def test_on_alias_keeps_only_active_window(editor, tmp_path):
    editor.load_initial_files([str(tmp_path / 'a.txt')])
    handle_command(editor, ':sp')
    assert editor.window_arrangement.active_tab.window_count() == 2

    handle_command(editor, ':on')
    assert editor.window_arrangement.active_tab.window_count() == 1


def test_close_and_clo_single_window_reports_e444(editor, monkeypatch):
    exits = _record_exits(editor, monkeypatch)
    editor.load_initial_files([None])

    handle_command(editor, ':clo')
    assert editor.message == 'E444: Cannot close last window'
    assert exits == []

    editor.message = None
    editor.window_arrangement.active_editor_buffer.buffer.text = 'dirty'
    handle_command(editor, ':close!')
    assert editor.message == 'E444: Cannot close last window'
    assert exits == []


def test_close_and_clo_multi_window_and_dirty_guard(editor, tmp_path):
    editor.load_initial_files([str(tmp_path / 'a.txt')])
    wa = editor.window_arrangement
    eb_a = wa.active_editor_buffer

    # Same dirty buffer visible in two splits: `:clo` succeeds without `!`.
    eb_a.buffer.text = 'dirty shared'
    handle_command(editor, ':sp')
    assert wa.active_tab.window_count() == 2
    handle_command(editor, ':clo')
    assert wa.active_tab.window_count() == 1

    # Different buffer in a second split: closing the last window showing a
    # dirty buffer blocks without `!` and succeeds with `:close!`.
    eb_b = EditorBuffer(editor, str(tmp_path / 'b.txt'))
    eb_b.buffer.text = 'dirty unique'
    wa._add_editor_buffer(eb_b)
    wa.active_tab.vsplit(eb_b)
    assert wa.active_editor_buffer is eb_b
    assert wa.active_tab.window_count() == 2

    handle_command(editor, ':close')
    assert wa.active_tab.window_count() == 2
    assert editor.message == 'No write since last change (add ! to override)'

    handle_command(editor, ':close!')
    assert wa.active_tab.window_count() == 1
    assert wa.active_editor_buffer is eb_a


def test_close_empty_arrangement_is_safe(editor):
    assert editor.window_arrangement.active_tab is None
    handle_command(editor, ':clo')
    handle_command(editor, ':close!')


def test_wqall_writes_all_dirty_buffers_and_exits(editor, tmp_path, monkeypatch):
    f1 = tmp_path / 'one.txt'
    f2 = tmp_path / 'two.txt'
    editor.load_initial_files([str(f1), str(f2)])
    wa = editor.window_arrangement
    wa.editor_buffers[0].buffer.text = 'alpha'
    wa.editor_buffers[1].buffer.text = 'beta'

    exits = _record_exits(editor, monkeypatch)
    handle_command(editor, ':wqall')

    assert exits == [1]
    assert f1.read_text() == 'alpha\n'
    assert f2.read_text() == 'beta\n'
