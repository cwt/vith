from unittest.mock import MagicMock

from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document

from vith.key_bindings import _delete_cell_clusters


def test_delete_cell_clusters_no_crash_at_col0_or_empty_line():
    clipboard = MagicMock()
    app = MagicMock()
    app.clipboard = clipboard

    class Event:
        pass

    event = Event()
    event.app = app
    event.arg = 1

    # X at column 0: nothing to delete, must not raise UnboundLocalError.
    buff = Buffer(document=Document("abc", 0))
    event.current_buffer = buff
    _delete_cell_clusters(event, backward=True)
    assert buff.document.text == "abc"

    # x on an empty line: nothing to delete, must not raise.
    buff2 = Buffer(document=Document("", 0))
    event.current_buffer = buff2
    _delete_cell_clusters(event, backward=False)
    assert buff2.document.text == ""

    # When something is deleted, clipboard is updated.
    buff3 = Buffer(document=Document("abc", 1))
    event.current_buffer = buff3
    _delete_cell_clusters(event, backward=True)
    clipboard.set_text.assert_called()


def test_indent_line_uses_event_current_buffer(editor):
    # 039/101: indent_line must reference event.current_buffer and insert indentation
    from prompt_toolkit.buffer import Buffer
    from prompt_toolkit.document import Document
    from prompt_toolkit.key_binding.key_processor import KeyPressEvent

    from vith.key_bindings import create_key_bindings

    editor.tabstop = 4
    editor.expand_tab = True
    kb = create_key_bindings(editor)
    binding = [
        b for b in kb.bindings if b.keys == ("c-t",) and b.handler.__name__ == "indent_line"
    ][0]

    buf = Buffer(document=Document("hello", 5))
    event = MagicMock(spec=KeyPressEvent)
    event.current_buffer = buf
    event.app = editor.application

    binding.handler(event)
    assert buf.text == "    hello"


def test_end_of_line_cell_position_respects_count():
    # 063 regression: bare '$' with a count must move to the line `arg-1`
    # rows below (Vim counted '$'), clamped to the last line.
    from vith.key_bindings import _end_of_line_cell_position

    doc = Document("alpha\nbeta\ngamma\ndelta", 0)

    # Default count stays on the current line's last cell.
    pos = _end_of_line_cell_position(doc, 1)
    assert doc.translate_index_to_position(pos) == (0, len("alpha") - 1)

    # '3$' -> two lines down ("gamma").
    pos = _end_of_line_cell_position(doc, 3)
    assert doc.translate_index_to_position(pos) == (2, len("gamma") - 1)

    # Counts beyond the document clamp to the last line.
    pos = _end_of_line_cell_position(doc, 99)
    assert doc.translate_index_to_position(pos) == (3, len("delta") - 1)


def test_end_of_line_cell_position_thai_cluster():
    # The target column is the base of the last cell cluster, not the last
    # codepoint: "น้ำ" is clusters [น้][ำ], so the base sits at column 2.
    from vith.key_bindings import _end_of_line_cell_position

    doc = Document("น้ำ\nสหัส", 0)
    pos = _end_of_line_cell_position(doc, 1)
    assert doc.translate_index_to_position(pos) == (0, 2)

    # Counted '$' from row 0 onto "สหัส": clusters are [ส][หั][ส], so the
    # last cluster base is the final ส at column 3.
    pos = _end_of_line_cell_position(doc, 2)
    assert doc.translate_index_to_position(pos) == (1, 3)


def test_snap_word_end_to_cell_runs_within_word_line():
    # 064 regression: the backward cell walk when snapping the word end must
    # be applied to the word's own line, never walk across '\n'.
    from vith.key_bindings import _snap_word_end_to_cell

    doc = Document("one two\n\nthree four", 0)

    # Absolute column 0 of a later row snaps to 0 (no newline overshoot).
    pos = _snap_word_end_to_cell(doc, doc.translate_row_col_to_index(2, 0))
    assert doc.translate_index_to_position(pos) == (2, 0)

    # A word ending at the end of a line snaps to its final cluster base.
    pos = _snap_word_end_to_cell(doc, doc.translate_row_col_to_index(0, 7))
    assert doc.translate_index_to_position(pos) == (0, 6)


def test_snap_word_end_to_cell_thai_cluster():
    # "น้ำ" followed by a space: snapping from the space returns the base of
    # the [ำ] cluster (column 2), not the whole-document walk.
    from vith.key_bindings import _snap_word_end_to_cell

    doc = Document("น้ำ abc\n\nnext", 0)
    pos = _snap_word_end_to_cell(doc, doc.translate_row_col_to_index(0, 3))
    assert doc.translate_index_to_position(pos) == (0, 2)


