from vith.commands.handler import handle_command

sample_text = """
Roses are red,
    Violets are blue,
Sugar is sweet,
    And so are you.
""".lstrip()


def given_sample_text(editor_buffer, text=None):
    editor = editor_buffer.editor
    editor.window_arrangement._add_editor_buffer(editor_buffer)
    editor_buffer.buffer.text = text or sample_text
    editor.sync_with_prompt_toolkit()


def given_cursor_position(editor_buffer, line_number, column=0):
    editor_buffer.buffer.cursor_position = editor_buffer.buffer.document.translate_row_col_to_index(
        line_number - 1, column
    )


def test_substitute_current_line(editor, editor_buffer):
    given_sample_text(editor_buffer)
    given_cursor_position(editor_buffer, 2)

    handle_command(editor, ':s/s are/ is')

    assert 'Roses are red,' in editor_buffer.buffer.text
    assert 'Violet is blue,' in editor_buffer.buffer.text
    assert 'And so are you.' in editor_buffer.buffer.text
    assert editor_buffer.buffer.cursor_position == editor_buffer.buffer.text.index('Violet')


def test_substitute_single_line(editor, editor_buffer):
    given_sample_text(editor_buffer)
    given_cursor_position(editor_buffer, 1)

    handle_command(editor, ':2s/s are/ is')

    assert 'Roses are red,' in editor_buffer.buffer.text
    assert 'Violet is blue,' in editor_buffer.buffer.text
    assert 'And so are you.' in editor_buffer.buffer.text
    assert editor_buffer.buffer.cursor_position == editor_buffer.buffer.text.index('Violet')


def test_substitute_range(editor, editor_buffer):
    given_sample_text(editor_buffer)
    given_cursor_position(editor_buffer, 1)

    handle_command(editor, ':1,3s/s are/ is')

    assert 'Rose is red,' in editor_buffer.buffer.text
    assert 'Violet is blue,' in editor_buffer.buffer.text
    assert 'And so are you.' in editor_buffer.buffer.text
    # The cursor must land on the last *substituted* line (line 2, "Violets
    # are…"), not the end of the range (docs/bugs/015.md).
    assert editor_buffer.buffer.cursor_position == editor_buffer.buffer.text.index('Violet')


def test_substitute_range_beyond_eof_is_an_error_not_a_crash(editor, editor_buffer):
    given_sample_text(editor_buffer)
    text_before = editor_buffer.buffer.text
    cursor_before = editor_buffer.buffer.cursor_position

    handle_command(editor, ':900s/s are/ is')

    # The out-of-document range reports the vim-like error and changes nothing.
    assert editor.message == 'E19: No such line in current buffer'
    assert editor_buffer.buffer.text == text_before
    assert editor_buffer.buffer.cursor_position == cursor_before


def test_substitute_no_match_keeps_cursor(editor, editor_buffer):
    given_sample_text(editor_buffer, 'Violet\n' * 4)
    cursor_before = editor_buffer.buffer.cursor_position

    handle_command(editor, ':1,4s/zero/nothing')

    # Nothing matched anywhere in the range: text (and cursor) unchanged.
    assert editor_buffer.buffer.text == 'Violet\nViolet\nViolet\nViolet\n'
    assert editor_buffer.buffer.cursor_position == cursor_before


def test_substitute_range_boundaries(editor, editor_buffer):
    given_sample_text(editor_buffer, 'Violet\n' * 4)

    handle_command(editor, ':2,3s/Violet/Rose')

    assert 'Violet\nRose\nRose\nViolet\n' in editor_buffer.buffer.text


def test_substitute_from_search_history(editor, editor_buffer):
    given_sample_text(editor_buffer)
    editor.application.current_search_state.text = 'blue'

    handle_command(editor, ':1,3s//pretty')
    assert 'Violets are pretty,' in editor_buffer.buffer.text


def test_substitute_from_substitute_search_history(editor, editor_buffer):
    given_sample_text(editor_buffer, 'Violet is Violet\n')

    handle_command(editor, ':s/Violet/Rose')
    assert 'Rose is Violet' in editor_buffer.buffer.text

    handle_command(editor, ':s//Lily')
    assert 'Rose is Lily' in editor_buffer.buffer.text


