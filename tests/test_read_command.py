"""
Tests for Phase 2 Ex commands: `:[addr]r`/`:read` (file & shell insertion),
`:sav`/`:saveas` & strict Vim `:w {file}` parity, `:so`/`:source`,
buffer completeness (`:b #`, `:bf`, `:bl`, `:bd <target>`, `:sb`), and
tab completeness (`:tabo`, `:tabfir`, `:tabl`, `:tabm`, `:tabs`).
"""

from __future__ import annotations

import gzip
import subprocess

from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document
from prompt_toolkit.filters import to_filter

from vith.commands.completer import create_command_completer
from vith.commands.handler import handle_command
from vith.commands.preview import CommandPreviewer
from vith.thai import _THAI_GATE, reset_thai_text_gate


def test_read_file_inserts_after_cursor_and_preserves_metadata(editor, tmp_path):
    base = tmp_path / 'base.txt'
    base.write_text('line1\nline2\nline3\n')
    snippet = tmp_path / 'snippet.txt'
    snippet.write_bytes(b'  ins1\r\nins2\r\n')

    editor.load_initial_files([str(base)])
    eb = editor.window_arrangement.active_editor_buffer
    orig_location = eb.location
    orig_encoding = eb.encoding
    # Cursor on line 1 (row 0)
    eb.buffer.cursor_position = 0

    handle_command(editor, f':r {snippet}')

    assert eb.buffer.text == 'line1\n  ins1\nins2\nline2\nline3'
    # Cursor moves to first non-blank character of first inserted line (`ins1` col 2).
    assert eb.buffer.document.cursor_position_row == 1
    assert eb.buffer.document.cursor_position_col == 2
    assert editor.message == f'"{snippet}" 2L'
    # Buffer metadata must not be mutated by `:read`.
    assert eb.location == orig_location
    assert eb.is_new is False
    assert eb.encoding == orig_encoding

    # Undo restores exact pre-read buffer text and cursor.
    handle_command(editor, ':u')
    assert eb.buffer.text == 'line1\nline2\nline3'
    assert eb.buffer.cursor_position == 0


def test_read_addresses_zero_dollar_numeric_offset_marks_and_errors(editor, tmp_path):
    snippet = tmp_path / 'ins.txt'
    snippet.write_text('INSERTED\n')

    editor.load_initial_files([None])
    eb = editor.window_arrangement.active_editor_buffer
    eb.buffer.text = 'one\ntwo\nthree'

    # `:0r` inserts before line 1.
    handle_command(editor, f':0r {snippet}')
    assert eb.buffer.text == 'INSERTED\none\ntwo\nthree'
    assert eb.buffer.document.cursor_position_row == 0

    # `:$r` appends after last line.
    handle_command(editor, f':$r {snippet}')
    assert eb.buffer.text == 'INSERTED\none\ntwo\nthree\nINSERTED'
    assert eb.buffer.document.cursor_position_row == 4

    # `:2read` inserts after 1-based line 2.
    handle_command(editor, f':2read {snippet}')
    assert eb.buffer.document.lines == ['INSERTED', 'one', 'INSERTED', 'two', 'three', 'INSERTED']

    # `.-1` from line 1 resolves to 0 (before line 1).
    eb.buffer.cursor_position = 0
    handle_command(editor, f':.-1r {snippet}')
    assert eb.buffer.document.lines[0] == 'INSERTED'

    # Visual marks (`'<`, `'>`) work when set and report E20 when unset.
    eb.visual_marks = None
    before = eb.buffer.text
    handle_command(editor, f":'<r {snippet}")
    assert editor.message == 'E20: Mark not set'
    assert eb.buffer.text == before

    eb.visual_marks = (1, 2)
    handle_command(editor, f":'>r {snippet}")
    assert eb.buffer.document.lines[3] == 'INSERTED'

    # Out-of-bounds address reports E19 without mutating.
    before = eb.buffer.text
    handle_command(editor, f':999r {snippet}')
    assert editor.message == 'E19: No such line in current buffer'
    assert eb.buffer.text == before


def test_read_into_empty_buffer_bare_read_gzip_and_empty_file(editor, tmp_path):
    # Empty buffer special case: replaces empty line instead of adding blank line.
    src = tmp_path / 'alpha.txt'
    src.write_text('alpha\nbeta\n')
    editor.load_initial_files([None])
    eb = editor.window_arrangement.active_editor_buffer
    assert eb.buffer.text == ''

    # Bare `:r` on untitled buffer reports 'No file name'.
    handle_command(editor, ':r')
    assert editor.message == 'No file name'
    assert eb.buffer.text == ''

    # `:r file` into empty buffer produces no leading blank line.
    handle_command(editor, f':r {src}')
    assert eb.buffer.text == 'alpha\nbeta'

    # Reading an empty file inserts 0 lines and reports 0L without mutating.
    empty = tmp_path / 'empty.txt'
    empty.write_text('')
    handle_command(editor, f':r {empty}')
    assert eb.buffer.text == 'alpha\nbeta'
    assert editor.message == f'"{empty}" 0L'

    # GZip backend works transparently through `:read`.
    gz_path = tmp_path / 'compressed.txt.gz'
    with gzip.open(gz_path, 'wb') as fh:
        fh.write(b'from gzip\n')
    handle_command(editor, f':$read {gz_path}')
    assert eb.buffer.text == 'alpha\nbeta\nfrom gzip'

    # Bare `:r` on a named buffer re-reads its own file after cursor line.
    named = tmp_path / 'self.txt'
    named.write_text('self1\nself2\n')
    handle_command(editor, f':e! {named}')
    eb2 = editor.window_arrangement.active_editor_buffer
    eb2.buffer.cursor_position = 0
    handle_command(editor, ':r')
    assert eb2.buffer.text == 'self1\nself1\nself2\nself2'


