"""
Tests for Phase 1 Ex commands: missing aliases, `:close`, `:undo`/`:redo`,
`:nohlsearch`, conditional write/exit (`:update`, `:xit`, `:wall`, `:xall`),
and buffer/info/diagnostics (`:enew`, `:file`, `:version`, `:messages`).
"""

from __future__ import annotations

from vith.commands.handler import handle_command
from vith.editor_buffer import EditorBuffer


def _record_exits(editor, monkeypatch):
    exits = []

    def fake_exit(*args, **kwargs):
        exits.append(1)

    monkeypatch.setattr(editor.application, 'exit', fake_exit)
    return exits


def test_ls_and_files_aliases_open_read_only_info_split(editor, tmp_path):
    # Empty arrangement shows a message without creating a split.
    handle_command(editor, ':ls')
    assert editor.message == 'No open buffers'
    assert editor.window_arrangement.active_editor_buffer is None

    target = tmp_path / 'sample.txt'
    target.write_text('hello\n')
    editor.load_initial_files([str(target)])
    wa = editor.window_arrangement
    orig_eb = wa.active_editor_buffer

    handle_command(editor, ':ls')
    assert wa.active_tab.window_count() == 2
    ls_eb = wa.active_editor_buffer
    assert ls_eb is not orig_eb
    assert ls_eb.is_scratch is True
    assert ls_eb.get_display_name() == '[Buffers]'
    assert ls_eb.buffer.read_only() is True
    assert 'sample.txt' in ls_eb.buffer.text
    first_output = ls_eb.buffer.text

    # Closing the info split with `:q` restores the original buffer and drops the scratch buffer.
    handle_command(editor, ':q')
    assert wa.active_tab.window_count() == 1
    assert wa.active_editor_buffer is orig_eb
    assert ls_eb not in wa.editor_buffers

    # `:files` produces identical split content.
    handle_command(editor, ':files')
    assert wa.active_tab.window_count() == 2
    assert wa.active_editor_buffer.buffer.text == first_output
    handle_command(editor, ':q')
    assert len(wa.editor_buffers) == 1


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


def test_only_is_tab_local_and_guards_dirty_windows(editor, tmp_path):
    # 132: `:only` closes windows in the current tab only and reports E445
    # when a dirty buffer would lose its last visible window.
    a = tmp_path / 'only-a.txt'
    b = tmp_path / 'only-b.txt'
    c = tmp_path / 'only-c.txt'
    for path in (a, b, c):
        path.write_text('x\n')
    editor.load_initial_files([str(a)])
    wa = editor.window_arrangement
    handle_command(editor, ':sp')
    handle_command(editor, ':tabe %s' % b)
    assert len(wa.tab_pages) == 2
    handle_command(editor, ':tabp')

    eb_c = EditorBuffer(editor, str(c))
    eb_c.buffer.text = 'dirty C'
    wa._add_editor_buffer(eb_c)
    wa.active_tab.hsplit(eb_c)
    # Keep A active so C is an "other window" with unsaved changes.
    wa.active_tab.active_window = wa.active_tab.windows()[0]
    assert wa.active_editor_buffer is not eb_c
    assert wa.active_tab.window_count() == 3

    handle_command(editor, ':only')
    assert editor.message == 'E445: Other window contains changes'
    assert wa.active_tab.window_count() == 3

    editor.message = None
    handle_command(editor, ':only!')
    assert wa.active_tab.window_count() == 1
    assert len(wa.tab_pages) == 2  # the other tab page survives
    assert wa.active_editor_buffer is not eb_c


def test_tabonly_and_tabclose_guard_dirty_buffers(editor, tmp_path):
    # 132: `:tabonly` / `:tabclose` report E37 unless `!` is given.
    a = tmp_path / 'tabs-a.txt'
    b = tmp_path / 'tabs-b.txt'
    a.write_text('a\n')
    b.write_text('b\n')
    editor.load_initial_files([str(a)])
    wa = editor.window_arrangement
    handle_command(editor, ':tabe %s' % b)
    assert len(wa.tab_pages) == 2
    wa.active_editor_buffer.buffer.text = 'dirty B'

    # `:tabclose` blocks while the dirty buffer's only tab would be removed.
    handle_command(editor, ':tabc')
    assert editor.message == 'E37: No write since last change (add ! to override)'
    assert len(wa.tab_pages) == 2

    handle_command(editor, ':tabc!')
    assert len(wa.tab_pages) == 1

    # Reopen a second tab, then `:tabonly` from the first tab must be guarded.
    handle_command(editor, ':tabe %s' % b)
    handle_command(editor, ':tabp')
    wa.tab_pages[1].active_window.editor_buffer.buffer.text = 'dirty B again'
    handle_command(editor, ':tabo')
    assert editor.message == 'E37: No write since last change (add ! to override)'
    assert len(wa.tab_pages) == 2

    editor.message = None
    handle_command(editor, ':tabo!')
    assert len(wa.tab_pages) == 1