def test_substitute_with_repeat_last_substitution(editor, editor_buffer):
    given_sample_text(editor_buffer, 'Violet is Violet\n')
    editor.application.current_search_state.text = 'Lily'

    handle_command(editor, ':s/Violet/Rose')
    assert 'Rose is Violet' in editor_buffer.buffer.text

    handle_command(editor, ':s')
    assert 'Rose is Rose' in editor_buffer.buffer.text


def test_substitute_without_replacement_text(editor, editor_buffer):
    given_sample_text(editor_buffer, 'Violet Violet Violet \n')
    editor.application.current_search_state.text = 'Lily'

    handle_command(editor, ':s/Violet/')
    assert ' Violet Violet \n' in editor_buffer.buffer.text

    handle_command(editor, ':s/Violet')
    assert '  Violet \n' in editor_buffer.buffer.text

    handle_command(editor, ':s/')
    assert '   \n' in editor_buffer.buffer.text


def test_substitute_with_repeat_last_substitution_without_previous_substitution(
    editor, editor_buffer
):
    original_text = 'Violet is blue\n'
    given_sample_text(editor_buffer, original_text)

    handle_command(editor, ':s')
    assert original_text in editor_buffer.buffer.text

    editor.application.current_search_state.text = 'blue'

    handle_command(editor, ':s')
    assert 'Violet is \n' in editor_buffer.buffer.text


def test_substitute_flags_empty_flags(editor, editor_buffer):
    given_sample_text(editor_buffer, 'Violet is Violet\n')
    handle_command(editor, ':s/Violet/Rose/')
    assert 'Rose is Violet' in editor_buffer.buffer.text


def test_substitute_flags_g(editor, editor_buffer):
    given_sample_text(editor_buffer, 'Violet is Violet\n')
    handle_command(editor, ':s/Violet/Rose/g')
    assert 'Rose is Rose' in editor_buffer.buffer.text


def test_substitute_invalid_regex_does_not_crash(editor, editor_buffer):
    given_sample_text(editor_buffer, 'hello\n')
    handle_command(editor, ':s/[unclosed/x')
    assert editor.message is not None
    assert editor_buffer.buffer.text == 'hello\n'


def test_substitute_invalid_replacement_does_not_crash(editor, editor_buffer):
    given_sample_text(editor_buffer, 'hello\n')
    handle_command(editor, ':s/hello/\\1')
    assert editor.message is not None


def test_substitute_empty_search_history_does_not_mutate(editor, editor_buffer):
    given_sample_text(editor_buffer, 'hello\n')
    editor.application.current_search_state.text = ''
    before = editor_buffer.buffer.text
    handle_command(editor, ':s//X')
    assert editor_buffer.buffer.text == before
    assert editor.message is not None


def test_substitute_zero_matches_does_not_mark_dirty(editor, editor_buffer):
    given_sample_text(editor_buffer, 'hello\n')
    editor_buffer.buffer.text = 'hello'
    editor_buffer._file_content = editor_buffer.buffer.text
    editor_buffer._has_unsaved_changes = False
    assert not editor_buffer.has_unsaved_changes
    handle_command(editor, ':s/notfound/x/g')
    assert not editor_buffer.has_unsaved_changes


def test_substitute_double_slash_empty_replace_with_flags(editor, editor_buffer):
    # 083: `:s/pat//g` (empty replacement + flags) parses like Vim.
    given_sample_text(editor_buffer, 'land land\n')
    handle_command(editor, ':s/land//g')
    assert editor_buffer.buffer.text == ' \n'


def test_substitute_row_zero_range(editor, editor_buffer):
    # 083: `:0,1s` maps row 0 to the first line like Vim.
    given_sample_text(editor_buffer, 'land land\n')
    handle_command(editor, ':0,1s/land/sea/g')
    assert 'sea sea' in editor_buffer.buffer.text


def test_substitute_flags_i_and_I(editor, editor_buffer):
    # 094: `:s` flags `i` and `I` override editor.ignore_case
    given_sample_text(editor_buffer, 'Foo foo FOO\n')
    editor.ignore_case = False

    # `i` matches case-insensitively
    handle_command(editor, ':s/foo/X/i')
    assert editor_buffer.buffer.text == 'X foo FOO\n'

    # `I` forces case-sensitivity even if editor.ignore_case is True
    editor_buffer.buffer.text = 'Foo foo FOO\n'
    editor.ignore_case = True
    handle_command(editor, ':s/foo/X/gI')
    assert editor_buffer.buffer.text == 'Foo X FOO\n'


