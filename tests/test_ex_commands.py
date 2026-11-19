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


def test_undo_and_redo_commands_roundtrip_and_boundaries(editor):
    editor.load_initial_files([None])
    eb = editor.window_arrangement.active_editor_buffer
    buff = eb.buffer

    # Empty stacks report Vim boundary messages.
    handle_command(editor, ':u')
    assert editor.message == 'Already at oldest change'
    handle_command(editor, ':red')
    assert editor.message == 'Already at newest change'

    # Push edits and test :u, :undo, :red, :redo round-trip + cursor restore.
    buff.text = 'first'
    buff.cursor_position = 2
    buff.save_to_undo_stack()
    buff.text = 'second'
    buff.cursor_position = 4
    buff.save_to_undo_stack()
    buff.text = 'third'

    editor.message = None
    handle_command(editor, ':u')
    assert buff.text == 'second'
    assert buff.cursor_position == 4
    assert editor.message is None

    handle_command(editor, ':undo')
    assert buff.text == 'first'
    assert buff.cursor_position == 2

    handle_command(editor, ':undo')
    assert buff.text == 'first'
    assert editor.message == 'Already at oldest change'

    editor.message = None
    handle_command(editor, ':red')
    assert buff.text == 'second'
    assert editor.message is None

    handle_command(editor, ':redo')
    assert buff.text == 'third'

    handle_command(editor, ':redo')
    assert buff.text == 'third'
    assert editor.message == 'Already at newest change'


def test_undo_and_redo_read_only_empty_arrangement_and_thai_gate(editor):
    from prompt_toolkit.filters import to_filter

    from vith.commands.preview import CommandPreviewer
    from vith.thai import _THAI_GATE, reset_thai_text_gate

    # Empty arrangement never raises.
    handle_command(editor, ':u')
    handle_command(editor, ':redo')

    # Read-only buffer (prompt_toolkit Filter) rejects undo and redo without mutating.
    editor.load_initial_files([None])
    eb = editor.window_arrangement.active_editor_buffer
    eb.buffer.text = 'สวัสดี'
    eb.buffer.save_to_undo_stack()
    eb.buffer.text = 'plain ascii'
    eb.buffer.read_only = to_filter(True)

    handle_command(editor, ':u')
    assert eb.buffer.text == 'plain ascii'
    assert editor.message == "E21: Cannot make changes, 'modifiable' is off"

    editor.message = None
    handle_command(editor, ':red')
    assert editor.message == "E21: Cannot make changes, 'modifiable' is off"

    # Unlocking read_only allows undo; restoring Thai latches the Thai gate.
    eb.buffer.read_only = to_filter(False)
    reset_thai_text_gate()
    assert not _THAI_GATE.value

    # Previewing :undo / :redo must not mutate the buffer.
    previewer = CommandPreviewer(editor)
    previewer.preview(':undo')
    assert eb.buffer.text == 'plain ascii'

    handle_command(editor, ':undo')
    assert eb.buffer.text == 'สวัสดี'
    assert _THAI_GATE.value

    # Redo back to ASCII, then test Thai latch on redo as well.
    handle_command(editor, ':redo')
    assert eb.buffer.text == 'plain ascii'

    # Reject `!` on :u! and :redo!.
    handle_command(editor, ':u!')
    assert editor.message == 'No ! allowed'
    handle_command(editor, ':redo!')
    assert editor.message == 'No ! allowed'


