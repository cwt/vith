from prompt_toolkit.completion import Completer, Completion, PathCompleter, WordCompleter
from prompt_toolkit.contrib.completers.system import SystemCompleter
from prompt_toolkit.contrib.regular_languages.completion import GrammarCompleter

from .commands import SET_COMMANDS, get_commands
from .grammar import COMMAND_GRAMMAR

__all__ = ('create_command_completer',)


def create_command_completer(editor):
    commands = [c + ' ' for c in get_commands()]

    return GrammarCompleter(
        COMMAND_GRAMMAR,
        {
            'command': WordCompleter(commands),
            'location': PathCompleter(expanduser=True),
            'set_option': WordCompleter(sorted(SET_COMMANDS)),
            'buffer_name': BufferNameCompleter(editor),
            'colorscheme': ColorSchemeCompleter(editor),
            'shell_command': SystemCompleter(),
        },
    )


class BufferNameCompleter(Completer):
    """
    Complete on buffer names.
    Prefix matches are yielded first, followed by substring matches when a
    non-empty query is typed.
    """

    def __init__(self, editor):
        self.editor = editor

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        start_pos = -len(text)
        substring_matches = []

        for eb in self.editor.window_arrangement.editor_buffers:
            location = eb.location
            if location is None:
                continue
            if location.startswith(text):
                yield Completion(location, start_position=start_pos, display=location)
            elif text and text in location:
                substring_matches.append(location)

        for location in substring_matches:
            yield Completion(location, start_position=start_pos, display=location)


class ColorSchemeCompleter(Completer):
    """
    Complete on the names of the color schemes that are currently known to the
    Editor instance.
    """

    def __init__(self, editor):
        self.editor = editor

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        start_pos = -len(text)

        for style_name in self.editor.styles:
            if style_name.startswith(text):
                yield Completion(style_name, start_position=start_pos, display=style_name)