def test_substitute_unknown_flag_shows_error(editor, editor_buffer):
    # 094: invalid substitute flags show error and do not mutate
    given_sample_text(editor_buffer, 'hello world\n')
    handle_command(editor, ':s/hello/world/z')
    assert editor.message is not None
    assert 'E488: Trailing characters: z' in editor.message
    assert editor_buffer.buffer.text == 'hello world\n'


def test_substitute_active_buffer_none_guard(editor):
    # 103: substitute with no active editor buffer does not raise AttributeError
    from vith.commands.commands import substitute

    substitute(editor, None, None, 'foo', 'bar', '')
    # cleanly returned without exception


def test_substitute_latches_thai_gate(editor, editor_buffer, monkeypatch):
    # 081: mass replacement introducing Thai must latch the renderer gate
    # (the cursor-line-only note used to miss it, leaving the 002/062-era
    # sanitizer disarmed while the screen shows Thai).
    import vith.thai as thai

    monkeypatch.setattr(thai._THAI_GATE, "value", False)
    try:
        given_sample_text(editor_buffer, 'land is plain\n')
        handle_command(editor, ':s/land/ทำ/g')
        assert thai._THAI_GATE.value is True
    finally:
        thai._THAI_GATE.value = False


def test_substitute_identical_replacement_succeeds_and_moves_cursor(editor, editor_buffer):
    # 110 item 1: `:s/foo/foo/` where replacement equals matched text must
    # succeed (not report "Pattern not found") and move cursor to the matched row.
    given_sample_text(editor_buffer, 'first\nsecond\ntarget\n')
    editor.message = None
    handle_command(editor, ':1,3s/target/target/')
    assert editor.message is None
    assert editor_buffer.buffer.document.cursor_position_row == 2


def test_substitute_rejects_nested_quantifier(editor, editor_buffer):
    # 123: catastrophic patterns are rejected without touching the buffer.
    before = 'a' * 30 + '\n'
    given_sample_text(editor_buffer, before)
    handle_command(editor, ':s/(a+)+$/x')
    assert 'Unsafe pattern' in editor.message
    assert editor_buffer.buffer.text == before


def test_substitute_rejects_common_catastrophic_shapes(editor, editor_buffer):
    # 123: other nested-quantifier shapes are rejected; the buffer and the
    # search history stay untouched.
    from vith.commands.commands import _strip_regex_literals

    assert _strip_regex_literals(r'\(a+\)+') == 'a++'
    given_sample_text(editor_buffer, 'aaa bbb\n')
    for pattern in (r'([a-z]+)*$', r'(\w+\s?)+', '(a|a?)+'):
        editor.message = None
        handle_command(editor, ':s/%s/x' % pattern)
        assert 'Unsafe pattern' in editor.message, pattern
    assert editor_buffer.buffer.text == 'aaa bbb\n'


def test_substitute_allows_safe_grouped_patterns(editor, editor_buffer):
    # 123: the guard only fires on nested quantification — grouped
    # alternation, quantified classes, and escaped parens still substitute.
    given_sample_text(editor_buffer, 'foo bar\n')
    handle_command(editor, ':s/(foo|bar)/baz/')
    assert editor_buffer.buffer.text.startswith('baz bar')

    editor_buffer.buffer.text = 'aaa\n'
    editor.message = None
    handle_command(editor, ':s/[a+]+/x/')
    assert editor.message is None
    assert editor_buffer.buffer.text.startswith('x')


def test_empty_and_colon_only_commands_are_noops(editor, editor_buffer):
    # 110 item 3: pressing Enter on `:`, `:   `, or whitespace is a silent
    # no-op in Vim rather than "Unknown command".
    editor.message = None
    for empty_cmd in ('', ':', ':   ', '   '):
        handle_command(editor, empty_cmd)
        assert editor.message is None


def test_substitute_percent_range_replaces_entire_buffer(editor, editor_buffer):
    given_sample_text(editor_buffer, 'one foo foo\ntwo foo\nthree foo\n')
    given_cursor_position(editor_buffer, 2)

    handle_command(editor, ':%s/foo/bar/')
    assert editor_buffer.buffer.text == 'one bar foo\ntwo bar\nthree bar\n'

    handle_command(editor, ':%s/foo/baz/g')
    assert editor_buffer.buffer.text == 'one bar baz\ntwo bar\nthree bar\n'


