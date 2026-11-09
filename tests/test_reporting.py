from prompt_toolkit.document import Document

from vith.reporting import report_pyflakes


def test_report_pyflakes_utf8_column():
    doc = Document('x = "ที่ดิน" + undefined_var\n')
    err = report_pyflakes(doc)[0]
    # Character column should point at 'undefined_var', not a byte offset.
    assert doc.lines[0][err.start_column : err.end_column] == 'undefined_var'


def test_reporter_error_equality():
    from vith.reporting import ReporterError

    assert [ReporterError(0, 0, 1, 'u')] == [ReporterError(0, 0, 1, 'u')]


def test_run_reporter_clears_stale_errors_with_invalidation(editor, monkeypatch):
    # 069: the non-Python fast-exit must invalidate the app after clearing the
    # stale pyflakes index (rename scenario ':w newname.txt').
    from unittest.mock import MagicMock

    from vith import editor_buffer as eb_mod
    from vith.window_arrangement import EditorBuffer

    invalidates = []

    class FakeApp:
        def invalidate(self):
            invalidates.append(1)

    monkeypatch.setattr(eb_mod, "get_app", lambda: FakeApp())

    buffer_ = EditorBuffer(editor, "stale.py")
    buffer_.report_errors = [MagicMock()]
    buffer_.report_errors_by_line = {1: [MagicMock()]}

    # Rename to a non-Python location: fast-exit path (no running loop).
    buffer_.location = "stale.txt"
    buffer_.run_reporter()

    assert buffer_.report_errors == []
    assert buffer_.report_errors_by_line == {}
    assert invalidates == [1]


def test_run_reporter_silent_when_nothing_to_clear(editor, monkeypatch):
    # 069: no stale errors -> no spurious invalidation.
    from vith import editor_buffer as eb_mod
    from vith.window_arrangement import EditorBuffer

    invalidates = []

    class FakeApp:
        def invalidate(self):
            invalidates.append(1)

    monkeypatch.setattr(eb_mod, "get_app", lambda: FakeApp())

    buffer_ = EditorBuffer(editor, "stale.txt")
    buffer_.run_reporter()
    assert invalidates == []


def test_flakes_reporter_syntax_error_one_based_offset_to_zero_based_col():
    # 110 item 4: `_FlakesReporter.syntaxError` converts 1-based `SyntaxError.offset`
    # to 0-based `col` (clamped at 0 when `offset` is None or <= 0).
    from vith.reporting import _FlakesReporter

    reporter = _FlakesReporter()
    reporter.syntaxError("sample.py", "invalid syntax", 2, 5, "    x =")
    reporter.syntaxError("sample.py", "eof error", 3, None, "")
    reporter.syntaxError("sample.py", "zero offset", 4, 0, "")
    reporter.unexpectedError("sample.py", "unexpected")

    messages = reporter.messages
    assert messages[0].lineno == 2
    assert messages[0].col == 4
    assert messages[1].col == 0
    assert messages[2].col == 0
    assert messages[3].lineno == 1
    assert messages[3].col == 0


def test_byte_offset_to_char_col_exact_prefix_without_replacement_expansion():
    # 110 item 5: `_byte_offset_to_char_col` decodes without `errors='replace'`
    # so mid-character byte offsets never inflate the character column count.
    from vith.reporting import _byte_offset_to_char_col

    line = 'x = "ที่" + y'
    # 'x = "' is 5 ASCII bytes, 'ท' is 3 UTF-8 bytes (offsets 5..8).
    # Mid-character byte offset 6 or 7 should not inflate past 'ท'.
    assert _byte_offset_to_char_col(line, 5) == 5
    assert _byte_offset_to_char_col(line, 6) == 5
    assert _byte_offset_to_char_col(line, 8) == 6
    assert _byte_offset_to_char_col(line, 0) == 0


def test_report_pyflakes_clamps_line_end_to_document_length(monkeypatch):
    # 110 item 6: `line_end` is clamped to `len(document.text)` even if a
    # synthetic message points at the final line end.
    import pyflakes.api

    import vith.reporting as reporting_mod

    def fake_check(text, filename, reporter):
        reporter.syntaxError(filename, "bad syntax at end", 1, 100, text)

    monkeypatch.setattr(pyflakes.api, "check", fake_check)
    doc = Document("abc", 0)
    errors = reporting_mod.report_pyflakes(doc)
    assert len(errors) == 1
    assert errors[0].end_column <= len(doc.text)
