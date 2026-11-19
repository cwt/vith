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


def test_set_query_toggle_and_multi_argument(editor, tmp_path):
    from vith.commands.commands import set_options_lines

    f = tmp_path / 'sample.py'
    f.write_text('x = 1\n', encoding='utf-8')
    _open_files(editor, [str(f)])

    # Boolean query `?`:
    editor.show_line_numbers = True
    handle_command(editor, ':set nu?')
    assert editor.message == 'number'
    editor.show_line_numbers = False
    handle_command(editor, ':set number?')
    assert editor.message == 'nonumber'

    # Toggle via `!` and `inv`:
    handle_command(editor, ':set nu!')
    assert editor.show_line_numbers is True
    handle_command(editor, ':set invnu')
    assert editor.show_line_numbers is False
    handle_command(editor, ':set invnumber')
    assert editor.show_line_numbers is True

    # Toggle `hls!` clears hlsearch suppression when turning on:
    editor.highlight_search = False
    editor._hlsearch_suppressed = True
    handle_command(editor, ':set hls!')
    assert editor.highlight_search is True
    assert editor._hlsearch_suppressed is False

    # Toggle `tildeop` via `!` and `inv` and query via `?`:
    handle_command(editor, ':set top!')
    assert editor.application.vi_state.tilde_operator is True
    handle_command(editor, ':set top?')
    assert editor.message == 'tildeop'
    handle_command(editor, ':set invtildeop')
    assert editor.application.vi_state.tilde_operator is False
    handle_command(editor, ':set tildeop?')
    assert editor.message == 'notildeop'

    # Value queries (`?` and bare):
    handle_command(editor, ':set ts=4 cc=80,100')
    handle_command(editor, ':set ts?')
    assert editor.message == 'tabstop=4'
    handle_command(editor, ':set cc?')
    assert editor.message == 'colorcolumn=80,100'
    handle_command(editor, ':set cc')
    assert editor.message == 'colorcolumn=80,100'
    handle_command(editor, ':set cc=')
    assert editor.colorcolumn == []
    handle_command(editor, ':set ft?')
    assert editor.message == 'filetype=python'

    # Multi-argument `:set` applies left-to-right:
    handle_command(editor, ':set nonu rnu ts=2 noet so=5')
    assert editor.show_line_numbers is False
    assert editor.relative_number is True
    assert editor.tabstop == 2
    assert editor.expand_tab is False
    assert editor.scroll_offset == 5

    # Multiple queries combined:
    handle_command(editor, ':set nu? rnu? ts?')
    assert editor.message == 'nonumber  relativenumber  tabstop=2'

    # Error handling:
    handle_command(editor, ':set ts!')
    assert 'E474' in editor.message
    handle_command(editor, ':set nu=1')
    assert 'E474' in editor.message
    handle_command(editor, ':set bogus?')
    assert editor.message == 'Unknown option: bogus'
    handle_command(editor, ':set invbogus')
    assert editor.message == 'Unknown option: invbogus'
    handle_command(editor, ':set!')
    assert editor.message == 'No ! allowed'

    # Bare `:set` and `:set all` open inside vith as `[Options]` read-only splits:
    changed_lines = set_options_lines(editor, show_all=False)
    assert changed_lines[0] == '--- Options ---'
    assert 'nonumber' in changed_lines
    assert 'relativenumber' in changed_lines
    assert 'tabstop=2' in changed_lines

    all_lines = set_options_lines(editor, show_all=True)
    assert len(all_lines) > len(changed_lines)
    assert 'filetype=python' in all_lines

    handle_command(editor, ':set')
    info_eb = editor.window_arrangement.active_editor_buffer
    assert info_eb.is_scratch is True
    assert info_eb.scratch_title == '[Options]'
    assert 'nonumber' in info_eb.buffer.text
    handle_command(editor, ':q')

    handle_command(editor, ':set all')
    all_eb = editor.window_arrangement.active_editor_buffer
    assert all_eb.is_scratch is True
    assert all_eb.scratch_title == '[Options]'
    assert 'wrap' in all_eb.buffer.text
    handle_command(editor, ':q')


