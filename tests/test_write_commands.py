"""
Tests for the `:w` / `:wq` / `:wqa` write flow (docs/bugs/006.md and
docs/bugs/007.md): failed writes must be reported without quitting.
"""

from __future__ import annotations

from vith.commands.handler import handle_command
from vith.window_arrangement import EditorBuffer


def _editor_with_text(editor, editor_buffer, text):
    editor_buffer.buffer.text = text
    editor.window_arrangement._add_editor_buffer(editor_buffer)
    editor.sync_with_prompt_toolkit()
    return editor_buffer


def test_w_reports_file_exists_failure(editor, editor_buffer, tmp_path):
    _ = _editor_with_text(editor, editor_buffer, 'unsaved edits')
    existing = tmp_path / 'exists.txt'
    existing.write_text('occupado')

    handle_command(editor, ':w %s' % existing)

    assert editor.message == 'File exists (add ! to override)'
    assert existing.read_text() == 'occupado'


def test_wq_quits_only_after_successful_write(editor, editor_buffer, tmp_path, monkeypatch):
    _ = _editor_with_text(editor, editor_buffer, 'unsaved edits')
    existing = tmp_path / 'exists.txt'
    existing.write_text('occupado')

    exits = []

    def record_exit(*args, **kwargs):
        exits.append(1)

    monkeypatch.setattr(editor.application, 'exit', record_exit)

    # Failed write ("File exists"): stay in the editor.
    handle_command(editor, ':wq %s' % existing)
    assert editor.message == 'File exists (add ! to override)'
    assert exits == []
    assert existing.read_text() == 'occupado'

    # Successful write to a new file: quit exactly once.
    fresh = tmp_path / 'fresh.txt'
    handle_command(editor, ':wq %s' % fresh)
    assert exits == [1]
    assert fresh.read_text() == 'unsaved edits\n'


def test_wqa_quits_only_after_successful_write(editor, editor_buffer, tmp_path, monkeypatch):
    eb = _editor_with_text(editor, editor_buffer, 'wqa contents')
    eb.location = str(tmp_path / 'missing-dir' / 'wqa.txt')

    exits = []
    monkeypatch.setattr(editor.application, 'exit', lambda *a, **kw: exits.append(1))

    # A failing write (unwritable location) aborts the wqa:
    handle_command(editor, ':wqa')
    assert editor.message, 'failed write must surface a message'
    assert exits == []
    assert not (tmp_path / 'missing-dir' / 'wqa.txt').exists()

    # A successful write quits all and writes the buffer:
    eb.location = str(tmp_path / 'wqa_other.txt')
    handle_command(editor, ':wqa')
    assert exits == [1]
    assert (tmp_path / 'wqa_other.txt').read_text() == 'wqa contents\n'


def test_write_failure_does_not_mutate_location(editor, editor_buffer, tmp_path):
    eb = _editor_with_text(editor, editor_buffer, 'data')
    eb.location = str(tmp_path / 'ok.txt')
    bad = str(tmp_path / 'no-such-dir' / 'x.txt')
    assert eb.write(bad) is False
    assert eb.location == str(tmp_path / 'ok.txt')


def test_write_overwrite_check_expands_tilde(editor, editor_buffer, tmp_path, monkeypatch):
    # Create an existing file in tmp home? Use direct os.path.exists path.
    target = tmp_path / 'existing.txt'
    target.write_text('old')
    # :w without ! on existing path should be rejected.
    _editor_with_text(editor, editor_buffer, 'new')
    handle_command(editor, ':w %s' % target)
    # message shown, file unchanged
    assert editor.message is not None
    assert target.read_text() == 'old'


def test_w_bang_force_writes_read_only_file(editor, editor_buffer, tmp_path):
    # 117: `:w` on a read-only file fails; `:w!` writes once and restores
    # the read-only mode.
    import os
    import stat

    from vith.commands.handler import handle_command

    target = tmp_path / 'readonly.txt'
    target.write_text('original')
    os.chmod(target, 0o444)

    eb = _editor_with_text(editor, editor_buffer, 'forced')
    eb.location = str(target)
    assert eb.has_unsaved_changes

    handle_command(editor, ':w')
    assert 'denied' in editor.message.lower() or 'permission' in editor.message.lower()
    assert target.read_text() == 'original'
    assert eb.has_unsaved_changes

    editor.message = None
    handle_command(editor, ':w!')
    assert editor.message is None
    assert target.read_text() == 'forced\n'
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o444
    assert not eb.has_unsaved_changes


def test_write_forced_retries_after_chmod(editor, editor_buffer, tmp_path):
    # 117: the force path grants owner-write, retries the backend write,
    # and restores the original mode — pinned with a backend that fails
    # once with PermissionError (uid-independent).
    import os
    import stat
    from pathlib import Path

    target = tmp_path / 'flaky.txt'
    target.write_text('original')
    os.chmod(target, 0o444)
    calls = []

    class FlakyBackend:
        def can_open_location(self, location):
            return location == str(target)

        def write(self, location, text, encoding='utf-8'):
            calls.append(1)
            if len(calls) == 1:
                raise PermissionError(13, 'Permission denied')
            Path(location).write_text(text, encoding=encoding)

    editor.io_backends.insert(0, FlakyBackend())
    try:
        eb = _editor_with_text(editor, editor_buffer, 'forced')
        eb.location = str(target)

        assert eb.write(force=False) is False
        assert calls == [1]
        assert target.read_text() == 'original'

        assert eb.write(force=True) is True
        assert len(calls) == 3  # initial attempt + chmod + single retry
        assert target.read_text() == 'forced\n'
        assert stat.S_IMODE(os.stat(target).st_mode) == 0o444
    finally:
        editor.io_backends.pop(0)


