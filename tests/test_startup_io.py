"""
Startup and I/O robustness tests (docs/bugs/070.md): nested config paths,
`-p/-o/-O` mutual exclusion, latin-1→utf-8 save fallback, and unknown-location
indistinguishability.
"""

import subprocess
import sys

from prompt_toolkit.input import DummyInput
from prompt_toolkit.output import DummyOutput

from vith.editor import Editor
from vith.editor_buffer import EditorBuffer


def test_editor_makes_nested_config_directory(tmp_path):
    # 070 row 1: nested config paths are created (was bare os.mkdir).
    nested = tmp_path / "a" / "b" / "c"
    Editor(config_directory=str(nested), output=DummyOutput(), input=DummyInput())
    assert nested.is_dir()


def test_vith_flags_mutual_exclusion_error():
    # 070 row 2: `vith -p -o` prints a friendly error (exit 2) instead of
    # hitting the bare AssertionError inside load_initial_files.
    result = subprocess.run(
        [sys.executable, "-m", "vith", "-p", "-o", "one.txt", "two.txt"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 2
    assert "mutually exclusive" in result.stderr


def test_latin1_decoded_file_with_thai_saves_as_utf8(editor, tmp_path):
    # 070 row 3: typing Thai into a latin-1-decoded buffer must save as
    # utf-8 instead of failing the write.
    path = tmp_path / "latin1 file.txt"
    path.write_bytes("café\n".encode("latin-1"))

    buffer_ = EditorBuffer(editor, str(path))
    assert buffer_.encoding == "latin-1"

    buffer_.buffer.text = "café + ที่ดิน"
    assert buffer_.write() is True
    assert buffer_.encoding == "utf-8"
    assert path.read_bytes().decode("utf-8") == "café + ที่ดิน\n"


def test_unknown_scheme_marks_buffer_read_only(editor):
    # 070 row 4: an unknown location scheme shows a message and creates a
    # read-only buffer — it is not indistinguishable from a new file.
    buffer_ = EditorBuffer(editor, "xyz://f")
    assert editor.message  # Cannot read: <location>
    assert buffer_.buffer.read_only() is True
    assert buffer_.buffer.text == ""


def test_missing_file_is_still_a_writable_new_file(editor, tmp_path):
    # 070 row 4 (contrast case): a missing local file stays writable.
    buffer_ = EditorBuffer(editor, str(tmp_path / "not-there.txt"))
    assert buffer_.buffer.read_only() is False
    assert buffer_.is_new is True


def test_reload_keeps_contents_when_location_becomes_unreadable(editor, tmp_path):
    # 070 row 4: reload() must not wipe the buffer when the read fails.
    ed = editor
    good = tmp_path / "precious.txt"
    good.write_text("precious\n")
    buffer_ = EditorBuffer(ed, str(good))

    # Simulate the backend currently handling the location disappearing:
    ed.io_backends = [b for b in ed.io_backends if type(b).__name__ != "FileIO"]
    ed.message = None
    buffer_.location = "xyz://f"

    buffer_.reload()  # Must not raise; must not wipe.

    assert buffer_.buffer.text == "precious"