def test_bare_set_when_all_defaults(editor):
    from vith.commands.commands import set_options_lines

    _open_files(editor, [None])
    lines = set_options_lines(editor, show_all=False)
    assert lines == ['--- Options ---', '(all options at default values)']


def test_set_completion_multi_arg_all_and_inv(editor):
    from prompt_toolkit.completion import CompleteEvent
    from prompt_toolkit.document import Document

    from vith.commands.completer import create_command_completer

    completer = create_command_completer(editor)

    def complete(text):
        doc = Document(text, len(text))
        return [c.text for c in completer.get_completions(doc, CompleteEvent())]

    assert 'all' in complete('set a')
    assert 'number' in complete('set nu ')
    assert 'relativenumber' in complete('set nu r')
    assert 'invnumber' in complete('set invn')
    assert 'invnu' in complete('set invn')
    assert 'invtildeop' in complete('set invt')
    assert 'python' in complete('set nu ft=py')
    assert complete('set nu tabstop=') == []


def test_smartcase_option_in_search_substitute_and_global(editor, tmp_path):
    f = tmp_path / 'case.txt'
    f.write_text('foo\nFoo\nFOO\n', encoding='utf-8')
    _open_files(editor, [str(f)])
    eb = editor.window_arrangement.active_editor_buffer

    # Default: ignorecase=False, smartcase=False -> case-sensitive.
    assert editor.should_ignore_case('foo') is False
    handle_command(editor, ':set ic scs')
    assert editor.ignore_case is True
    assert editor.smartcase is True
    assert editor.should_ignore_case('foo') is True
    assert editor.should_ignore_case('Foo') is False

    # SearchToolbar ignore_case filter follows smartcase dynamically:
    search_state = editor.editor_layout.search_control.searcher_search_state
    editor.search_buffer.text = 'foo'
    assert search_state.ignore_case() is True
    editor.search_buffer.text = 'Foo'
    assert search_state.ignore_case() is False

    # `:s` honors smartcase (uppercase pattern is case-sensitive, lowercase folds case):
    handle_command(editor, ':%s/Foo/Bar/g')
    assert eb.buffer.document.lines == ['foo', 'Bar', 'FOO']
    handle_command(editor, ':%s/foo/baz/g')
    assert eb.buffer.document.lines == ['baz', 'Bar', 'baz']

    # Explicit `/i` overrides smartcase even on uppercase pattern:
    handle_command(editor, ':%s/BAZ/qux/gi')
    assert eb.buffer.document.lines == ['qux', 'Bar', 'qux']

    # `:g` honors smartcase too:
    handle_command(editor, ':g/Bar/d')
    assert eb.buffer.document.lines == ['qux', 'qux']

    handle_command(editor, ':set noscs')
    assert editor.smartcase is False
    handle_command(editor, ':set scs?')
    assert editor.message == 'nosmartcase'


