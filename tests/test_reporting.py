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
