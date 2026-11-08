"""
Tests for Vim quit semantics (`:q` window-vs-exit, `:wqa` write-all) and
`:b` buffer resolution (docs/bugs/067.md).
"""

from vith.commands.handler import handle_command
from vith.window_arrangement import EditorBuffer


def _exits_recorder(editor, monkeypatch):
    exits = []
    monkeypatch.setattr(editor.application, "exit", lambda *a, **kw: exits.append(1))
    return exits


def test_q_exits_despite_hidden_clean_buffers(editor, monkeypatch):
    # 067: hidden buffers no longer block `:q` with "more files to edit".
    editor.load_initial_files([None])
    hidden = EditorBuffer(editor)
    editor.window_arrangement._add_editor_buffer(hidden)
    exits = _exits_recorder(editor, monkeypatch)

    handle_command(editor, ":q")

    assert exits == [1]
    assert editor.message is None


def test_q_closes_window_instead_of_exiting(editor, monkeypatch):
    editor.load_initial_files([None])
    wa = editor.window_arrangement
    _hidden = EditorBuffer(editor)
    wa._add_editor_buffer(_hidden)
    eb2 = EditorBuffer(editor)
    wa._add_editor_buffer(eb2)
    wa.active_tab.vsplit(eb2)
    assert wa.active_tab.window_count() == 2
    exits = _exits_recorder(editor, monkeypatch)

    # First `:q` closes the current window only:
    handle_command(editor, ":q")
    assert exits == []
    assert wa.active_tab.window_count() == 1

    # Second `:q` (last window) exits:
    handle_command(editor, ":q")
    assert exits == [1]


def test_q_blocks_on_dirty_buffers(editor, monkeypatch):
    editor.load_initial_files([None])
    eb = editor.window_arrangement.active_editor_buffer
    eb.buffer.text = "dirty"
    exits = _exits_recorder(editor, monkeypatch)

    handle_command(editor, ":q")

    assert exits == []
    assert editor.message == "No write since last change (add ! to override)"


def test_qa_exits_from_any_window(editor, monkeypatch):
    editor.load_initial_files([None])
    wa = editor.window_arrangement
    eb2 = EditorBuffer(editor)
    wa._add_editor_buffer(eb2)
    wa.active_tab.vsplit(eb2)
    exits = _exits_recorder(editor, monkeypatch)

    handle_command(editor, ":qa")

    assert exits == [1]


def test_wqa_writes_all_dirty_buffers_and_exits(editor, editor_buffer, tmp_path, monkeypatch):
    # 067: `:wqa` must write *every* dirty buffer (not just the active one).
    editor_buffer.buffer.text = "first"
    editor_buffer.location = str(tmp_path / "a.txt")
    editor.window_arrangement._add_editor_buffer(editor_buffer)
    editor.sync_with_prompt_toolkit()

    eb2 = EditorBuffer(editor, str(tmp_path / "b.txt"))
    eb2.buffer.text = "second"
    editor.window_arrangement._add_editor_buffer(eb2)
    editor.sync_with_prompt_toolkit()

    exits = _exits_recorder(editor, monkeypatch)
    handle_command(editor, ":wqa")

    assert exits == [1]
    assert (tmp_path / "a.txt").read_text() == "first\n"
    assert (tmp_path / "b.txt").read_text() == "second\n"


def test_wqa_aborts_when_a_write_fails(editor, editor_buffer, tmp_path, monkeypatch):
    editor_buffer.buffer.text = "first"
    editor_buffer.location = str(tmp_path / "missing-dir" / "a.txt")  # unwritable
    editor.window_arrangement._add_editor_buffer(editor_buffer)
    editor.sync_with_prompt_toolkit()

    good = tmp_path / "b.txt"
    eb2 = EditorBuffer(editor, str(good))
    eb2.buffer.text = "second"
    editor.window_arrangement._add_editor_buffer(eb2)
    editor.sync_with_prompt_toolkit()

    exits = _exits_recorder(editor, monkeypatch)
    handle_command(editor, ":wqa")

    assert exits == []
    assert editor.message  # failure surfaced
    assert not good.exists()


def test_command_stacks_normalize_on_base(monkeypatch):
    # 075 row 3: stacked command decorators must both bind the base function
    # via `_unwrap_command` (a wrapped alias can be re-aliased safely),
    # while the module-level name keeps the original callable.
    from unittest.mock import MagicMock

    from vith.commands import commands as commands_mod

    calls = []

    def fake_command(editor, force=False):
        calls.append(("cmd", force))

    commands_mod.cmd('fake-a', accepts_force=True)(fake_command)
    commands_mod.cmd('fake-b', accepts_force=True)(commands_mod.COMMANDS_TO_HANDLERS['fake-a'])

    try:
        editor = MagicMock()
        editor.show_message = lambda *a: None
        commands_mod.COMMANDS_TO_HANDLERS['fake-b'](editor, {"force": True, "location": None})
        assert calls == [("cmd", True)]
    finally:
        commands_mod.COMMANDS_TO_HANDLERS.pop('fake-a', None)
        commands_mod.COMMANDS_TO_HANDLERS.pop('fake-b', None)