def test_shiftwidth_option_and_indent_bindings(editor, tmp_path):
    from vith.commands.preview import CommandPreviewer
    from vith.key_bindings import _get_buffer_shiftwidth, _shift_lines

    f = tmp_path / 'indent.txt'
    f.write_text('alpha\nbeta\n', encoding='utf-8')
    _open_files(editor, [str(f)])
    eb = editor.window_arrangement.active_editor_buffer

    # Default shiftwidth=0 follows buffer tabstop (8 for .txt):
    assert editor.shiftwidth == 0
    assert _get_buffer_shiftwidth(editor, eb.buffer) == 8

    # Setting shiftwidth=2 decouples >> / << / <C-t> from tabstop=8:
    handle_command(editor, ':set ts=8 sw=2 et')
    assert editor.shiftwidth == 2
    assert eb.shiftwidth == 2
    assert _get_buffer_shiftwidth(editor, eb.buffer) == 2

    _shift_lines(editor, eb.buffer, 1, indent=True)
    assert eb.buffer.document.lines[0] == '  alpha'
    _shift_lines(editor, eb.buffer, 1, indent=False)
    assert eb.buffer.document.lines[0] == 'alpha'

    # `<C-t>` in Insert mode indents by shiftwidth (2), not tabstop (8):
    handle_command(editor, ':1norm! i\x14')
    assert eb.buffer.document.lines[0] == '  alpha'

    # `:setlocal sw=3` overrides only current buffer; `sw=0` falls back to tabstop:
    handle_command(editor, ':setlocal sw=3')
    assert eb.shiftwidth == 3
    assert editor.shiftwidth == 2
    handle_command(editor, ':setlocal sw?')
    assert editor.message == 'shiftwidth=3'

    handle_command(editor, ':set sw=0')
    assert eb.shiftwidth == 8

    # Validation rejects negative values:
    handle_command(editor, ':set sw=-1')
    assert editor.message == 'Argument must be positive'

    # Live preview restores both global and buffer-local shiftwidth:
    previewer = CommandPreviewer(editor)
    previewer.preview(':set sw=6')
    assert editor.shiftwidth == 6
    assert eb.shiftwidth == 6
    previewer.restore()
    assert editor.shiftwidth == 0
    assert eb.shiftwidth == 8


def test_readonly_option_toggles_and_write_guard(editor, tmp_path):
    from vith.commands.commands import set_options_lines
    from vith.commands.preview import CommandPreviewer

    f = tmp_path / 'locked.txt'
    f.write_text('initial\n', encoding='utf-8')
    _open_files(editor, [str(f)])
    eb = editor.window_arrangement.active_editor_buffer

    # Default is writable:
    handle_command(editor, ':set ro?')
    assert editor.message == 'noreadonly'

    # `:set ro` locks edits and blocks `:w` without `!`:
    handle_command(editor, ':set ro')
    assert eb.buffer.read_only() is True
    handle_command(editor, ':set ro?')
    assert editor.message == 'readonly'
    assert 'readonly' in set_options_lines(editor, show_all=False)

    handle_command(editor, ':w')
    assert editor.message == "E45: 'readonly' option is set (add ! to override)"

    # `:w!` overrides E45 and writes:
    assert eb.write(force=True) is True

    # `:set ro!` / `:set invro` toggle read-only:
    handle_command(editor, ':set ro!')
    assert eb.buffer.read_only() is False
    handle_command(editor, ':set invro')
    assert eb.buffer.read_only() is True
    handle_command(editor, ':set noro')
    assert eb.buffer.read_only() is False

    # Previewing `:set ro` has zero side effects:
    previewer = CommandPreviewer(editor)
    previewer.preview(':set ro')
    assert eb.buffer.read_only() is False


def test_autoindent_option_enter_and_open_lines(editor, tmp_path):
    from vith.commands.preview import CommandPreviewer

    f = tmp_path / 'code.py'
    f.write_text('    def greet():\n', encoding='utf-8')
    _open_files(editor, [str(f)])
    eb = editor.window_arrangement.active_editor_buffer

    # Default autoindent=True copies leading whitespace on `o`, `O`, and Enter:
    assert editor.autoindent is True
    handle_command(editor, ':1norm! opass')
    assert eb.buffer.document.lines == ['    def greet():', '    pass']

    handle_command(editor, ':1norm! O# header')
    assert eb.buffer.document.lines[0] == '    # header'

    # Turning off autoindent (`:set noai`) suppresses indent copying on `o`, `O`, and Enter:
    handle_command(editor, ':set noai')
    assert editor.autoindent is False
    handle_command(editor, ':set ai?')
    assert editor.message == 'noautoindent'

    handle_command(editor, ':2norm! oreturn')
    assert eb.buffer.document.lines[2] == 'return'

    handle_command(editor, ':2norm! O# top')
    assert eb.buffer.document.lines[1] == '# top'

    # Insert-mode Enter honors autoindent toggle:
    eb.buffer.text = '    hello'
    handle_command(editor, ':set ai')
    handle_command(editor, ':1norm! A\rworld')
    assert eb.buffer.document.lines == ['    hello', '    world']

    eb.buffer.text = '    hello'
    handle_command(editor, ':set noai')
    handle_command(editor, ':1norm! A\rworld')
    assert eb.buffer.document.lines == ['    hello', 'world']

    # `paste_mode` also suppresses autoindent even when `autoindent=True`:
    eb.buffer.text = '    hello'
    handle_command(editor, ':set ai paste')
    handle_command(editor, ':1norm! opaste_line')
    assert eb.buffer.document.lines == ['    hello', 'paste_line']

    # Preview save/restore covers autoindent:
    previewer = CommandPreviewer(editor)
    previewer.preview(':set noai')
    assert editor.autoindent is False
    previewer.restore()
    assert editor.autoindent is True