def test_substitute_percent_repeat_last_substitution(editor, editor_buffer):
    given_sample_text(editor_buffer, 'alpha\nbeta alpha\ngamma\n')
    given_cursor_position(editor_buffer, 1)

    handle_command(editor, ':s/alpha/omega')
    assert editor_buffer.buffer.text == 'omega\nbeta alpha\ngamma\n'

    handle_command(editor, ':%s')
    assert editor_buffer.buffer.text == 'omega\nbeta omega\ngamma\n'


def test_substitute_dot_and_dollar_ranges(editor, editor_buffer):
    given_sample_text(editor_buffer, 'line1 x\nline2 x\nline3 x\nline4 x\n')
    given_cursor_position(editor_buffer, 2)

    handle_command(editor, ':.s/x/DOT')
    assert editor_buffer.buffer.document.lines == ['line1 x', 'line2 DOT', 'line3 x', 'line4 x', '']

    editor_buffer.buffer.text = 'line1 x\nline2 x\nline3 x\nline4 x'
    given_cursor_position(editor_buffer, 2)
    handle_command(editor, ':.,$s/x/TAIL')
    assert editor_buffer.buffer.document.lines == [
        'line1 x',
        'line2 TAIL',
        'line3 TAIL',
        'line4 TAIL',
    ]

    editor_buffer.buffer.text = 'line1 x\nline2 x\nline3 x'
    handle_command(editor, ':$s/x/LAST')
    assert editor_buffer.buffer.document.lines == ['line1 x', 'line2 x', 'line3 LAST']

    editor_buffer.buffer.text = 'line1 x\nline2 x\nline3 x'
    handle_command(editor, ':1,$s/x/ALL')
    assert editor_buffer.buffer.document.lines == ['line1 ALL', 'line2 ALL', 'line3 ALL']


def test_substitute_relative_line_offsets(editor, editor_buffer):
    given_sample_text(editor_buffer, 'a1\na2\na3\na4\na5')
    given_cursor_position(editor_buffer, 3)

    handle_command(editor, ':.-1,.+1s/a/b')
    assert editor_buffer.buffer.document.lines == ['a1', 'b2', 'b3', 'b4', 'a5']

    handle_command(editor, ':$-1,$s/a/c')
    assert editor_buffer.buffer.document.lines == ['a1', 'b2', 'b3', 'b4', 'c5']

    # Negative or beyond-EOF offset reports E19 without mutating.
    before = editor_buffer.buffer.text
    editor.message = None
    handle_command(editor, ':.-5s/b/z')
    assert editor.message == 'E19: No such line in current buffer'
    assert editor_buffer.buffer.text == before

    editor.message = None
    handle_command(editor, ':$,$+1s/b/z')
    assert editor.message == 'E19: No such line in current buffer'
    assert editor_buffer.buffer.text == before


def test_substitute_inverted_range_shows_error(editor, editor_buffer):
    given_sample_text(editor_buffer, 'one\ntwo\nthree\n')
    handle_command(editor, ':3,1s/one/two')
    assert editor.message == 'E19: No such line in current buffer'
    assert editor_buffer.buffer.text == 'one\ntwo\nthree\n'


def test_substitute_visual_marks_range_and_unset_mark_error(editor, editor_buffer):
    given_sample_text(editor_buffer, 'one x\ntwo x\nthree x\nfour x')
    handle_command(editor, ":'<,'>s/x/y/g")
    assert editor.message == 'E20: Mark not set'
    assert editor_buffer.buffer.text == 'one x\ntwo x\nthree x\nfour x'

    editor_buffer.visual_marks = (1, 2)
    editor.message = None
    handle_command(editor, ":'<,'>s/x/y/g")
    assert editor.message is None
    assert editor_buffer.buffer.document.lines == ['one x', 'two y', 'three y', 'four x']


