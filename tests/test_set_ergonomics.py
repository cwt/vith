"""
Tests for Phase 4 command-line ergonomics:
- `%`/`#` special filename expansion + chainable modifiers (`:p`, `:h`, `:t`, `:r`, `:e`)
- `:set` syntax upgrades (bare `:set`, `:set all`, `?`, `!`, `inv`, multi-arg)
- New `:set` options (`smartcase`, `shiftwidth`, `readonly`, `autoindent`, `fileencoding`)
- Inspection commands (`:reg`/`:registers`, `:marks`) and command completion.
"""

from __future__ import annotations

import os
import shlex
import sys
from unittest.mock import MagicMock

from vith.commands.handler import expand_command_filenames, handle_command


def _open_files(editor, paths):
    editor.load_initial_files(paths)
    editor.sync_with_prompt_toolkit()


def test_expand_percent_and_modifiers(editor, tmp_path):
    sub = tmp_path / 'pkg'
    sub.mkdir()
    src = sub / 'archive.tar.gz'
    src.write_text('hello\n', encoding='utf-8')

    _open_files(editor, [str(src)])

    expanded, err = expand_command_filenames('%', editor)
    assert err is None
    assert expanded == str(src)

    expanded_p, err = expand_command_filenames('%:p', editor)
    assert err is None
    assert expanded_p == os.path.abspath(str(src))

    expanded_h, err = expand_command_filenames('%:h', editor)
    assert err is None
    assert expanded_h == str(sub)

    expanded_t, err = expand_command_filenames('%:t', editor)
    assert err is None
    assert expanded_t == 'archive.tar.gz'

    expanded_r, err = expand_command_filenames('%:r', editor)
    assert err is None
    assert expanded_r == str(sub / 'archive.tar')

    expanded_rr, err = expand_command_filenames('%:r:r', editor)
    assert err is None
    assert expanded_rr == str(sub / 'archive')

    expanded_e, err = expand_command_filenames('%:e', editor)
    assert err is None
    assert expanded_e == 'gz'

    expanded_re, err = expand_command_filenames('%:r:e', editor)
    assert err is None
    assert expanded_re == 'tar'

    expanded_chain, err = expand_command_filenames('%:p:h/other.py', editor)
    assert err is None
    assert expanded_chain == f'{os.path.abspath(str(sub))}/other.py'

    expanded_tr, err = expand_command_filenames('%:t:r:r', editor)
    assert err is None
    assert expanded_tr == 'archive'


def test_expand_relative_head_and_escapes(editor, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'plain').write_text('x\n', encoding='utf-8')
    _open_files(editor, ['plain'])

    expanded_h, err = expand_command_filenames('%:h', editor)
    assert err is None
    assert expanded_h == '.'

    expanded_e, err = expand_command_filenames('%:e', editor)
    assert err is None
    assert expanded_e == ''

    # Escaped \% and \# pass through literally even when alternate buffer is unset.
    lit, err = expand_command_filenames(r'100\% \#1 %', editor)
    assert err is None
    assert lit == '100% #1 plain'


def test_expand_alternate_hash_and_errors(editor, tmp_path):
    # Untitled active buffer -> E499.
    _open_files(editor, [None])
    val, err = expand_command_filenames('%', editor)
    assert val is None
    assert err == "E499: Empty file name for '%'"

    # No alternate buffer -> E194.
    val, err = expand_command_filenames('#', editor)
    assert val is None
    assert err == "E194: No alternate file name to substitute for '#'"

    f1 = tmp_path / 'first.py'
    f2 = tmp_path / 'second.txt'
    f1.write_text('one\n', encoding='utf-8')
    f2.write_text('two\n', encoding='utf-8')

    handle_command(editor, f':e {f1}')
    handle_command(editor, f':e {f2}')
    assert editor.window_arrangement.active_editor_buffer.location == str(f2)

    alt_expanded, err = expand_command_filenames('#:t', editor)
    assert err is None
    assert alt_expanded == 'first.py'

    # `:e #` switches back to first.py via expanded location.
    handle_command(editor, ':e #')
    assert editor.window_arrangement.active_editor_buffer.location == str(f1)


def test_expand_shell_escape_and_command_integrations(editor, tmp_path):
    spaced = tmp_path / 'my file.txt'
    spaced.write_text('alpha\nbeta\n', encoding='utf-8')
    _open_files(editor, [str(spaced)])

    # Shell escape quotes filenames containing spaces.
    cmd_str, err = expand_command_filenames('cat %', editor, shell_escape=True)
    assert err is None
    assert cmd_str == f'cat {shlex.quote(str(spaced))}'

    # `:w %:r.bak` writes a copy to `<stem>.bak`.
    handle_command(editor, ':w %:r.bak')
    bak = tmp_path / 'my file.bak'
    assert bak.exists()
    assert bak.read_text(encoding='utf-8') == 'alpha\nbeta\n'

    # `:r !python3 -c ... %:t` expands `%` inside shell command with shell escaping.
    py = shlex.quote(sys.executable)
    handle_command(editor, f':$r !{py} -c "import sys; print(sys.argv[1])" %:t')
    assert editor.window_arrangement.active_editor_buffer.buffer.document.lines[-1] == 'my file.txt'

    # `:!echo %` invokes `run_system_command` with expanded, shell-escaped path.
    mock_run = MagicMock()
    editor.application.run_system_command = mock_run
    handle_command(editor, ':!echo %')
    mock_run.assert_called_once_with(f'echo {shlex.quote(str(spaced))}')

    # `:%!python3 ... %:t` expands inside filter shell command.
    handle_command(
        editor,
        f':1,1!{py} -c "import sys; print(sys.stdin.read().strip() + \':\' + sys.argv[1])" %:t',
    )
    assert (
        editor.window_arrangement.active_editor_buffer.buffer.document.lines[0]
        == 'alpha:my file.txt'
    )

    # `:w !python3 ... %:e` expands inside pipe-write command.
    out_file = tmp_path / 'pipe_out.txt'
    handle_command(
        editor,
        f':1,1w !{py} -c "import pathlib, sys; pathlib.Path({str(out_file)!r}).write_text(sys.argv[1])" %:e',
    )
    assert out_file.read_text(encoding='utf-8') == 'txt'

    # `:w! >> %:r.log` expands inside append target.
    handle_command(editor, ':1,1w! >> %:r.log')
    log_file = tmp_path / 'my file.log'
    assert log_file.read_text(encoding='utf-8') == 'alpha:my file.txt\n'


def test_buffer_hash_still_works_with_untitled_alternate(editor, tmp_path):
    f1 = tmp_path / 'named.py'
    f1.write_text('x = 1\n', encoding='utf-8')
    _open_files(editor, [None])
    untitled = editor.window_arrangement.active_editor_buffer
    untitled.buffer.text = 'draft note'
    handle_command(editor, f':e! {f1}')
    assert editor.window_arrangement.alternate_editor_buffer is untitled

    # `:e #` rejects untitled alternate with E194...
    handle_command(editor, ':e #')
    assert editor.message == "E194: No alternate file name to substitute for '#'"
    assert editor.window_arrangement.active_editor_buffer.location == str(f1)

    # ...while `:b #` still switches cleanly to the untitled alternate buffer.
    handle_command(editor, ':b #')
    assert editor.window_arrangement.active_editor_buffer is untitled


def test_expand_error_on_empty_editor_shows_message(editor):
    # Active buffer is None (no files loaded): `:r %` shows E499 cleanly.
    handle_command(editor, ':r %')
    assert editor.message == "E499: Empty file name for '%'"
