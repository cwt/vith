import os
import shlex
from typing import Any

from .files import filter_range_command
from .grammar import COMMAND_GRAMMAR
from .ranges import NO_SUCH_LINE_TEXT, resolve_line_address
from .registry import call_command_handler, has_command_handler
from .substitution import parse_substitute_args, parse_substitute_range, substitute

__all__ = ('expand_command_filenames', 'handle_command')

_FILENAME_MODIFIERS = frozenset(('p', 'h', 't', 'r', 'e'))
_LOCATION_VARIABLE_NAMES = ('location', 'write_append')
_SHELL_VARIABLE_NAMES = ('shell_command', 'read_shell', 'filter_shell', 'write_shell')


class _ExpandedVariables:
    """Wrapper over grammar match variables with expanded `%`/`#` values."""

    def __init__(self, base: Any, overrides: dict[str, str]) -> None:
        self._base = base
        self._overrides = overrides

    def get(self, key: str, default: Any = None) -> Any:
        if key in self._overrides:
            return self._overrides[key]
        return self._base.get(key, default)

    def __getitem__(self, key: str) -> Any:
        if key in self._overrides:
            return self._overrides[key]
        return self._base[key]


def _apply_filename_modifier(path: str, modifier: str) -> str:
    """Apply a single Vim filename modifier (`:p`, `:h`, `:t`, `:r`, `:e`)."""
    if modifier == 'p':
        return os.path.abspath(os.path.expanduser(path))
    if modifier == 'h':
        head = os.path.dirname(path)
        return head if head else '.'
    if modifier == 't':
        return os.path.basename(path)
    if modifier == 'r':
        return os.path.splitext(path)[0]
    if modifier == 'e':
        return os.path.splitext(path)[1].lstrip('.')
    return path


def expand_command_filenames(
    text: str, editor: Any, *, shell_escape: bool = False
) -> tuple[str | None, str | None]:
    """
    Expand Vim command-line special filenames (`%`, `#`) and chainable
    modifiers (`:p`, `:h`, `:t`, `:r`, `:e`) inside `text`.

    Escaped `\\%` and `\\#` pass through literally. Returns `(expanded, None)`
    on success or `(None, error_message)` (`E499` / `E194`) when a referenced
    buffer has no file name.
    """
    if '%' not in text and '#' not in text:
        return text, None

    out: list[str] = []
    i = 0
    length = len(text)

    while i < length:
        ch = text[i]
        if ch == '\\' and i + 1 < length and text[i + 1] in ('%', '#'):
            out.append(text[i + 1])
            i += 2
            continue

        if ch in ('%', '#'):
            wa = editor.window_arrangement
            if ch == '%':
                eb = wa.active_editor_buffer
                raw_path = eb.location if eb is not None else None
                if not raw_path:
                    return None, "E499: Empty file name for '%'"
            else:
                alt = getattr(wa, 'alternate_editor_buffer', None)
                raw_path = alt.location if alt is not None else None
                if not raw_path:
                    return None, "E194: No alternate file name to substitute for '#'"

            value = raw_path
            i += 1
            while i + 1 < length and text[i] == ':' and text[i + 1] in _FILENAME_MODIFIERS:
                value = _apply_filename_modifier(value, text[i + 1])
                i += 2

            if shell_escape:
                value = shlex.quote(value)
            out.append(value)
            continue

        out.append(ch)
        i += 1

    return ''.join(out), None


def _expand_variables(variables: Any, editor: Any) -> tuple[Any, str | None]:
    """
    Expand `%`/`#` in location and shell grammar variables, returning either
    `(wrapped_variables, None)` or `(None, error_message)`.
    """
    overrides: dict[str, str] = {}

    for key in _LOCATION_VARIABLE_NAMES:
        raw = variables.get(key)
        if raw is not None:
            expanded, err = expand_command_filenames(raw, editor, shell_escape=False)
            if err is not None or expanded is None:
                return None, err
            if expanded != raw:
                overrides[key] = expanded

    for key in _SHELL_VARIABLE_NAMES:
        raw = variables.get(key)
        if raw is not None:
            expanded, err = expand_command_filenames(raw, editor, shell_escape=True)
            if err is not None or expanded is None:
                return None, err
            if expanded != raw:
                overrides[key] = expanded

    if not overrides:
        return variables, None
    return _ExpandedVariables(variables, overrides), None


def handle_command(editor, input_string):
    """
    Handle commands entered on the Vi command line.
    """
    # Treat empty/whitespace/colon-only input as a no-op.
    if not input_string or not input_string.lstrip(':').strip():
        return

    # Match with grammar and extract variables.
    m = COMMAND_GRAMMAR.match(input_string)
    if m is None:
        editor.show_message('Unknown command: %s' % input_string)
        return

    variables, expand_err = _expand_variables(m.variables(), editor)
    if expand_err is not None:
        editor.show_message(expand_err)
        return

    command = variables.get('command')
    go_to_line = variables.get('go_to_line')
    shell_command = variables.get('shell_command')
    filter_shell = variables.get('filter_shell')
    sub_range = variables.get('sub_range')
    sub_args = variables.get('sub_args')

    # Call command handler.

    if go_to_line is not None:
        # Handle go-to-line.
        _go_to_line(editor, go_to_line)

    elif filter_shell is not None:
        # Handle `:[range]!{cmd}` external filter.
        filter_range_command(editor, variables.get('filter_range'), filter_shell)

    elif shell_command is not None:
        # Handle shell commands.
        editor.application.run_system_command(shell_command)

    elif has_command_handler(command):
        # Handle other 'normal' commands.
        call_command_handler(command, editor, variables)

    elif command in ('s', 'substitute'):
        range_start, range_end = parse_substitute_range(sub_range)
        search, replace, flags = parse_substitute_args(sub_args)
        substitute(editor, range_start, range_end, search, replace, flags)

    elif not command:
        return

    else:
        # For unknown commands, show error message.
        editor.show_message('Not an editor command: %s' % input_string)
        return

    # After execution of commands, make sure to update the layout and focus
    # stack.
    editor.sync_with_prompt_toolkit()


def _go_to_line(editor, address):
    """
    Move cursor to the addressed line (`:<N>`, `:$`, `:.`, `:+N`, `:-N`, `:'<`,
    `:'>`) in the active buffer.
    """
    eb = editor.window_arrangement.active_editor_buffer
    if eb is not None:
        buff = eb.buffer
        visual_marks = getattr(eb, 'visual_marks', None)
    else:
        buff = editor.application.current_buffer
        visual_marks = None
    if buff is None:
        return

    doc = buff.document
    line_count = len(doc.lines)
    row, err = resolve_line_address(
        str(address),
        doc.cursor_position_row,
        line_count,
        visual_marks,
    )
    if err is not None or row is None:
        editor.show_message(err or NO_SUCH_LINE_TEXT)
        return

    addr_text = str(address).strip()
    if addr_text.isdigit():
        # Bare numeric line jump (`:999`, `:0`) clamps to [0, line_count - 1] like Vim.
        target_row = max(0, min(row, line_count - 1))
    elif 0 <= row < line_count:
        target_row = row
    else:
        editor.show_message(NO_SUCH_LINE_TEXT)
        return

    line_text = doc.lines[target_row]
    first_non_blank = len(line_text) - len(line_text.lstrip())
    buff.cursor_position = doc.translate_row_col_to_index(target_row, first_non_blank)