def test_import_vith_main_does_not_start_editor(monkeypatch):
    # 068: importing the `__main__` submodule must not launch the editor;
    # `python -m vith` still runs because the module executes as `__main__`.
    # The guard in vith/__main__.py must keep `run()` import-order safe.
    import importlib

    import vith.entry_points.run_vith as run_vith

    started = []

    def fake_run():
        started.append(1)

    monkeypatch.setattr(run_vith, "run", fake_run)

    importlib.import_module("vith.__main__")

    assert started == []
    importlib.invalidate_caches()


def test_python_m_vith_help_smoke(capsys):
    # `python -m vith --help` exercises the actual guarded entrypoint.
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "vith", "--help"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
    assert result.stdout


def test_replace_single_cell_count_semantics():
    # 071: Vim `r` with a count replaces N characters with ONE char:
    # `2rX` on "abcdef" yields "Xcdef" with the cursor on the replacement,
    # not "XXcdef" as before.
    from prompt_toolkit.document import Document

    from vith.key_bindings import _replace_cell_clusters

    class Event:
        pass

    event = Event()
    event.app = MagicMock()
    event.arg = 2
    event.data = "X"
    buff = Buffer(document=Document("abcdef", 0))
    event.current_buffer = buff

    _replace_cell_clusters(event)

    assert buff.document.text == "Xcdef"
    assert buff.cursor_position == 0
    from prompt_toolkit.key_binding.vi_state import InputMode

    assert event.app.vi_state.input_mode == InputMode.NAVIGATION


def test_replace_single_cell_single_count():
    from prompt_toolkit.document import Document
    from prompt_toolkit.key_binding.vi_state import InputMode

    from vith.key_bindings import _replace_cell_clusters

    class Event:
        pass

    event = Event()
    event.app = MagicMock()
    event.arg = 1
    event.data = "x"
    buff = Buffer(document=Document("abcdef", 0))
    event.current_buffer = buff

    _replace_cell_clusters(event)

    assert buff.document.text == "xbcdef"
    assert event.app.vi_state.input_mode == InputMode.NAVIGATION


def test_name_under_cursor_selections():
    # 071: the file-explorer <Enter> opens the token under the cursor, not
    # the whole line.
    from prompt_toolkit.document import Document

    from vith.key_bindings import _name_under_cursor

    def name_at(line, col):
        return _name_under_cursor(Document(line, col))

    assert name_at("readme.txt notes dir_with space", 0) == "readme.txt"
    # Between two tokens -> the next token ("w"-style):
    assert name_at("readme.txt notes dir_with space", 13) == "notes"
    # Late in a token -> that token:
    assert name_at("readme.txt notes dir_with space", 9) == "readme.txt"
    # Cursor clamped to the line end with trailing spaces -> nearest token:
    assert name_at("readme.txt notes  ", 18) == "notes"
    assert name_at("", 0) == ""


def test_negative_count_delegate_documented_as_upstream_parity():
    # 071 row 7 (documented upstream-parity dead branch): the Thai
    # find_previous_word_ending(-count) delegate must behave exactly like
    # prompt_toolkit's own negative-count path on ASCII content.
    from prompt_toolkit.document import Document

    import vith.thai as thai
    from vith.thai import find_previous_word_ending

    orig_next = thai._ORIGINAL_DOCUMENT_METHODS["find_next_word_ending"]
    doc = Document("alpha beta gamma", 10)
    result = find_previous_word_ending(doc, count=-1, WORD=False)
    assert result == orig_next(doc, count=1, WORD=False)


def test_positive_count_clamps_non_positive_event_args():
    # 111 item 10: `_positive_count` clamps 0 or negative `event.arg` to 1
    # so cell-cluster delete/change/replace/toggle-case handlers never no-op.
    from prompt_toolkit.document import Document

    from vith.key_bindings import (
        _delete_cell_clusters,
        _positive_count,
        _replace_cell_clusters,
    )

    assert _positive_count(None) == 1
    assert _positive_count(0) == 1
    assert _positive_count(-5) == 1
    assert _positive_count(3) == 3

    class Event:
        pass

    event = Event()
    event.app = MagicMock()
    event.arg = 0
    event.data = "Z"
    buff = Buffer(document=Document("abcdef", 0))
    event.current_buffer = buff

    _replace_cell_clusters(event)
    assert buff.document.text == "Zbcdef"

    event.arg = -2
    _delete_cell_clusters(event, backward=False)
    assert buff.document.text == "bcdef"
