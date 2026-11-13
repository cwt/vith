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
