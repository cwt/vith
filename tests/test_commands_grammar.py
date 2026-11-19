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
