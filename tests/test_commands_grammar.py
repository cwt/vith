from vith.commands.grammar import COMMAND_GRAMMAR
from vith.commands.handler import handle_command


def test_colo_matches_grammar():
    m = COMMAND_GRAMMAR.match('colo default')
    assert m is not None
    assert m.variables().get('command') == 'colo'
    assert m.variables().get('colorscheme') == 'default'


def test_unknown_multiword_command_shows_message(editor):
    handle_command(editor, ':foo bar')
    assert editor.message is not None


def test_location_commands_alternation_longest_first():
    # 073 row 8: longest names come first in the alternation, so `wq`/`write`
    # (and friends) match directly instead of relying on generic-branch
    # backtracking; parsing results stay identical to the alphabetical era.
    from vith.commands.grammar import COMMAND_GRAMMAR

    for line, expected in (
        (':wq file.txt', 'wq'),
        (':wq !', 'wq'),
        (':write other.txt', 'write'),
        (':w elsewhere.txt', 'w'),
        (':edit foo', 'edit'),
        (':e foo', 'e'),
        (':tabe foo', 'tabe'),
        (':tabedit foo', 'tabedit'),
        (':vsplit dir', 'vsplit'),
    ):
        m = COMMAND_GRAMMAR.match(line)
        assert m is not None, line
        assert m.variables().get('command') == expected, line


def test_substitute_grammar_ranges_delimiters_and_non_collisions():
    for line, exp_range, exp_args in (
        (':%s/a/b/g', '%', '/a/b/g'),
        (':%s', '%', None),
        (':.,$s#a/b#c#i', '.,$', '#a/b#c#i'),
        (":'<,'>s|a|b|", "'<,'>", '|a|b|'),
        (':.-1,.+1s/a/b', '.-1,.+1', '/a/b'),
        (':s /a/b', '', ' /a/b'),
    ):
        m = COMMAND_GRAMMAR.match(line)
        assert m is not None, line
        variables = m.variables()
        assert variables.get('command') == 's', line
        assert variables.get('sub_range') == exp_range, line
        assert variables.get('sub_args') == exp_args, line

    for line, expected_cmd in (
        (':set number', 'set'),
        (':setlocal ts=4', 'setlocal'),
        (':sp foo.py', 'sp'),
        (':split', 'split'),
        (':s1', 's1'),
        (':sub', 'sub'),
    ):
        m = COMMAND_GRAMMAR.match(line)
        assert m is not None, line
        variables = m.variables()
        assert variables.get('command') == expected_cmd, line
        assert variables.get('sub_args') is None, line