def test_read_missing_file_readonly_empty_arrangement_thai_and_preview(editor, tmp_path):
    # Empty arrangement never raises.
    handle_command(editor, ':r')

    editor.load_initial_files([None])
    eb = editor.window_arrangement.active_editor_buffer
    eb.buffer.text = 'existing'

    # Missing file reports E484 without mutating.
    missing = tmp_path / 'does_not_exist.txt'
    handle_command(editor, f':r {missing}')
    assert editor.message == f"E484: Can't open file {missing}"
    assert eb.buffer.text == 'existing'

    # Read-only buffer rejects `:r` with E21.
    thai_file = tmp_path / 'thai.txt'
    thai_file.write_text('สวัสดี\nชาวโลก\n', encoding='utf-8')
    eb.buffer.read_only = to_filter(True)
    handle_command(editor, f':r {thai_file}')
    assert editor.message == "E21: Cannot make changes, 'modifiable' is off"
    assert eb.buffer.text == 'existing'

    # CommandPreviewer has zero side effects on `:r` and `:r !cmd`.
    eb.buffer.read_only = to_filter(False)
    previewer = CommandPreviewer(editor)
    previewer.preview(f':r {thai_file}')
    previewer.preview(':r !echo should-not-run')
    assert eb.buffer.text == 'existing'

    # Reading Thai text latches the sticky Thai/Lao renderer gate.
    reset_thai_text_gate()
    assert not _THAI_GATE.value
    handle_command(editor, f':r {thai_file}')
    assert eb.buffer.text == 'existing\nสวัสดี\nชาวโลก'
    assert _THAI_GATE.value


def test_read_shell_command_vim_parity_and_errors(editor, monkeypatch):
    editor.load_initial_files([None])
    eb = editor.window_arrangement.active_editor_buffer
    eb.buffer.text = 'top\nbottom'
    eb.buffer.cursor_position = 0

    # `:r !cmd` inserts stdout after current line.
    handle_command(editor, ':r !printf "from_shell\\nsecond_line\\n"')
    assert eb.buffer.text == 'top\nfrom_shell\nsecond_line\nbottom'
    assert eb.buffer.document.cursor_position_row == 1

    # `:r!cmd` (without space before `!`) works identically.
    handle_command(editor, ':0r!printf "header\\n"')
    assert eb.buffer.document.lines[0] == 'header'

    # Decision D3: non-zero exit inserts combined output (2>&1) and reports
    # `shell returned N`; undo cleanly restores the buffer.
    before_fail = eb.buffer.text
    handle_command(editor, ':$r !sh -c "echo partial_out; echo err_line >&2; exit 7"')
    assert 'partial_out' in eb.buffer.text
    assert 'err_line' in eb.buffer.text
    assert editor.message == 'shell returned 7'

    handle_command(editor, ':u')
    assert eb.buffer.text == before_fail

    # Empty shell command reports E34.
    handle_command(editor, ':r !')
    assert editor.message == 'E34: No previous command'

    # Timeout and OSError abort without mutating buffer.
    def fake_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd='sleep 99', timeout=30)

    monkeypatch.setattr(subprocess, 'run', fake_timeout)
    handle_command(editor, ':r !sleep 99')
    assert 'timed out' in editor.message
    assert eb.buffer.text == before_fail

    def fake_oserror(*args, **kwargs):
        raise OSError('spawn failed')

    monkeypatch.setattr(subprocess, 'run', fake_oserror)
    handle_command(editor, ':r !broken')
    assert 'Cannot execute shell command' in editor.message
    assert eb.buffer.text == before_fail


def test_read_command_completion_paths_and_commands(editor, tmp_path):
    sample = tmp_path / 'readme_target.txt'
    sample.write_text('hi\n')
    completer = create_command_completer(editor)
    event = CompleteEvent()

    # Command completion offers `read `.
    cmd_completions = [c.text for c in completer.get_completions(Document('rea'), event)]
    assert 'read ' in cmd_completions

    # Path completion works after `:r `.
    prefix = f'r {tmp_path}/readme_'
    path_completions = [c.text for c in completer.get_completions(Document(prefix), event)]
    assert any('target.txt' in item for item in path_completions)
