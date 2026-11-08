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