def test_phase1_aliases_match_without_collisions():
    for line, expected_cmd, expected_force in (
        (':ls', 'ls', ''),
        (':files', 'files', ''),
        (':bnext', 'bnext', ''),
        (':bnext!', 'bnext', '!'),
        (':bprev', 'bprev', ''),
        (':bprevious!', 'bprevious', '!'),
        (':bdelete', 'bdelete', ''),
        (':bwipeout!', 'bwipeout', '!'),
        (':on', 'on', ''),
        (':clo', 'clo', ''),
        (':close!', 'close', '!'),
        (':wqall', 'wqall', ''),
        (':wqall!', 'wqall', '!'),
        (':u', 'u', ''),
        (':undo', 'undo', ''),
        (':red', 'red', ''),
        (':redo', 'redo', ''),
        (':noh', 'noh', ''),
        (':nohlsearch', 'nohlsearch', ''),
        (':up', 'up', ''),
        (':update!', 'update', '!'),
        (':x', 'x', ''),
        (':xit!', 'xit', '!'),
        (':exit', 'exit', ''),
        (':wa', 'wa', ''),
        (':wall!', 'wall', '!'),
        (':xa', 'xa', ''),
        (':xall!', 'xall', '!'),
        (':enew', 'enew', ''),
        (':enew!', 'enew', '!'),
        (':ene', 'ene', ''),
        (':ene!', 'ene', '!'),
        (':f', 'f', ''),
        (':file', 'file', ''),
        (':ver', 'ver', ''),
        (':version', 'version', ''),
        (':mes', 'mes', ''),
        (':messages', 'messages', ''),
        (':so', 'so', ''),
        (':source', 'source', ''),
        (':bf', 'bf', ''),
        (':bfirst!', 'bfirst', '!'),
        (':bl', 'bl', ''),
        (':blast!', 'blast', '!'),
        (':sb', 'sb', ''),
        (':sbuffer', 'sbuffer', ''),
        (':tabo', 'tabo', ''),
        (':tabonly!', 'tabonly', '!'),
        (':tabfir', 'tabfir', ''),
        (':tabfirst', 'tabfirst', ''),
        (':tabl', 'tabl', ''),
        (':tablast', 'tablast', ''),
        (':tabm', 'tabm', ''),
        (':tabmove', 'tabmove', ''),
        (':tabs', 'tabs', ''),
    ):
        m = COMMAND_GRAMMAR.match(line)
        assert m is not None, line
        variables = m.variables()
        assert variables.get('command') == expected_cmd, line
        assert variables.get('force') == expected_force, line

    for line, expected_cmd, expected_arg in (
        (':tabm 0', 'tabm', '0'),
        (':tabm +1', 'tabm', '+1'),
        (':tabmove -2', 'tabmove', '-2'),
        (':tabmove $', 'tabmove', '$'),
    ):
        m = COMMAND_GRAMMAR.match(line)
        assert m is not None, line
        variables = m.variables()
        assert variables.get('command') == expected_cmd, line
        assert variables.get('tab_arg') == expected_arg, line

    for line, expected_cmd, expected_buf, expected_force in (
        (':b #', 'b', '#', ''),
        (':buffer 2', 'buffer', '2', ''),
        (':bd 2', 'bd', '2', ''),
        (':bd! foo.py', 'bd', 'foo.py', '!'),
        (':bdelete #', 'bdelete', '#', ''),
        (':bw! 1', 'bw', '1', '!'),
        (':bwipeout bar', 'bwipeout', 'bar', ''),
        (':sb 2', 'sb', '2', ''),
        (':sbuffer foo.py', 'sbuffer', 'foo.py', ''),
    ):
        m = COMMAND_GRAMMAR.match(line)
        assert m is not None, line
        variables = m.variables()
        assert variables.get('command') == expected_cmd, line
        assert variables.get('buffer_name') == expected_buf, line
        assert variables.get('force') == expected_force, line

    for line, expected_cmd, expected_loc in (
        (':up dest.txt', 'up', 'dest.txt'),
        (':update! dest.txt', 'update', 'dest.txt'),
        (':x out.txt', 'x', 'out.txt'),
        (':xit out.txt', 'xit', 'out.txt'),
        (':exit! out.txt', 'exit', 'out.txt'),
        (':f newname.py', 'f', 'newname.py'),
        (':file newname.py', 'file', 'newname.py'),
        (':sav copy.py', 'sav', 'copy.py'),
        (':sav! copy.py', 'sav', 'copy.py'),
        (':saveas copy.py', 'saveas', 'copy.py'),
        (':saveas! copy.py', 'saveas', 'copy.py'),
        (':so ~/.vithrc', 'so', '~/.vithrc'),
        (':source config.toml', 'source', 'config.toml'),
    ):
        m = COMMAND_GRAMMAR.match(line)
        assert m is not None, line
        variables = m.variables()
        assert variables.get('command') == expected_cmd, line
        assert variables.get('location') == expected_loc, line