def test_fileencoding_option_validation_and_write(editor, tmp_path):
    from vith.commands.commands import set_options_lines

    f = tmp_path / 'encoded.txt'
    f.write_text('caf\u00e9\n', encoding='utf-8')
    _open_files(editor, [str(f)])
    eb = editor.window_arrangement.active_editor_buffer

    handle_command(editor, ':set fenc?')
    assert editor.message == 'fileencoding=utf-8'

    # Invalid codec is rejected without mutating encoding:
    handle_command(editor, ':set fenc=not_a_real_codec_xyz')
    assert 'E474' in editor.message
    assert eb.encoding == 'utf-8'

    # Valid encoding updates `eb.encoding`, appears in bare `:set`, and writes bytes in that encoding:
    handle_command(editor, ':set fenc=latin-1')
    assert eb.encoding == 'latin-1'
    assert 'fileencoding=latin-1' in set_options_lines(editor, show_all=False)
    handle_command(editor, ':w')
    assert f.read_bytes() == 'caf\u00e9\n'.encode('latin-1')


def test_buffer_local_options_handle_empty_editor(editor):
    handle_command(editor, ':set ro')
    assert editor.message == 'No buffer'
    handle_command(editor, ':set fenc=utf-8')
    assert editor.message == 'No buffer'


def test_toml_config_supports_phase4_options(editor):
    from vith.rc_file import apply_toml_config

    apply_toml_config(
        editor,
        {'editor': {'smartcase': True, 'shiftwidth': 2, 'autoindent': False}},
    )
    assert editor.smartcase is True
    assert editor.shiftwidth == 2
    assert editor.autoindent is False


def test_registers_command_and_info_split(editor, tmp_path):
    from prompt_toolkit.clipboard import ClipboardData
    from prompt_toolkit.selection import SelectionType

    from vith.commands.commands import registers_lines

    f = tmp_path / 'reg.txt'
    f.write_text('first line\nsecond line\n', encoding='utf-8')
    _open_files(editor, [str(f)])

    # Empty clipboard and registers:
    editor.application.clipboard.set_data(ClipboardData(''))
    editor.application.vi_state.named_registers.clear()
    assert registers_lines(editor) == []
    handle_command(editor, ':reg')
    assert editor.message == 'No registers'

    # Populate unnamed clipboard (linewise), named char/block registers:
    handle_command(editor, ':1,2y')
    editor.application.vi_state.named_registers['a'] = ClipboardData(
        'hello\tworld', SelectionType.CHARACTERS
    )
    editor.application.vi_state.named_registers['0'] = ClipboardData('zero', SelectionType.BLOCK)

    lines = registers_lines(editor)
    assert lines[0] == 'Type Name Content'
    assert any(
        '""' in line and 'first line^Jsecond line' in line and '  l  ' in line for line in lines
    )
    assert any('"0' in line and 'zero' in line and '  b  ' in line for line in lines)
    assert any('"a' in line and 'hello^Iworld' in line and '  c  ' in line for line in lines)

    # `:registers` opens inside vith as a read-only `[Registers]` scratch split:
    handle_command(editor, ':registers')
    reg_eb = editor.window_arrangement.active_editor_buffer
    assert reg_eb.is_scratch is True
    assert reg_eb.scratch_title == '[Registers]'
    assert reg_eb.buffer.read_only() is True
    assert '"a' in reg_eb.buffer.text
    handle_command(editor, ':q')
    assert editor.window_arrangement.active_editor_buffer.location == str(f)