def test_noh_and_nohlsearch_suppress_and_auto_restore_on_search(editor):
    from prompt_toolkit.layout.processors import ConditionalProcessor, HighlightSearchProcessor
    from prompt_toolkit.search import SearchDirection, SearchState

    from vith.commands.preview import CommandPreviewer

    editor.load_initial_files([None])
    eb = editor.window_arrangement.active_editor_buffer
    eb.buffer.text = 'alpha beta alpha'
    editor.sync_with_prompt_toolkit()

    control = editor.editor_layout._create_buffer_control(eb)
    search_proc = next(
        p
        for p in control.input_processors
        if isinstance(p, ConditionalProcessor) and isinstance(p.processor, HighlightSearchProcessor)
    )

    assert editor.highlight_search is True
    assert editor._hlsearch_suppressed is False
    assert search_proc.filter() is True

    # Previewing `:noh` must not suppress highlighting.
    previewer = CommandPreviewer(editor)
    previewer.preview(':noh')
    assert editor._hlsearch_suppressed is False

    # `:noh` suppresses highlight while keeping `highlight_search = True`.
    handle_command(editor, ':noh')
    assert editor.highlight_search is True
    assert editor._hlsearch_suppressed is True
    assert search_proc.filter() is False

    # Typing in `search_buffer` clears suppression.
    editor.search_buffer.text = 'alpha'
    assert editor._hlsearch_suppressed is False
    assert search_proc.filter() is True

    # `:nohlsearch` suppresses again; `apply_search` (n/N/*/#) clears it.
    handle_command(editor, ':nohlsearch')
    assert editor._hlsearch_suppressed is True
    eb.buffer.apply_search(SearchState(text='alpha', direction=SearchDirection.FORWARD))
    assert editor._hlsearch_suppressed is False

    # `:s` and `:s/.../n` clear suppression.
    handle_command(editor, ':noh')
    assert editor._hlsearch_suppressed is True
    handle_command(editor, ':s/alpha/gamma/n')
    assert editor._hlsearch_suppressed is False

    handle_command(editor, ':noh')
    assert editor._hlsearch_suppressed is True
    handle_command(editor, ':s/alpha/gamma/')
    assert editor._hlsearch_suppressed is False

    # Previewing `:set hls` temporarily clears suppression and restores on Esc;
    # executing `:set hls` permanently clears suppression.
    handle_command(editor, ':noh')
    assert editor._hlsearch_suppressed is True
    previewer2 = CommandPreviewer(editor)
    previewer2.preview(':set hls')
    assert editor._hlsearch_suppressed is False
    previewer2.restore()
    assert editor._hlsearch_suppressed is True

    handle_command(editor, ':set hls')
    assert editor._hlsearch_suppressed is False

    # Focusing search_buffer on key press clears suppression too.
    handle_command(editor, ':noh')
    editor.application.layout.focus(editor.search_buffer)
    editor.application.key_processor.after_key_press.fire()
    assert editor._hlsearch_suppressed is False


def test_update_only_writes_when_modified_or_named_target(editor, tmp_path, monkeypatch):
    target = tmp_path / 'note.txt'
    target.write_text('initial\n')
    editor.load_initial_files([str(target)])
    eb = editor.window_arrangement.active_editor_buffer

    writes = []
    orig_write = eb.write

    def tracked_write(*args, **kwargs):
        writes.append((args, kwargs))
        return orig_write(*args, **kwargs)

    monkeypatch.setattr(eb, 'write', tracked_write)

    # Clean buffer: `:up` is a clean no-op without touching disk.
    handle_command(editor, ':up')
    assert writes == []
    assert editor.message is None

    # Dirty buffer: `:update` writes changes.
    eb.buffer.text = 'updated'
    handle_command(editor, ':update')
    assert len(writes) == 1
    assert target.read_text() == 'updated\n'
    assert not eb.has_unsaved_changes

    # Clean buffer with explicit destination writes to that destination.
    other = tmp_path / 'copy.txt'
    handle_command(editor, f':up {other}')
    assert len(writes) == 2
    assert other.read_text() == 'updated\n'


def test_update_empty_arrangement_guards(editor, tmp_path):
    handle_command(editor, ':up')
    assert editor.message is None

    handle_command(editor, f':up {tmp_path / "nowhere.txt"}')
    assert editor.message == 'No file name'