def test_substitute_alternative_delimiters(editor, editor_buffer):
    given_sample_text(editor_buffer, '/usr/bin/python\n/usr/local/bin\n')

    handle_command(editor, ':%s#/usr/bin#/opt/bin#g')
    assert editor_buffer.buffer.text == '/opt/bin/python\n/usr/local/bin\n'

    handle_command(editor, ':%s|/usr/local|/opt/local|g')
    assert editor_buffer.buffer.text == '/opt/bin/python\n/opt/local/bin\n'

    handle_command(editor, ':%s!/opt/local!!g')
    assert editor_buffer.buffer.text == '/opt/bin/python\n/bin\n'

    handle_command(editor, ':%s:/opt/bin:/usr/bin:i')
    assert editor_buffer.buffer.text == '/usr/bin/python\n/bin\n'


def test_substitute_escaped_delimiter_and_regex_backslashes(editor, editor_buffer):
    given_sample_text(editor_buffer, 'a/b and c\\d and 123\n')

    handle_command(editor, r':s/a\/b/x\/y/g')
    assert editor_buffer.buffer.text == 'x/y and c\\d and 123\n'

    handle_command(editor, r':s/c\\/z\\/')
    assert editor_buffer.buffer.text == 'x/y and z\\d and 123\n'

    handle_command(editor, r':s#(\d+)#[\1]#g')
    assert editor_buffer.buffer.text == 'x/y and z\\d and [123]\n'


def test_substitute_flag_n_counts_without_mutating(editor, editor_buffer):
    given_sample_text(editor_buffer, 'foo bar foo\nbaz foo\n')
    editor_buffer._file_content = editor_buffer.buffer.text
    editor_buffer._has_unsaved_changes = False
    cursor_before = editor_buffer.buffer.cursor_position

    handle_command(editor, ':%s/foo/qux/gn')
    assert editor.message == '3 matches on 2 lines'
    assert editor_buffer.buffer.text == 'foo bar foo\nbaz foo\n'
    assert not editor_buffer.has_unsaved_changes
    assert editor_buffer.buffer.cursor_position == cursor_before

    # Without `g`, at most 1 match per line is counted; singular grammar holds.
    handle_command(editor, ':1s/foo/qux/n')
    assert editor.message == '1 match on 1 line'
    assert editor_buffer.buffer.text == 'foo bar foo\nbaz foo\n'

    handle_command(editor, ':%s/missing/qux/n')
    assert editor.message == 'Pattern not found'


def test_substitute_flag_p_prints_last_substituted_line(editor, editor_buffer):
    given_sample_text(editor_buffer, 'first foo\n  second foo\nthird bar\n')
    handle_command(editor, ':%s/foo/baz/gp')
    assert editor_buffer.buffer.text == 'first baz\n  second baz\nthird bar\n'
    assert editor.message == '  second baz'


def test_substitute_visual_colon_prefills_marks_and_runs(editor):
    from unittest.mock import MagicMock

    from prompt_toolkit.key_binding.vi_state import InputMode
    from prompt_toolkit.selection import SelectionState, SelectionType

    from vith.commands.handler import handle_command
    from vith.key_bindings import create_key_bindings

    editor.load_initial_files([None])
    editor.sync_with_prompt_toolkit()
    eb = editor.window_arrangement.active_editor_buffer
    eb.buffer.text = 'old 1\nold 2\nold 3\nold 4'
    # Select upward from line 3 to line 2 to verify row ordering normalization.
    orig_pos = eb.buffer.document.translate_row_col_to_index(2, 2)
    cur_pos = eb.buffer.document.translate_row_col_to_index(1, 0)
    eb.buffer.cursor_position = cur_pos
    eb.buffer.selection_state = SelectionState(orig_pos, SelectionType.LINES)

    kb = create_key_bindings(editor)
    handler = [
        b
        for b in kb.bindings
        if b.keys == (':',) and b.handler.__name__ == 'enter_command_mode_from_selection'
    ][0].handler

    event = MagicMock()
    event.app = editor.application
    event.current_buffer = eb.buffer
    handler(event)

    assert eb.buffer.selection_state is None
    assert eb.visual_marks == (1, 2)
    assert editor.command_buffer.text == "'<,'>"
    assert editor.command_buffer.cursor_position == len("'<,'>")
    assert editor.application.vi_state.input_mode == InputMode.INSERT

    cmd_text = editor.command_buffer.text + 's/old/new/g'
    editor.leave_command_mode(append_to_history=True)
    handle_command(editor, cmd_text)
    assert eb.buffer.document.lines == ['old 1', 'new 2', 'new 3', 'old 4']
