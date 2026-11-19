from .commands import (
    call_command_handler,
    filter_range_command,
    has_command_handler,
    parse_substitute_args,
    parse_substitute_range,
    substitute,
)
from .grammar import COMMAND_GRAMMAR
from .ranges import NO_SUCH_LINE_TEXT, resolve_line_address

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