def test_xit_and_exit_conditional_write_and_multi_window_semantics(editor, tmp_path, monkeypatch):
    f1 = tmp_path / 'a.txt'
    f2 = tmp_path / 'b.txt'
    f1.write_text('orig a\n')
    f2.write_text('orig b\n')
    editor.load_initial_files([str(f1)])
    wa = editor.window_arrangement
    eb1 = wa.active_editor_buffer

    exits = _record_exits(editor, monkeypatch)

    # Multi-window: `:x` writes dirty active buffer and closes ONLY its window.
    handle_command(editor, f':vsp {f2}')
    eb2 = wa.active_editor_buffer
    assert wa.active_tab.window_count() == 2
    eb2.buffer.text = 'changed b'

    handle_command(editor, ':x')
    assert f2.read_text() == 'changed b\n'
    assert exits == []
    assert wa.active_tab.window_count() == 1
    assert wa.active_editor_buffer is eb1

    # Failed write (existing target without `!`): `:xit` aborts without exiting.
    handle_command(editor, f':xit {f2}')
    assert editor.message == 'File exists (add ! to override)'
    assert exits == []
    assert f2.read_text() == 'changed b\n'

    # Clean last window: `:exit` exits without rewriting `f1`.
    writes = []
    orig_write = eb1.write

    def tracked_write(*args, **kwargs):
        writes.append(1)
        return orig_write(*args, **kwargs)

    monkeypatch.setattr(eb1, 'write', tracked_write)
    editor.message = None
    handle_command(editor, ':exit')
    assert writes == []
    assert exits == [1]


def test_wall_and_xall_write_dirty_buffers_and_abort_on_error(editor, tmp_path, monkeypatch):
    f1 = tmp_path / 'w1.txt'
    f2 = tmp_path / 'w2.txt'
    f1.write_text('one\n')
    f2.write_text('two\n')
    editor.load_initial_files([str(f1), str(f2)])
    wa = editor.window_arrangement
    eb1, eb2 = wa.editor_buffers[0], wa.editor_buffers[1]

    # Clean buffers: `:wa` writes nothing.
    writes = []
    for eb in (eb1, eb2):
        orig = eb.write

        def make_tracker(fn):
            def tracked(*args, **kwargs):
                writes.append(1)
                return fn(*args, **kwargs)

            return tracked

        monkeypatch.setattr(eb, 'write', make_tracker(orig))

    handle_command(editor, ':wa')
    assert writes == []

    # Dirty buffers: `:wall` writes only dirty ones.
    eb1.buffer.text = 'one edited'
    handle_command(editor, ':wall')
    assert writes == [1]
    assert f1.read_text() == 'one edited\n'
    assert not eb1.has_unsaved_changes

    # Dirty untitled buffer causes `:xa` to abort with 'No file name'.
    exits = _record_exits(editor, monkeypatch)
    untitled = EditorBuffer(editor)
    untitled.buffer.text = 'unnamed work'
    wa._add_editor_buffer(untitled)
    handle_command(editor, ':xa')
    assert editor.message == 'No file name'
    assert exits == []

    # Naming the untitled buffer allows `:xall` to write all dirty buffers and exit.
    f3 = tmp_path / 'w3.txt'
    untitled.location = str(f3)
    eb2.buffer.text = 'two edited'
    handle_command(editor, ':xall')
    assert exits == [1]
    assert f2.read_text() == 'two edited\n'
    assert f3.read_text() == 'unnamed work\n'


def test_enew_and_ene_create_untitled_buffer_in_current_window(editor, tmp_path):
    f1 = tmp_path / 'existing.txt'
    f1.write_text('hello\n')
    editor.load_initial_files([str(f1)])
    wa = editor.window_arrangement
    orig_eb = wa.active_editor_buffer

    # Split so we verify `:enew` replaces only the current window, without new splits.
    handle_command(editor, ':sp')
    assert wa.active_tab.window_count() == 2

    handle_command(editor, ':enew')
    assert wa.active_tab.window_count() == 2
    new_eb = wa.active_editor_buffer
    assert new_eb is not orig_eb
    assert new_eb.location is None
    assert new_eb.buffer.text == ''
    assert orig_eb in wa.editor_buffers

    # Dirty buffer blocks `:ene` without `!`, succeeds with `:ene!`.
    new_eb.buffer.text = 'unsaved scratch'
    handle_command(editor, ':ene')
    assert wa.active_editor_buffer is new_eb
    assert editor.message == 'No write since last change (add ! to override)'

    handle_command(editor, ':ene!')
    assert wa.active_editor_buffer is not new_eb
    assert wa.active_editor_buffer.location is None
    assert wa.active_editor_buffer.buffer.text == ''


