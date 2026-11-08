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


def test_indent_line_uses_event_current_buffer():
    # Smoke: indent_line must reference event.current_buffer, not event.application.
    import inspect

    from vith.key_bindings import create_key_bindings

    src = inspect.getsource(create_key_bindings)
    assert "event.application.current_buffer" not in src


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