def test_wq_closes_only_current_window(editor, tmp_path, monkeypatch):
    # 131: `:wq` must close the current window, not exit the application and
    # discard other windows' unsaved changes.
    exits = _record_exits(editor, monkeypatch)
    a = tmp_path / 'a.txt'
    b = tmp_path / 'b.txt'
    a.write_text('A0\n')
    b.write_text('B0\n')
    editor.load_initial_files([str(a)])
    wa = editor.window_arrangement
    eb_a = wa.active_editor_buffer
    eb_a.buffer.text = 'A-dirty'
    eb_b = EditorBuffer(editor, str(b))
    eb_b.buffer.text = 'B-dirty'
    wa._add_editor_buffer(eb_b)
    wa.active_tab.hsplit(eb_b)
    wa.active_tab.active_window = wa.active_tab.windows()[0]
    assert wa.active_editor_buffer is eb_a

    handle_command(editor, ':wq')

    assert a.read_text() == 'A-dirty\n'
    assert not eb_a.has_unsaved_changes
    assert exits == []
    assert wa.active_tab.window_count() == 1
    assert eb_b.has_unsaved_changes


def test_q_allows_dirty_buffer_visible_in_two_windows(editor, tmp_path, monkeypatch):
    # 131: `:q` uses :close's visible-count rule — a dirty buffer that stays
    # visible in another window must not block the close.
    exits = _record_exits(editor, monkeypatch)
    target = tmp_path / 'shared.txt'
    target.write_text('x\n')
    editor.load_initial_files([str(target)])
    wa = editor.window_arrangement
    eb = wa.active_editor_buffer
    eb.buffer.text = 'dirty'

    handle_command(editor, ':sp')
    assert wa.active_tab.window_count() == 2
    handle_command(editor, ':q')
    assert exits == []
    assert wa.active_tab.window_count() == 1
    assert editor.message is None

    # On the last window the same dirty buffer blocks `:q`.
    editor.message = None
    handle_command(editor, ':q')
    assert exits == []
    assert editor.message == 'No write since last change (add ! to override)'
    assert wa.active_tab.window_count() == 1


def test_zz_maps_to_x(editor, tmp_path, monkeypatch):
    # 131: `ZZ` is Vim's `:x` — it does not rewrite a clean file and it quits
    # a clean unnamed buffer.
    from unittest.mock import MagicMock

    from vith.key_bindings import create_key_bindings

    exits = _record_exits(editor, monkeypatch)
    binding = [b for b in create_key_bindings(editor).bindings if b.keys == ('Z', 'Z')][0]

    target = tmp_path / 'clean.txt'
    target.write_text('clean\n')
    editor.load_initial_files([str(target)])
    eb = editor.window_arrangement.active_editor_buffer
    eb._file_content = 'clean'
    eb._has_unsaved_changes = False
    writes = []
    original_write = eb.write

    def counting_write(*args, **kwargs):
        writes.append(1)
        return original_write(*args, **kwargs)

    monkeypatch.setattr(eb, 'write', counting_write)
    binding.handler(MagicMock())
    assert writes == []
    assert exits == [1]

    # Clean unnamed buffer: `:x` succeeds (no "No file name") and quits.
    exits.clear()
    editor.message = None
    editor.load_initial_files([])
    wa = editor.window_arrangement
    eb2 = EditorBuffer(editor, text='something')
    wa._add_editor_buffer(eb2, show_in_current_window=True)
    eb2._file_content = 'something'
    eb2._has_unsaved_changes = False
    binding.handler(MagicMock())
    assert exits == [1]
    assert editor.message is None


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


