from vith.process import set_process_name
from vith.window_arrangement import EditorBuffer


def test_editor_title_default(editor):
    assert editor.get_title() == "vith"


def test_editor_title_with_file(editor):
    eb = EditorBuffer(editor)
    eb.location = "/path/to/my_script.py"
    editor.window_arrangement._add_editor_buffer(eb)

    assert editor.get_title() == "vith - my_script.py"


def test_editor_title_update_and_clear(editor, monkeypatch):
    recorded = []

    class MockOutput:
        def set_title(self, text):
            recorded.append(("set_title", text))

        def clear_title(self):
            recorded.append(("clear_title", ""))

        def write_raw(self, seq):
            recorded.append(("write_raw", seq))

        def flush(self):
            recorded.append(("flush", ""))

    editor.application.output = MockOutput()

    # Without tmux/screen:
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.delenv("STY", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")

    editor.update_title()
    assert ("set_title", "vith") in recorded
    assert ("write_raw", "\x1b]0;vith\x07") in recorded

    recorded.clear()
    editor._clear_terminal_title()
    assert ("clear_title", "") in recorded
    assert ("write_raw", "\x1b]0;\x07") in recorded


def test_editor_title_with_tmux(editor, monkeypatch):
    recorded = []

    class MockOutput:
        def set_title(self, text):
            recorded.append(("set_title", text))

        def clear_title(self):
            recorded.append(("clear_title", ""))

        def write_raw(self, seq):
            recorded.append(("write_raw", seq))

        def flush(self):
            recorded.append(("flush", ""))

    editor.application.output = MockOutput()
    monkeypatch.setenv("TMUX", "1")

    editor.update_title()
    assert ("write_raw", "\x1b]0;vith\x07\x1bkvith\x1b\\") in recorded

    recorded.clear()
    editor._clear_terminal_title()
    assert ("write_raw", "\x1b]0;\x07\x1bk\x1b\\") in recorded


def test_set_process_name_safe():
    # Calling on macOS or Linux should never raise an exception.
    set_process_name("vith")
