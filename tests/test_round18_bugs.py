"""Regression tests for the Round 18 findings (docs/bugs/204.md-229.md)."""

from unittest.mock import MagicMock

from prompt_toolkit.application.current import set_app
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.clipboard import ClipboardData
from prompt_toolkit.document import Document
from prompt_toolkit.key_binding.key_processor import KeyPress
from prompt_toolkit.key_binding.vi_state import InputMode
from prompt_toolkit.keys import Keys
from prompt_toolkit.selection import SelectionType

from vith.commands.handler import handle_command
from vith.commands.info import registers_lines
from vith.key_bindings import _net_inserted_text, create_key_bindings


def _insert_handlers(editor):
    """Return the counted-insert entry/exit handlers by their key tuples."""
    kb = create_key_bindings(editor)
    names = {
        "insert_with_count": ("i",),
        "append_with_count": ("a",),
        "leave_insert_with_repeat": ("escape",),
        "substitute_cell": ("s",),
        "open_line_below": ("o",),
        "open_line_above": ("O",),
    }
    return {
        names[b.handler.__name__]: b.handler for b in kb.bindings if b.handler.__name__ in names
    }


def _insert_event(buff, arg=None, mode=InputMode.INSERT):
    app = MagicMock()
    app.vi_state.input_mode = mode
    event = MagicMock()
    event.app = app
    event.current_buffer = buff
    event.arg = arg
    return event


def test_204_counted_insert_replays_only_net_inserted_text(editor):
    """`3iX<Esc>` still yields three X's (no regression from the diff fix)."""
    editor.load_initial_files([None])
    handlers = _insert_handlers(editor)
    buff = Buffer(document=Document("abc", 0))

    handlers[("i",)](_insert_event(buff, arg=3))
    buff.insert_text("X")
    handlers[("escape",)](_insert_event(buff))

    assert buff.text == "XXXabc"


def test_204_counted_insert_backspace_left_of_start(editor):
    """Backspacing before the recorded start must not slice an empty region."""
    editor.load_initial_files([None])
    handlers = _insert_handlers(editor)
    buff = Buffer(document=Document("abcdef", 3))

    handlers[("i",)](_insert_event(buff, arg=3))
    buff.cursor_position -= 1
    buff.delete()  # backspace over 'c'
    buff.cursor_position -= 1
    buff.delete()  # backspace over 'b'
    buff.insert_text("Z")
    handlers[("escape",)](_insert_event(buff))

    assert buff.text == "aZZZdef"


def test_204_stale_counted_insert_cleared_by_later_insert(editor):
    """A non-Esc exit (e.g. F1) followed by `o...<Esc>` must not replay stale text."""
    editor.load_initial_files([None])
    handlers = _insert_handlers(editor)
    buff = Buffer(document=Document("first\nsecond\nthird", 0))

    handlers[("i",)](_insert_event(buff, arg=3))
    buff.insert_text("X")
    # Leave insert mode without Esc (F1 / focus change): the state stays armed.
    assert getattr(buff, "_vith_insert_repeat", None) is not None

    # A later uncounted insert entry must clear the stale replay state.
    handlers[("o",)](_insert_event(buff))
    buff.insert_text("Y")
    handlers[("escape",)](_insert_event(buff))

    assert buff.text == "Xfirst\nY\nsecond\nthird"


def test_204_net_inserted_text_helper():
    assert _net_inserted_text("abc", "Xabc") == "X"
    assert _net_inserted_text("abcdef", "aZdef") == "Z"
    assert _net_inserted_text("abc", "abc") == ""


def _editor_with_lines(editor, text):
    editor.load_initial_files([None])
    eb = editor.window_arrangement.active_editor_buffer
    eb.buffer.text = text
    editor.sync_with_prompt_toolkit()
    return eb


def test_205_normal_mode_named_register_available_to_ex_put(editor):
    eb = _editor_with_lines(editor, "alpha\nbeta\ngamma")
    editor.application.vi_state.named_registers["a"] = ClipboardData(
        "normal yanked", SelectionType.LINES
    )

    handle_command(editor, ":2put a")

    assert "normal yanked" in eb.buffer.document.lines
    assert editor.message is None


def test_205_ex_yank_uses_shared_store_without_duplicate_rows(editor):
    _editor_with_lines(editor, "line one\nline two")

    handle_command(editor, ":1y a")

    data = editor.application.vi_state.named_registers["a"]
    assert data.text == "line one"
    assert data.type == SelectionType.LINES
    a_rows = [row for row in registers_lines(editor) if '"a ' in row]
    assert len(a_rows) == 1


def test_205_explicit_unnamed_register_put(editor):
    eb = _editor_with_lines(editor, "line one\nline two")
    handle_command(editor, ":1y a")

    handle_command(editor, ':2put "')

    assert eb.buffer.document.lines[2] == "line one"


def _feed(editor, *keys):
    key_processor = editor.application.key_processor
    with set_app(editor.application):
        for key in keys:
            key_processor.feed(KeyPress(key))
        key_processor.process_keys()


def test_207_f1_from_command_mode_restores_preview(editor):
    editor.load_initial_files([None])
    editor.show_line_numbers = True

    editor.enter_command_mode()
    editor.command_buffer.text = "set nonumber"
    assert editor.show_line_numbers is False  # preview is live

    _feed(editor, Keys.F1)

    assert editor.show_line_numbers is True  # preview aborted/restored
    assert editor.command_buffer.text == ""
    assert editor.application.vi_state.input_mode == InputMode.NAVIGATION


def test_207_f1_from_search_mode_leaves_insert(editor):
    editor.load_initial_files([None])
    editor.application.layout.focus(editor.search_buffer)
    editor.application.vi_state.input_mode = InputMode.INSERT
    editor.search_buffer.text = "needle"

    _feed(editor, Keys.F1)

    assert editor.search_buffer.text == ""
    assert editor.application.vi_state.input_mode == InputMode.NAVIGATION


def test_206_force_write_tilde_path(editor, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    path = tmp_path / "ro.txt"
    path.write_text("original", encoding="utf-8")
    path.chmod(0o444)

    editor.load_initial_files(["~/ro.txt"])
    eb = editor.window_arrangement.active_editor_buffer
    eb.buffer.text = "updated"

    assert eb.write(force=True) is True
    assert path.read_text(encoding="utf-8") == "updated"
    assert path.stat().st_mode & 0o222 == 0  # original read-only mode restored
    assert editor.message is None


def test_206_tilde_path_reuses_absolute_buffer(editor, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    path = tmp_path / "foo.py"
    path.write_text("content", encoding="utf-8")

    editor.load_initial_files([str(path)])
    first = editor.window_arrangement.active_editor_buffer

    handle_command(editor, ":e ~/foo.py")

    buffers = editor.window_arrangement.editor_buffers
    assert len(buffers) == 1
    assert editor.window_arrangement.active_editor_buffer is first


def test_205_uppercase_register_appends_to_lowercase(editor):
    _editor_with_lines(editor, "one\ntwo")

    handle_command(editor, ":1y A")
    assert editor.application.vi_state.named_registers["a"].text == "one"

    handle_command(editor, ":2y A")
    assert editor.application.vi_state.named_registers["a"].text == "one\ntwo"