def test_version_and_ver_commands_open_read_only_info_split(editor):
    import prompt_toolkit

    from vith import __version__ as vith_version
    from vith.commands.commands import version_lines

    # Works even when no buffer is open (empty arrangement).
    empty_lines = version_lines(editor)
    assert any(f'vith {vith_version}' in line for line in empty_lines)
    assert any(f'prompt_toolkit {prompt_toolkit.__version__}' in line for line in empty_lines)
    assert any('Thai word segmentation:' in line for line in empty_lines)
    assert any('Cold-text compression:' in line for line in empty_lines)

    # Opening `:ver` on an empty arrangement creates a read-only info buffer cleanly.
    handle_command(editor, ':ver')
    wa = editor.window_arrangement
    assert wa.active_editor_buffer is not None
    assert wa.active_editor_buffer.is_scratch is True
    assert wa.active_editor_buffer.buffer.read_only() is True
    assert f'vith {vith_version}' in wa.active_editor_buffer.buffer.text

    # With an active buffer open, `:ver` and `:version` open identical splits
    # and clean up their scratch buffer on `:q`.
    handle_command(editor, ':enew')
    orig_eb = wa.active_editor_buffer
    assert orig_eb.is_scratch is False

    handle_command(editor, ':ver')
    assert wa.active_tab.window_count() == 2
    ver_eb = wa.active_editor_buffer
    assert ver_eb.get_display_name() == '[Version]'
    assert ver_eb.buffer.read_only() is True
    first_text = ver_eb.buffer.text
    assert f'vith {vith_version}' in first_text
    assert 'Cold-text compression:' in first_text

    handle_command(editor, ':q')
    assert wa.active_tab.window_count() == 1
    assert ver_eb not in wa.editor_buffers

    handle_command(editor, ':version')
    assert wa.active_tab.window_count() == 2
    assert wa.active_editor_buffer.buffer.text == first_text
    handle_command(editor, ':q')

    # `:ver!` is rejected.
    handle_command(editor, ':ver!')
    assert editor.message == 'No ! allowed'


def test_messages_and_mes_commands_open_read_only_info_split(editor):
    from vith.commands.commands import messages_lines

    editor.load_initial_files([None])
    wa = editor.window_arrangement
    orig_eb = wa.active_editor_buffer

    # Empty message log shows 'No messages' without self-recording or splitting.
    handle_command(editor, ':messages')
    assert editor.message == 'No messages'
    assert messages_lines(editor) == []
    assert wa.active_tab.window_count() == 1

    # Empty/None messages are ignored; non-empty messages are recorded.
    editor.show_message('')
    editor.show_message(None)
    assert messages_lines(editor) == []

    editor.show_message('first message')
    editor.show_message('second message')

    handle_command(editor, ':mes')
    assert wa.active_tab.window_count() == 2
    mes_eb = wa.active_editor_buffer
    assert mes_eb is not orig_eb
    assert mes_eb.is_scratch is True
    assert mes_eb.get_display_name() == '[Messages]'
    assert mes_eb.buffer.read_only() is True
    assert mes_eb.buffer.text == 'first message\nsecond message'

    handle_command(editor, ':q')
    assert wa.active_tab.window_count() == 1
    assert mes_eb not in wa.editor_buffers

    handle_command(editor, ':messages')
    assert wa.active_tab.window_count() == 2
    assert wa.active_editor_buffer.buffer.text == 'first message\nsecond message'
    handle_command(editor, ':q')

    # Bounded to 200 entries (oldest messages evicted).
    for i in range(210):
        editor.show_message(f'msg-{i}')
    history = messages_lines(editor)
    assert len(history) == 200
    assert history[0] == 'msg-10'
    assert history[-1] == 'msg-209'

    # `:mes!` is rejected.
    handle_command(editor, ':mes!')
    assert editor.message == 'No ! allowed'


def test_phase1_commands_appear_in_command_completer(editor):
    from prompt_toolkit.completion import CompleteEvent
    from prompt_toolkit.document import Document

    from vith.commands.commands import get_commands
    from vith.commands.completer import create_command_completer

    registered = set(get_commands())
    expected = {
        'ls',
        'files',
        'bnext',
        'bprev',
        'bprevious',
        'bdelete',
        'bwipeout',
        'on',
        'clo',
        'close',
        'wqall',
        'u',
        'undo',
        'red',
        'redo',
        'noh',
        'nohlsearch',
        'up',
        'update',
        'x',
        'xit',
        'exit',
        'wa',
        'wall',
        'xa',
        'xall',
        'enew',
        'ene',
        'f',
        'file',
        'ver',
        'version',
        'mes',
        'messages',
    }
    assert expected.issubset(registered)

    completer = create_command_completer(editor)
    event = CompleteEvent()
    for prefix, must_contain in (
        ('nohl', 'nohlsearch '),
        ('mess', 'messages '),
        ('vers', 'version '),
        ('bdel', 'bdelete '),
        ('enew', 'enew '),
    ):
        completions = [c.text for c in completer.get_completions(Document(prefix), event)]
        assert must_contain in completions, (prefix, completions)
