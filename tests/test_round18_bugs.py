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


def test_214_reject_nonpositive_colorcolumn(editor):
    _editor_with_lines(editor, "a")
    original = list(editor.colorcolumn)

    for command in (":set cc=0", ":set cc=-5"):
        editor.message = None
        handle_command(editor, command)
        assert editor.message is not None
        assert editor.colorcolumn == original


def test_214_reject_huge_integer_option(editor):
    editor.load_initial_files([None])
    original = editor.tabstop

    handle_command(editor, ":set ts=100000")

    assert editor.message is not None
    assert editor.tabstop == original


def test_214_preview_ignores_out_of_range_values(editor):
    editor.load_initial_files([None])
    original_cc = list(editor.colorcolumn)
    original_ts = editor.tabstop

    editor.previewer.preview("set cc=0")
    editor.previewer.preview("set ts=100000")

    assert editor.colorcolumn == original_cc
    assert editor.tabstop == original_ts


def test_213_setlocal_fileformat_leaves_global_unchanged(editor):
    eb = _editor_with_lines(editor, "a\nb")
    editor.fileformat = "unix"

    handle_command(editor, ":setlocal ff=dos")

    assert eb.fileformat == "dos"
    assert editor.fileformat == "unix"


def test_213_set_ff_bang_reports_invalid_argument(editor):
    _editor_with_lines(editor, "a\nb")

    handle_command(editor, ":set ff!")

    assert editor.message is not None
    assert "E474" in editor.message


def test_213_multi_query_includes_fileformat(editor):
    _editor_with_lines(editor, "a\nb")

    handle_command(editor, ":set ts ff")

    assert "tabstop=" in (editor.message or "")
    assert "fileformat=" in (editor.message or "")


def test_212_chained_tilde_replacement_uses_expanded_text(editor):
    eb = _editor_with_lines(editor, "a b c")

    handle_command(editor, ":s/a/X/")
    handle_command(editor, ":s/b/~/")
    handle_command(editor, ":s/c/~/")

    assert eb.buffer.text == "X X X"
    assert editor.last_substitute_text == "X"


def test_212_non_slash_delimiter_rejects_stray_slash(editor):
    eb = _editor_with_lines(editor, "a b")

    handle_command(editor, ":s#a#b#/")

    assert editor.message is not None
    assert "E488" in editor.message
    assert eb.buffer.text == "a b"


def test_211_unicode_superscript_digit_preview_does_not_crash(editor):
    editor.load_initial_files([None])
    original = editor.tabstop

    editor.previewer.preview("set ts=\u00b2")

    assert editor.tabstop == original


def test_211_unicode_superscript_digit_tabmove_reports_error(editor):
    editor.load_initial_files([None])

    for command in (":tabm \u00b2", ":tabm +\u00b2"):
        editor.message = None
        handle_command(editor, command)
        assert editor.message is not None
        assert "E474" in editor.message


def test_210_scalar_tabstop_does_not_shadow_filetype_table(editor):
    from vith.rc_file import apply_toml_config

    errors = []
    ok = apply_toml_config(
        editor,
        {"tabstop": 4, "filetype_tabstops": {"python": 2}},
        error_handler=errors.append,
    )

    assert ok is True
    assert editor.tabstop == 4
    assert editor.filetype_tabstops["python"] == 2
    assert errors == []


def test_210_invalid_filetype_tabstop_widths_reported(editor):
    from vith.rc_file import apply_toml_config

    errors = []
    ok = apply_toml_config(
        editor,
        {"tabstop": {"python": True, "rust": -5, "go": "bad"}},
        error_handler=errors.append,
    )

    assert ok is False
    assert len(errors) == 3
    assert editor.filetype_tabstops["python"] == 4  # default preserved


def test_210_editor_alias_beats_top_level_option(editor):
    from vith.rc_file import apply_toml_config

    apply_toml_config(editor, {"tabstop": 8, "editor": {"ts": 2}})

    assert editor.tabstop == 2


def test_209_bench_thai_plan_smoke():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "tools" / "bench_plans.py"
    spec = importlib.util.spec_from_file_location("bench_plans", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    module.bench_thai_plan()


def test_209_word_regex_excludes_unassigned_U0EC7():
    from vith.completion import DocumentWordsCompleter

    pattern = DocumentWordsCompleter._WORD_RE
    assert pattern.search("\u0ec8") is not None
    assert pattern.search("\u0ec7") is None


def test_208_redos_guard_catches_nested_and_counted_repeats(editor):
    _editor_with_lines(editor, "aaa\nbbb")
    for pattern in ("((a)+)+$", "(?:(a+))+$", "(a+){5,}$"):
        editor.message = None
        handle_command(editor, ":s/%s/X/" % pattern)
        assert "Unsafe pattern" in editor.message, pattern


def test_208_leading_bracket_class_is_not_a_false_positive(editor):
    from vith.commands.substitution import is_unsafe_regex

    assert is_unsafe_regex(editor, "[]+(a+)+]") is False
    assert editor.message is None


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