def test_enew_on_empty_arrangement_creates_initial_buffer(editor):
    assert editor.window_arrangement.active_editor_buffer is None
    handle_command(editor, ':enew')
    eb = editor.window_arrangement.active_editor_buffer
    assert eb is not None
    assert eb.location is None
    assert eb.buffer.text == ''


def test_file_command_status_and_rename(editor, tmp_path):
    from prompt_toolkit.filters import to_filter

    from vith.commands.preview import CommandPreviewer
    from vith.thai import _THAI_GATE, reset_thai_text_gate

    # Empty arrangement reports 'No file name'.
    handle_command(editor, ':f')
    assert editor.message == 'No file name'

    # Existing multi-line file status (cursor at line 2 of 4 -> 50%).
    target = tmp_path / 'notes.txt'
    target.write_text('l1\nl2\nl3\nl4\n')
    editor.load_initial_files([str(target)])
    eb = editor.window_arrangement.active_editor_buffer
    eb.buffer.cursor_position = eb.buffer.document.translate_row_col_to_index(1, 0)

    handle_command(editor, ':f')
    assert editor.message == f'"{target}" 4 lines --50%--'

    # Modified + readonly flags appear cleanly.
    eb.buffer.text = 'l1\nl2\nl3\nl4\nl5'
    eb.buffer.read_only = to_filter(True)
    handle_command(editor, ':file')
    assert '[readonly][Modified]' in editor.message
    assert '5 lines' in editor.message
    eb.buffer.read_only = to_filter(False)

    # Previewing `:file renamed.py` must NOT mutate `eb.location`.
    previewer = CommandPreviewer(editor)
    previewer.preview(':file renamed.py')
    assert eb.location == str(target)

    # Renaming updates location, resets filetype detection, refreshes title,
    # and latches the Thai gate when the new filename contains Thai.
    reset_thai_text_gate()
    new_name = str(tmp_path / 'สวัสดี.py')
    handle_command(editor, f':file {new_name}')
    assert eb.location == new_name
    assert eb.filetype == 'python'
    assert editor.get_title() == 'vith - สวัสดี.py'
    assert _THAI_GATE.value
    assert new_name in editor.message

    # `:f!` is rejected.
    handle_command(editor, ':f!')
    assert editor.message == 'No ! allowed'


def test_file_command_untitled_and_new_named_flags(editor, tmp_path):
    editor.load_initial_files([None])
    handle_command(editor, ':f')
    assert editor.message == '"[New file]" 1 line --100%--'

    fresh = tmp_path / 'brand_new.txt'
    handle_command(editor, f':f {fresh}')
    assert editor.message == f'"{fresh}" [New file] 1 line --100%--'


def test_version_and_ver_commands_use_pager_and_report_runtime_info(editor, monkeypatch):
    import prompt_toolkit

    from vith import __version__ as vith_version
    from vith.commands.commands import version_lines

    captured = _capture_pager(monkeypatch)

    # Works even when no buffer is open (empty arrangement).
    empty_lines = version_lines(editor)
    assert any(f'vith {vith_version}' in line for line in empty_lines)
    assert any(f'prompt_toolkit {prompt_toolkit.__version__}' in line for line in empty_lines)
    assert any('Thai word segmentation:' in line for line in empty_lines)
    assert any('Cold-text compression:' in line for line in empty_lines)

    # With an active buffer open, `:ver` and `:version` emit identical pager output.
    editor.load_initial_files([None])
    handle_command(editor, ':ver')
    handle_command(editor, ':version')

    assert len(captured) == 2
    assert captured[0] == captured[1]
    assert f'vith {vith_version}' in captured[0]
    assert 'Cold-text compression:' in captured[0]

    # `:ver!` is rejected.
    handle_command(editor, ':ver!')
    assert editor.message == 'No ! allowed'
