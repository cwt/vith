"""Regression tests for the Round 18 findings (docs/bugs/204.md-229.md)."""

from unittest.mock import MagicMock

import pytest
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


def test_229_no_lambdas_in_shipped_code():
    import re
    from pathlib import Path

    import vith

    source_root = Path(vith.__file__).resolve().parent
    offenders = [
        str(path.relative_to(source_root))
        for path in source_root.rglob("*.py")
        if re.search(r"\blambda\b", path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_227_modeline_sniffing_is_bounded_and_finds_tail_modeline():
    from vith.lexer import sniff_filetype_from_text

    class NoSplitStr(str):
        def splitlines(self):  # pragma: no cover - must never be called
            raise AssertionError("whole-buffer splitlines called")

    text = NoSplitStr(("plain text line\n" * 5000) + "# vim: set ft=python:\n")
    assert sniff_filetype_from_text(text) == "python"


def test_227_colorscheme_uses_cached_styles(editor, monkeypatch):
    import vith.editor as editor_module

    def unexpected(*_args, **_kwargs):
        raise AssertionError("bypassed cached styles")

    monkeypatch.setattr(editor_module, "get_editor_style_by_name", unexpected)
    assert editor.use_colorscheme("monokai") is True


def test_227_render_json_parses_once(editor, monkeypatch):
    import vith.render as render_module

    real_loads = render_module.json.loads
    calls = []

    def counting_loads(*args, **kwargs):
        calls.append(args)
        return real_loads(*args, **kwargs)

    monkeypatch.setattr(render_module.json, "loads", counting_loads)
    render_module.render_document('{"answer": 42}', filetype="json")

    assert len(calls) == 1


def test_227_ex_edit_builds_one_document(editor, monkeypatch):
    import vith.commands.ranges as ranges_module

    real_document = ranges_module.Document
    calls = []

    class CountingDocument(real_document):
        def __new__(cls, *args, **kwargs):
            calls.append(1)
            return real_document(*args, **kwargs)

    monkeypatch.setattr(ranges_module, "Document", CountingDocument)
    eb = _editor_with_lines(editor, "one\ntwo")

    handle_command(editor, ":1d")

    assert len(calls) == 1
    assert eb.buffer.document.text == "two"


def test_225_buffer_list_aligns_by_display_width(editor, tmp_path):
    from prompt_toolkit.utils import get_cwidth

    from vith.layout import BufferListOverlay

    paths = []
    for name in ("ก้้้.txt", "abcd.txt", "中文.txt"):
        path = tmp_path / name
        path.write_text("x", encoding="utf-8")
        paths.append(str(path))
    editor.load_initial_files(paths)

    overlay = BufferListOverlay(editor)
    tokens = overlay.content.content.text()
    text = "".join(fragment for _style, fragment in tokens)
    rows = [line for line in text.splitlines() if "line " in line]

    assert len(rows) == 3
    line_columns = {get_cwidth(row[: row.index("line ")]) for row in rows}
    assert len(line_columns) == 1


def test_224_substitute_is_completed(editor):
    from prompt_toolkit.completion import CompleteEvent
    from prompt_toolkit.document import Document

    from vith.commands.completer import create_command_completer

    completer = create_command_completer(editor)
    completions = [
        completion.text.strip()
        for completion in completer.get_completions(Document("sub", 3), CompleteEvent())
    ]

    assert "substitute" in completions


def test_223_vsaw_is_instance_local(editor, tmp_path):
    from prompt_toolkit.input import DummyInput
    from prompt_toolkit.output import DummyOutput

    from vith.editor import Editor

    second = Editor(config_directory=str(tmp_path), input=DummyInput(), output=DummyOutput())
    editor.variation_selector_always_wide = True

    assert editor.variation_selector_always_wide is True
    assert second.variation_selector_always_wide is False
    third = Editor(config_directory=str(tmp_path), input=DummyInput(), output=DummyOutput())
    assert third._default_options["variation_selector_always_wide"] is False


def test_223_vsaw_enabled_uses_active_application(editor, tmp_path):
    from prompt_toolkit.application.current import set_app
    from prompt_toolkit.input import DummyInput
    from prompt_toolkit.output import DummyOutput

    from vith.editor import Editor
    from vith.thai.clusters import _vsaw_enabled

    second = Editor(config_directory=str(tmp_path), input=DummyInput(), output=DummyOutput())
    editor.variation_selector_always_wide = True

    with set_app(editor.application):
        assert _vsaw_enabled() is True
    with set_app(second.application):
        assert _vsaw_enabled() is False


def test_222_repeated_patch_install_is_quiet(caplog):
    import logging

    from vith.thai import install_prompt_toolkit_thai_patches

    with caplog.at_level(logging.WARNING, logger="vith.thai.patches"):
        caplog.clear()
        assert install_prompt_toolkit_thai_patches() is True
        assert install_prompt_toolkit_thai_patches() is True

    assert "VS16 merge fix: target snippet not found" not in caplog.text


def test_221_hook_lookup_skips_directory_collision(tmp_path, monkeypatch):
    from vith.rc_file import _resolve_hook_path

    (tmp_path / "myhook").mkdir()  # colliding directory in CWD
    hooks = tmp_path / "custom_hooks"
    hooks.mkdir()
    expected = hooks / "myhook.py"
    expected.write_text("def configure(e): pass\n", encoding="utf-8")
    config = tmp_path / "config.toml"
    config.write_text("[hooks]\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert _resolve_hook_path("myhook", str(config), str(hooks)) == str(expected)


def test_221_custom_hooks_dir_without_config_path(editor, tmp_path):
    from vith.rc_file import apply_toml_config

    hooks = tmp_path / "hooks"
    hooks.mkdir()
    (hooks / "other.py").write_text("def configure(e):\n    e.hook_ran = True\n", encoding="utf-8")

    assert apply_toml_config(
        editor,
        {"hooks": {"directory": str(hooks), "enabled": ["other.py"]}},
        config_path=None,
    )
    assert getattr(editor, "hook_ran", False) is True


def test_220_ex_delete_clamps_col_on_whitespace_line(editor):
    eb = _editor_with_lines(editor, "hello\n    \nworld")

    handle_command(editor, ":1d")

    assert eb.buffer.document.cursor_position_row == 0
    assert eb.buffer.document.cursor_position_col == 3


def test_220_line_jump_clamps_col_on_whitespace_line(editor):
    eb = _editor_with_lines(editor, "hello\n    \nworld")

    handle_command(editor, ":2")

    assert eb.buffer.document.cursor_position_row == 1
    assert eb.buffer.document.cursor_position_col == 3


def test_220_ex_cursor_snaps_off_combining_mark(editor):
    eb = _editor_with_lines(editor, "x\nก้\nz")

    handle_command(editor, ":1d")

    assert eb.buffer.document.cursor_position_row == 0
    assert eb.buffer.document.cursor_position_col == 0


def test_219_untitled_sniffed_python_is_highlighted(editor):
    from prompt_toolkit.document import Document

    from vith.lexer import DocumentLexer

    editor.load_initial_files([None])
    eb = editor.window_arrangement.active_editor_buffer
    eb.buffer.text = "#!/usr/bin/env python3\ndef foo():\n    return 42\n"
    assert eb.filetype == "python"

    get_line = DocumentLexer(eb).lex_document(Document(eb.buffer.text, 0))
    assert any("keyword" in style.lower() for style, _ in get_line(1))


def test_219_empty_extensionless_filetype_is_not_stuck(editor, tmp_path):
    path = tmp_path / "myscript"
    path.write_text("", encoding="utf-8")
    editor.load_initial_files([str(path)])
    eb = editor.window_arrangement.active_editor_buffer

    assert eb.filetype == ""
    eb.buffer.text = "#!/usr/bin/env python3\ndef foo():\n    pass\n"
    assert eb.filetype == "python"


def test_219_python_gate_honors_filetype():
    from vith.reporting import checks_python_file

    assert checks_python_file("foo.py", "text") is False
    assert checks_python_file("myscript", "python") is True
    assert checks_python_file("foo.py") is True


def test_218_short_display_name_strips_trailing_separators():
    from vith.editor_buffer import short_display_name

    assert short_display_name("subdir/") == "subdir"
    assert short_display_name("/path/to/subdir/") == "subdir"
    assert short_display_name("/") == "/"


def test_218_trailing_slash_title_and_tab_caption(editor, tmp_path):
    directory = tmp_path / "subdir"
    directory.mkdir()

    editor.load_initial_files([str(directory) + "/"])
    eb = editor.window_arrangement.active_editor_buffer

    assert eb.get_display_name(short=True) == "subdir"
    assert editor.get_title() == "vith - subdir"


def test_217_spaced_path_arguments_parse():
    from vith.commands.grammar import COMMAND_GRAMMAR

    path = "/tmp/my file.py"
    for line in (":e " + path, ":w " + path, ":cd My Documents", ":b " + path):
        match = COMMAND_GRAMMAR.match(line)
        assert match is not None, line
        variables = match.variables()
        assert any(
            variables.get(key) in (path, "My Documents") for key in ("location", "buffer_name")
        ), (line, variables)


def test_217_spaced_buffer_name_reuses_buffer(editor, tmp_path):
    path = tmp_path / "my file.py"
    path.write_text("content", encoding="utf-8")
    editor.load_initial_files([str(path)])
    first = editor.window_arrangement.active_editor_buffer

    handle_command(editor, ":b " + str(path))

    assert editor.window_arrangement.active_editor_buffer is first


def test_217_punctuation_args_report_E488(editor):
    _editor_with_lines(editor, "a\nb")

    for command in (":d @", ":j -1"):
        editor.message = None
        handle_command(editor, command)
        assert editor.message is not None
        assert "E488" in editor.message


def test_216_reload_clears_automatic_read_only(editor, tmp_path):
    path = tmp_path / "secret.txt"
    path.write_text("top secret\n", encoding="utf-8")
    path.chmod(0o000)
    editor.load_initial_files([str(path)])
    eb = editor.window_arrangement.active_editor_buffer
    assert eb.buffer.read_only()

    path.chmod(0o644)
    eb.reload()

    assert eb.buffer.text == "top secret"
    assert not eb.buffer.read_only()


def test_216_reload_keeps_explicit_read_only(editor, tmp_path):
    path = tmp_path / "note.txt"
    path.write_text("note\n", encoding="utf-8")
    editor.load_initial_files([str(path)])
    eb = editor.window_arrangement.active_editor_buffer
    handle_command(editor, ":set ro")

    eb.reload()

    assert eb.buffer.read_only()


def test_215_setdefault_never_inserts_none():
    from vith.editor import FiletypeTabstops

    ft = FiletypeTabstops({"python": 4})
    assert ft.setdefault("py") == 4
    assert len(ft) == 1

    with pytest.raises(ValueError):
        ft.setdefault("missing")
    assert "missing" not in ft


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