def test_read_command_grammar_and_non_collision_with_redo():
    for line, exp_addr, exp_cmd, exp_loc, exp_shell in (
        (':r', None, 'r', None, None),
        (':read', None, 'read', None, None),
        (':0r foo.txt', '0', 'r', 'foo.txt', None),
        (':$read ~/bar.txt', '$', 'read', '~/bar.txt', None),
        (':.-1r snippet.py', '.-1', 'r', 'snippet.py', None),
        (":'<r part.txt", "'<", 'r', 'part.txt', None),
        (':r !echo hello', None, 'r', None, 'echo hello'),
        (':r!echo hello', None, 'r', None, 'echo hello'),
        (':0read !date -u', '0', 'read', None, 'date -u'),
    ):
        m = COMMAND_GRAMMAR.match(line)
        assert m is not None, line
        v = m.variables()
        assert v.get('read_addr') == exp_addr, line
        assert v.get('command') == exp_cmd, line
        assert v.get('location') == exp_loc, line
        assert v.get('read_shell') == exp_shell, line

    # `:red` and `:redo` must not collide with `:r`/`:read`.
    for line, expected_cmd in ((':red', 'red'), (':redo', 'redo')):
        m = COMMAND_GRAMMAR.match(line)
        assert m is not None, line
        v = m.variables()
        assert v.get('command') == expected_cmd, line
        assert v.get('read_shell') is None, line


def test_bare_address_jump_grammar():
    for line, expected_addr in (
        (':42', '42'),
        (':$', '$'),
        (':.', '.'),
        (':+3', '+3'),
        (':-2', '-2'),
        (':+', '+'),
        (':-', '-'),
        (':$-1', '$-1'),
        (':.+2', '.+2'),
        (":'<", "'<"),
        (":'>", "'>"),
    ):
        m = COMMAND_GRAMMAR.match(line)
        assert m is not None, line
        assert m.variables().get('go_to_line') == expected_addr, line


def test_range_line_editing_grammar_and_non_collisions():
    for line, exp_range, exp_cmd, exp_force, exp_arg, exp_dest in (
        (':d', None, 'd', '', None, None),
        (':2,4d', '2,4', 'd', '', None, None),
        (':%delete', '%', 'delete', '', None, None),
        (':2d 3', '2', 'd', '', '3', None),
        (':1,3y', '1,3', 'y', '', None, None),
        (':0pu', '0', 'pu', '', None, None),
        (':$put!', '$', 'put', '!', None, None),
        (':1,3j!', '1,3', 'j', '!', None, None),
        (':2,4m 0', '2,4', 'm', None, None, '0'),
        (':m+1', None, 'm', None, None, '+1'),
        (':1,3t $', '1,3', 't', None, None, '$'),
        (':co.', None, 'co', None, None, '.'),
        (':t0', None, 't', None, None, '0'),
        (':sort', None, 'sort', '', None, None),
        (':2,5sort! iu', '2,5', 'sort', '!', 'iu', None),
        (':%retab! 4', '%', 'retab', '!', '4', None),
    ):
        m = COMMAND_GRAMMAR.match(line)
        assert m is not None, line
        v = m.variables()
        assert v.get('cmd_range') == exp_range, line
        assert v.get('command') == exp_cmd, line
        assert v.get('force') == exp_force, line
        assert v.get('cmd_arg') == exp_arg, line
        assert v.get('dest_addr') == exp_dest, line

    # Ensure no collisions with `:colo`, `:comp`, `:mes`, `:tabe`, `:tabs`.
    for line, expected_cmd in (
        (':colo vim', 'colo'),
        (':comp', 'comp'),
        (':mes', 'mes'),
        (':messages', 'messages'),
        (':tabe foo.py', 'tabe'),
        (':tabs', 'tabs'),
        (':ver', 'ver'),
        (':version', 'version'),
        (':vsp a.py', 'vsp'),
        (':vnew', 'vnew'),
    ):
        m = COMMAND_GRAMMAR.match(line)
        assert m is not None, line
        assert m.variables().get('command') == expected_cmd, line


def test_global_and_vglobal_grammar():
    for line, exp_range, exp_cmd, exp_force, exp_args in (
        (':g/foo/d', None, 'g', '', '/foo/d'),
        (':g!/foo/d', None, 'g', '!', '/foo/d'),
        (':2,5global#a/b#s/a/b/g', '2,5', 'global', '', '#a/b#s/a/b/g'),
        (':v/bar/p', None, 'v', '', '/bar/p'),
        (':%vglobal/baz/y', '%', 'vglobal', '', '/baz/y'),
    ):
        m = COMMAND_GRAMMAR.match(line)
        assert m is not None, line
        v = m.variables()
        assert v.get('cmd_range') == exp_range, line
        assert v.get('command') == exp_cmd, line
        assert v.get('force') == exp_force, line
        assert v.get('global_args') == exp_args, line


