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


def test_q_closes_window_despite_dirty_other_window_or_hidden(editor, monkeypatch):
    # 095: dirty buffer in another window or hidden must not block :q closing clean window
    editor.load_initial_files(["clean.txt", "dirty.txt"])
    wa = editor.window_arrangement
    eb_clean = wa.active_editor_buffer
    eb_dirty = [b for b in wa.editor_buffers if b != eb_clean][0]
    eb_dirty.buffer.text = "dirty edits"
    assert eb_dirty.has_unsaved_changes
    assert not eb_clean.has_unsaved_changes

    # Split into 2 windows: clean window active, dirty buffer in other window
    wa.active_tab.vsplit(eb_dirty)
    assert wa.active_tab.window_count() == 2
    wa.cycle_focus()  # focus clean window
    assert wa.active_editor_buffer == eb_clean
    exits = _exits_recorder(editor, monkeypatch)

    handle_command(editor, ":q")
    assert exits == []
    assert wa.active_tab.window_count() == 1
    assert editor.message != "No write since last change (add ! to override)"


def test_q_force_closes_dirty_window(editor, monkeypatch):
    # 095: :q! closes dirty active window
    editor.load_initial_files([None])
    wa = editor.window_arrangement
    eb2 = EditorBuffer(editor)
    wa._add_editor_buffer(eb2)
    wa.active_tab.vsplit(eb2)
    assert wa.active_tab.window_count() == 2
    wa.active_editor_buffer.buffer.text = "unsaved in active"
    exits = _exits_recorder(editor, monkeypatch)

    handle_command(editor, ":q!")
    assert exits == []
    assert wa.active_tab.window_count() == 1


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


def test_goto_buffer_pattern_with_untitled_buffers_is_guarded(editor, monkeypatch):
    # 080: `:b <pattern>` used to crash with a TypeError whenever any buffer
    # had location=None (untitled/new buffers exist by default).
    from vith.commands.handler import handle_command

    editor.load_initial_files([None])  # creates the untitled buffer

    handle_command(editor, ":b zzz-nomatch")  # substring branch reached
    assert editor.message and editor.message.startswith("E94")
    handle_command(editor, ":b 2")  # numeric fallback after substring
    handle_command(editor, ":b 1")


def test_ls_pager_one_based_and_no_name(editor):
    # 087: `:ls` pager numbers must match the 1-based `:b <n>` numbering and
    # untitled buffers must print a name instead of repr(None).
    from vith.commands.commands import buffer_list_lines

    editor.load_initial_files([None])
    lines = buffer_list_lines(editor)
    assert lines and lines[0].lstrip().startswith("1 % ")  # was "0 %"
    assert "None" not in lines[0]


def test_goto_buffer_zero_and_negative_show_e94(editor):
    # 110 item 2: `:b 0` and `:b -1` must report E94 instead of wrapping via
    # negative Python list indexing to the last buffer.
    from vith.commands.handler import handle_command
    from vith.editor_buffer import EditorBuffer

    wa = editor.window_arrangement
    eb1 = EditorBuffer(editor, "first.txt")
    eb2 = EditorBuffer(editor, "second.txt")
    wa._add_editor_buffer(eb1)
    wa._add_editor_buffer(eb2)
    wa.go_to_buffer("1")
    assert wa.active_editor_buffer is eb1

    for invalid in ("0", "-1"):
        editor.message = None
        handle_command(editor, f":b {invalid}")
        assert editor.message == f"E94: No matching buffer for {invalid}"
        assert wa.active_editor_buffer is eb1


def test_edit_without_location_reloads_active_buffer_from_command_line(editor, tmp_path):
    # 110 item 11: `:e` with no argument uses `window_arrangement.active_editor_buffer`
    # even when the command buffer currently has focus (`current_editor_buffer is None`).
    from vith.commands.handler import handle_command

    p = tmp_path / "reload_me.txt"
    p.write_text("original\n")
    editor.load_initial_files([str(p)])
    eb = editor.window_arrangement.active_editor_buffer
    assert eb.buffer.text == "original"

    p.write_text("updated on disk\n")
    handle_command(editor, ":e")
    assert eb.buffer.text == "updated on disk"


def test_split_commands_focus_new_window(editor, monkeypatch):
    # 110 item 9: `:sp` and `:vsp` update the layout and focus the new split window.
    from vith.commands.handler import handle_command

    focused = []

    def fake_focus(target):
        focused.append(target)

    monkeypatch.setattr(editor.application.layout, "focus", fake_focus)
    editor.load_initial_files([None])
    handle_command(editor, ":sp")
    assert len(editor.window_arrangement.active_tab.windows()) == 2
    assert focused and focused[-1] is editor.window_arrangement.active_pt_window

    handle_command(editor, ":vsp")
    assert len(editor.window_arrangement.active_tab.windows()) == 3
    assert focused[-1] is editor.window_arrangement.active_pt_window


def test_wqa_force_and_empty_arrangement_command_guards(editor, tmp_path, monkeypatch):
    # 110 item 2: `:wqa!` is registered with `accepts_force=True`.
    # 111 items 6 & 7: commands and `_go_to_line` guard against `None` active/current buffers.
    from vith.commands.commands import _confirm_unsaved_changes
    from vith.commands.handler import handle_command

    exited = []

    def fake_exit():
        exited.append(True)

    monkeypatch.setattr(editor.application, "exit", fake_exit)

    # Empty arrangement guards (`active_editor_buffer is None`):
    assert _confirm_unsaved_changes(editor, force=False) is True
    handle_command(editor, ":e")
    handle_command(editor, ":w")
    handle_command(editor, ":q")
    assert exited == [True]
    exited.clear()

    # `:wqa!` writes dirty buffers and exits without "No ! allowed":
    f = tmp_path / "dirty.txt"
    f.write_text("initial\n")
    editor.load_initial_files([str(f)])
    eb = editor.window_arrangement.active_editor_buffer
    eb.buffer.text = "updated"

    editor.message = None
    handle_command(editor, ":wqa!")
    assert editor.message is None
    assert exited == [True]
    assert f.read_text() == "updated\n"


def test_press_enter_to_continue_shared_helper(monkeypatch):
    # 113 item 5 / 115 item 10: `rc_file` reuses `press_enter_to_continue` from `commands`.
    import builtins

    from vith import rc_file
    from vith.commands import commands as commands_mod

    assert rc_file.press_enter_to_continue is commands_mod.press_enter_to_continue

    prompts = []

    def fake_input(prompt=""):
        prompts.append(prompt)
        return ""

    monkeypatch.setattr(builtins, "input", fake_input)
    commands_mod.press_enter_to_continue()
    assert prompts == ["\nPress ENTER to continue..."]
