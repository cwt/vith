import re
from functools import lru_cache

from prompt_toolkit.completion import Completer, Completion, PathCompleter, WordCompleter
from prompt_toolkit.contrib.completers.system import SystemCompleter
from prompt_toolkit.contrib.regular_languages.completion import GrammarCompleter
from prompt_toolkit.document import Document

from .grammar import COMMAND_GRAMMAR
from .options import SET_EXTRA_TOGGLE_ON_NAMES, SET_TOGGLE_GROUPS
from .registry import SET_COMMANDS, get_commands

__all__ = ('create_command_completer', 'FiletypeCompleter')

#: Matches the value part of `:set ... filetype=<prefix>` (and the `ft` alias),
#: with optional leading colon/whitespace (command-line buffer omits the
#: `:` prompt, `handle_command` strings include it).
_FILETYPE_VALUE_RE = re.compile(
    r'^\s*:?\s*(?:set|setlocal|setl)\s+(?:.*\s)?(?:filetype|ft)=([^\s]*)$'
)
#: Matches the trailing option token of `:set` / `:setlocal` / `:setl` (single
#: or multi-argument).
_SET_TOKEN_RE = re.compile(r'^\s*:?\s*(?:set|setlocal|setl)\s+(?:.*\s)?([^\s]*)$')


def _set_option_candidates(prefix: str) -> list[str]:
    """Return sorted `:set` option completion candidates for `prefix`."""
    candidates: set[str] = set(SET_COMMANDS.keys())
    candidates.add('all')
    if prefix.startswith('inv'):
        for _display, _attr, on_names, _off in SET_TOGGLE_GROUPS:
            for name in on_names:
                candidates.add('inv' + name)
        for name in SET_EXTRA_TOGGLE_ON_NAMES:
            candidates.add('inv' + name)
    return sorted(name for name in candidates if name.startswith(prefix))


@lru_cache(maxsize=1)
def _filetype_aliases():
    """Sorted Pygments lexer aliases (`get_lexer_by_name` targets)."""
    from pygments.lexers import get_all_lexers

    aliases = set()
    for _, alias_list, _, _ in get_all_lexers():
        aliases.update(alias_list)
    return sorted(aliases)


@lru_cache(maxsize=1)
def _filetype_display_names() -> dict[str, str]:
    """Map Pygments alias -> human-readable lexer name for completion meta."""
    from pygments.lexers import get_all_lexers

    mapping: dict[str, str] = {}
    for name, alias_list, _, _ in get_all_lexers():
        for alias in alias_list:
            mapping.setdefault(alias, name)
    return mapping


class FiletypeCompleter(Completer):
    """
    Complete Pygments lexer aliases for `:set filetype=`.

    Empty prefix yields all aliases; otherwise only `startswith` matches
    (mirroring `ColorSchemeCompleter`).
    """

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        start_pos = -len(text)
        display_names = _filetype_display_names()

        for alias in _filetype_aliases():
            if alias.startswith(text):
                yield Completion(
                    alias,
                    start_position=start_pos,
                    display=alias,
                    display_meta=display_names.get(alias, ''),
                )


class CommandCompleter(Completer):
    """
    Dispatch `:set filetype=<tab>` to `FiletypeCompleter` and multi-argument
    `:set` option tokens (including `all` and `inv*`) before delegating
    everything else to the grammar completer.
    """

    def __init__(self, inner):
        self.inner = inner
        self.filetype_completer = FiletypeCompleter()

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        m = _FILETYPE_VALUE_RE.match(text)
        if m is not None:
            prefix = m.group(1)
            sub_document = Document(prefix, len(prefix))
            yield from self.filetype_completer.get_completions(sub_document, complete_event)
            return

        set_match = _SET_TOKEN_RE.match(text)
        if set_match is not None:
            token = set_match.group(1)
            if '=' in token:
                return
            start_pos = -len(token)
            for candidate in _set_option_candidates(token):
                yield Completion(candidate, start_position=start_pos, display=candidate)
            return

        yield from self.inner.get_completions(document, complete_event)


def create_command_completer(editor):
    commands = [c + ' ' for c in get_commands()]

    inner = GrammarCompleter(
        COMMAND_GRAMMAR,
        {
            'command': WordCompleter(commands),
            'location': PathCompleter(expanduser=True),
            'write_append': PathCompleter(expanduser=True),
            'set_option': WordCompleter(sorted(SET_COMMANDS)),
            'buffer_name': BufferNameCompleter(editor),
            'colorscheme': ColorSchemeCompleter(editor),
            'shell_command': SystemCompleter(),
            'read_shell': SystemCompleter(),
            'filter_shell': SystemCompleter(),
            'write_shell': SystemCompleter(),
        },
    )
    return CommandCompleter(inner)


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
