from .commands import (
    call_command_handler,
    has_command_handler,
    parse_substitute_args,
    parse_substitute_range,
    substitute,
)
from .grammar import COMMAND_GRAMMAR

__all__ = ('handle_command',)


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

    variables = m.variables()
    command = variables.get('command')
    go_to_line = variables.get('go_to_line')
    shell_command = variables.get('shell_command')
    sub_range = variables.get('sub_range')
    sub_args = variables.get('sub_args')

    # Call command handler.

    if go_to_line is not None:
        # Handle go-to-line.
        _go_to_line(editor, go_to_line)

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


def _go_to_line(editor, line):
    """
    Move cursor to this line in the current buffer.
    """
    b = editor.application.current_buffer
    if b is None:
        return
    b.cursor_position = b.document.translate_row_col_to_index(max(0, int(line) - 1), 0)