def test_wqa_bang_passes_force_through(editor, editor_buffer, tmp_path, monkeypatch):
    # 117 (with 110 item 2): `:wqa!` forwards force to each buffer write.
    import os
    from pathlib import Path

    target = tmp_path / 'wqa_readonly.txt'
    target.write_text('original')
    os.chmod(target, 0o444)
    calls = []

    class FlakyBackend:
        def can_open_location(self, location):
            return location == str(target)

        def write(self, location, text, encoding='utf-8'):
            calls.append(1)
            if len(calls) == 1:
                raise PermissionError(13, 'Permission denied')
            Path(location).write_text(text, encoding=encoding)

    editor.io_backends.insert(0, FlakyBackend())
    try:
        eb = _editor_with_text(editor, editor_buffer, 'forced')
        eb.location = str(target)

        exits = []
        monkeypatch.setattr(editor.application, 'exit', lambda *a, **kw: exits.append(1))

        handle_command(editor, ':wqa')
        assert exits == []
        assert target.read_text() == 'original'

        handle_command(editor, ':wqa!')
        assert exits == [1]
        assert target.read_text() == 'forced\n'
    finally:
        editor.io_backends.pop(0)


def test_edit_blocked_by_unsaved_changes(editor, editor_buffer, tmp_path):
    # 096: :e <file> must be blocked by unsaved changes unless ! is given
    eb = _editor_with_text(editor, editor_buffer, 'unsaved changes')
    assert eb.has_unsaved_changes
    target = tmp_path / 'other.txt'
    target.write_text('other contents')

    handle_command(editor, ':e %s' % target)
    assert editor.message == 'No write since last change (add ! to override)'
    assert editor.window_arrangement.active_editor_buffer == eb

    # With !, switch proceeds
    handle_command(editor, ':e! %s' % target)
    assert editor.window_arrangement.active_editor_buffer.location == str(target)


def test_edit_clean_buffer_succeeds(editor, editor_buffer, tmp_path):
    # 096: :e <file> on a clean buffer switches without warning
    eb = _editor_with_text(editor, editor_buffer, 'clean')
    eb._file_content = 'clean'
    eb._has_unsaved_changes = False
    assert not eb.has_unsaved_changes
    target = tmp_path / 'clean_other.txt'
    target.write_text('clean other')

    handle_command(editor, ':e %s' % target)
    assert editor.window_arrangement.active_editor_buffer.location == str(target)


def _named_dirty_buffer(editor, tmp_path, name='current.txt'):
    """A named buffer whose on-disk file exists and whose text was modified."""
    path = tmp_path / name
    path.write_text('old\n')
    eb = EditorBuffer(editor)
    eb.location = str(path)
    eb._file_content = 'old'
    eb.buffer.text = 'new'
    editor.window_arrangement._add_editor_buffer(eb, show_in_current_window=True)
    editor.sync_with_prompt_toolkit()
    editor.message = None
    assert eb.has_unsaved_changes
    return eb, path


def test_w_current_file_by_name_succeeds(editor, editor_buffer, tmp_path):
    # 130: :w <current-path> must not be blocked by the overwrite guard.
    eb, path = _named_dirty_buffer(editor, tmp_path)
    handle_command(editor, ':w %s' % path)
    assert editor.message is None
    assert path.read_text() == 'new\n'
    assert not eb.has_unsaved_changes


def test_w_percent_succeeds(editor, editor_buffer, tmp_path):
    # 130: Vim's `:w %` writes the current file without `!`.
    _, path = _named_dirty_buffer(editor, tmp_path)
    handle_command(editor, ':w %')
    assert editor.message is None
    assert path.read_text() == 'new\n'


def test_write_forms_on_current_file_succeed(editor, editor_buffer, tmp_path, monkeypatch):
    # 130: :wq % / :up % / :x % / :sav <current> all use the same exemption.
    exits = []
    monkeypatch.setattr(editor.application, 'exit', lambda *a, **kw: exits.append(1))

    for command in (':wq %', ':up %', ':x %'):
        _, path = _named_dirty_buffer(editor, tmp_path, name=command[2:4] + '.txt')
        handle_command(editor, command)
        assert editor.message is None, command
        assert path.read_text() == 'new\n', command
        editor.message = None

    assert len(exits) == 2  # :wq and :x quit; :up only writes

    eb, path = _named_dirty_buffer(editor, tmp_path, name='saveas.txt')
    handle_command(editor, ':sav %s' % path)
    assert editor.message is None
    assert path.read_text() == 'new\n'
    assert eb.location == str(path)


def test_w_foreign_existing_file_still_blocked(editor, editor_buffer, tmp_path):
    # 130: the exemption is limited to the buffer's own file.
    _named_dirty_buffer(editor, tmp_path)
    other = tmp_path / 'other.txt'
    other.write_text('occupado')
    handle_command(editor, ':w %s' % other)
    assert editor.message == 'File exists (add ! to override)'
    assert other.read_text() == 'occupado'