def test_filter_and_range_write_grammar():
    for line, exp_range, exp_shell in (
        (':%!sort', '%', 'sort'),
        (':2,4!tr a-z A-Z', '2,4', 'tr a-z A-Z'),
        (":'<,'>!jq .", "'<,'>", 'jq .'),
    ):
        m = COMMAND_GRAMMAR.match(line)
        assert m is not None, line
        v = m.variables()
        assert v.get('filter_range') == exp_range, line
        assert v.get('filter_shell') == exp_shell, line
        assert v.get('shell_command') is None, line

    # Bare `:!cmd` stays on `shell_command`.
    m_bare = COMMAND_GRAMMAR.match(':!ls -la')
    assert m_bare is not None
    assert m_bare.variables().get('shell_command') == 'ls -la'
    assert m_bare.variables().get('filter_shell') is None

    for line, exp_range, exp_cmd, exp_force, exp_loc, exp_app, exp_sh in (
        (':2,4w out.txt', '2,4', 'w', '', 'out.txt', None, None),
        (':2,4w! out.txt', '2,4', 'w', '!', 'out.txt', None, None),
        (':w >> log.txt', None, 'w', '', None, 'log.txt', None),
        (':2,4w! >> log.txt', '2,4', 'w', '!', None, 'log.txt', None),
        (':w !wc -l', None, 'w', '', None, None, 'wc -l'),
        (':2,4write !cat', '2,4', 'write', '', None, None, 'cat'),
    ):
        m = COMMAND_GRAMMAR.match(line)
        assert m is not None, line
        v = m.variables()
        assert v.get('cmd_range') == exp_range, line
        assert v.get('command') == exp_cmd, line
        assert v.get('force') == exp_force, line
        assert v.get('location') == exp_loc, line
        assert v.get('write_append') == exp_app, line
        assert v.get('write_shell') == exp_sh, line


def test_normal_command_grammar():
    for line, exp_range, exp_cmd, exp_force, exp_keys in (
        (':norm A;', None, 'norm', '', 'A;'),
        (':%norm! dd', '%', 'norm', '!', 'dd'),
        (':1,3normal I# ', '1,3', 'normal', '', 'I# '),
    ):
        m = COMMAND_GRAMMAR.match(line)
        assert m is not None, line
        v = m.variables()
        assert v.get('cmd_range') == exp_range, line
        assert v.get('command') == exp_cmd, line
        assert v.get('force') == exp_force, line
        assert v.get('norm_keys') == exp_keys, line


def test_set_grammar_bare_all_query_toggle_and_multi():
    for line, exp_cmd, exp_opt, exp_val, exp_rest in (
        (':set', 'set', None, None, None),
        (':setlocal', 'setlocal', None, None, None),
        (':set all', 'set', 'all', None, None),
        (':set nu?', 'set', 'nu?', None, None),
        (':set nu!', 'set', 'nu!', None, None),
        (':set invnu', 'set', 'invnu', None, None),
        (':set nu rnu ts=4 et', 'set', 'nu', None, 'rnu ts=4 et'),
        (':setl ts=2 ft=python', 'setl', 'ts', '2', 'ft=python'),
    ):
        m = COMMAND_GRAMMAR.match(line)
        assert m is not None, line
        v = m.variables()
        assert v.get('command') == exp_cmd, line
        assert v.get('set_option') == exp_opt, line
        assert v.get('set_value') == exp_val, line
        assert v.get('set_rest') == exp_rest, line


def test_reg_and_marks_grammar_non_collisions():
    for line, expected_cmd in (
        (':reg', 'reg'),
        (':registers', 'registers'),
        (':marks', 'marks'),
    ):
        m = COMMAND_GRAMMAR.match(line)
        assert m is not None, line
        v = m.variables()
        assert v.get('command') == expected_cmd, line
        assert v.get('dest_addr') is None, line
        assert v.get('read_shell') is None, line