def test_marks_command_and_mark_navigation(editor, tmp_path):
    from vith.commands.commands import marks_lines

    f = tmp_path / 'marks.txt'
    f.write_text('  first line\n    second line\nthird line\n', encoding='utf-8')
    _open_files(editor, [str(f)])
    eb = editor.window_arrangement.active_editor_buffer

    # No marks yet:
    assert marks_lines(editor) == []
    handle_command(editor, ':marks')
    assert editor.message == 'No marks'

    # Set mark `a` on line 2 col 6 (`s` -> `c`) via `:norm!`, and set visual marks:
    handle_command(editor, ':2norm! 6lma')
    assert eb.marks['a'] == (1, 6)
    eb.visual_marks = (0, 2)

    lines = marks_lines(editor)
    assert lines[0] == 'mark line  col file/text'
    assert any(
        'a' in line and '   2' in line and '   6' in line and 'second line' in line
        for line in lines
    )
    assert any('<' in line and '   1' in line and 'first line' in line for line in lines)
    assert any('>' in line and '   3' in line and 'third line' in line for line in lines)

    # Jump to mark linewise (`'a` -> first non-blank, col 4) and exact (`` `a `` -> col 6):
    handle_command(editor, ":1norm! 'a")
    assert eb.buffer.document.cursor_position_row == 1
    assert eb.buffer.document.cursor_position_col == 4

    handle_command(editor, ':1norm! `a')
    assert eb.buffer.document.cursor_position_row == 1
    assert eb.buffer.document.cursor_position_col == 6

    # `:marks` opens inside vith as a read-only `[Marks]` scratch split:
    handle_command(editor, ':marks')
    marks_eb = editor.window_arrangement.active_editor_buffer
    assert marks_eb.is_scratch is True
    assert marks_eb.scratch_title == '[Marks]'
    assert marks_eb.buffer.read_only() is True
    assert 'second line' in marks_eb.buffer.text
    handle_command(editor, ':q')
    assert editor.window_arrangement.active_editor_buffer is eb


def test_phase4_command_and_option_completions(editor, tmp_path):
    from prompt_toolkit.completion import CompleteEvent
    from prompt_toolkit.document import Document

    from vith.commands.completer import create_command_completer

    f = tmp_path / 'alpha.py'
    f.write_text('x = 1\n', encoding='utf-8')
    _open_files(editor, [str(f)])
    completer = create_command_completer(editor)

    def complete(text):
        doc = Document(text, len(text))
        return [c.text for c in completer.get_completions(doc, CompleteEvent())]

    # Phase 1-4 commands registered in command completion:
    assert 'registers ' in complete('reg')
    assert 'reg ' in complete('reg')
    assert 'marks ' in complete('mar')
    assert 'saveas ' in complete('sav')
    assert 'source ' in complete('sou')
    assert 'normal ' in complete('nor')
    assert 'tabmove ' in complete('tabm')
    assert 'tabs ' in complete('tab')

    # Phase 4 `:set` options + `inv*` + graceful `?`/`!`:
    assert 'smartcase' in complete('set sm')
    assert 'scs' in complete('set sc')
    assert 'shiftwidth' in complete('set sh')
    assert 'sw' in complete('set sw')
    assert 'readonly' in complete('set re')
    assert 'ro' in complete('set ro')
    assert 'invro' in complete('set invr')
    assert 'autoindent' in complete('set au')
    assert 'fileencoding' in complete('set filee')
    assert 'fenc' in complete('set fe')
    assert complete('set nu?') == []
    assert complete('set nu!') == []

    # Buffer completion on `:bd` / `:sb` and path completion on `:sav` / `:so`:
    assert str(f) in complete('bd ')
    assert str(f) in complete('sb ')
    assert any('alpha.py' in item for item in complete(f'sav {tmp_path}/'))
    assert any('alpha.py' in item for item in complete(f'so {tmp_path}/'))
