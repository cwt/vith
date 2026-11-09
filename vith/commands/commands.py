import os
import re
from typing import Any

from prompt_toolkit.application import run_in_terminal
from prompt_toolkit.document import Document

from vith.cold_text import format_cold_text_stats
from vith.thai import note_document_text

__all__ = (
    'has_command_handler',
    'call_command_handler',
)


COMMANDS_TO_HANDLERS: dict[str, Any] = {}  # Global mapping Vi commands to their handler.
COMMANDS_TAKING_LOCATIONS: set[str] = set()  # Name of commands that accept locations.
SET_COMMANDS: dict[str, Any] = {}  # Mapping ':set'-commands to their handler.
SET_COMMANDS_TAKING_VALUE: set[str] = set()


_NO_WRITE_SINCE_LAST_CHANGE_TEXT = 'No write since last change (add ! to override)'
_NO_FILE_NAME = 'No file name'


def _confirm_unsaved_changes(editor, force):
    """
    Standard unsaved-changes guard for buffer switch/close/edit commands
    (shared by `:b`, `:bn`, `:bp`, `:bd`/`:bw` and `:e`; docs/bugs/073.md).

    Returns `True` when the operation should proceed.
    """
    eb = editor.window_arrangement.active_editor_buffer
    if not force and eb.has_unsaved_changes:
        editor.show_message(_NO_WRITE_SINCE_LAST_CHANGE_TEXT)
        return False
    return True


def _run_pager(print_handler):
    """
    Run `print_handler` in a terminal pager and wait for Enter afterwards
    (shared by `:ls`, `:compression` and the rc-file loader; docs/bugs/073.md).
    """

    def pager_handler():
        print_handler()
        input('\nPress ENTER to continue...')

    run_in_terminal(pager_handler)


def has_command_handler(command):
    return command in COMMANDS_TO_HANDLERS


def call_command_handler(command, editor, variables):
    """
    Execute command.
    """
    COMMANDS_TO_HANDLERS[command](editor, variables)


def get_commands():
    return COMMANDS_TO_HANDLERS.keys()


def get_commands_taking_locations():
    return COMMANDS_TAKING_LOCATIONS


# Decorators


def _cmd(name):
    """
    Base decorator for registering commands in this namespace.
    """

    def decorator(func):
        COMMANDS_TO_HANDLERS[name] = func
        return func

    return decorator


def _unwrap_command(func):
    """
    Return the base command function for a possibly already-wrapped handler.
    """
    return getattr(func, "_vith_wrapped_command", func)


def location_cmd(name, accepts_force=False):
    """
    Decorator that registers a command that takes a location as (optional)
    parameter.
    """
    COMMANDS_TAKING_LOCATIONS.add(name)

    def decorator(func):
        base = _unwrap_command(func)

        def command_wrapper(editor, variables):
            location = variables.get('location')
            force = bool(variables['force'])

            if force and not accepts_force:
                editor.show_message('No ! allowed')
            elif accepts_force:
                base(editor, location, force=force)
            else:
                base(editor, location)

        command_wrapper._vith_wrapped_command = base
        COMMANDS_TO_HANDLERS[name] = command_wrapper
        # The base function is returned so the module-local alias keeps the
        # original signature (`write_and_quit` still calls `write(...)`);
        # stacked decorators normalize on `base` via `_unwrap_command`.
        return func

    return decorator


def cmd(name, accepts_force=False):
    """
    Decorator that registers a command that doesn't take any parameters.
    """

    def decorator(func):
        base = _unwrap_command(func)

        def command_wrapper(editor, variables):
            force = bool(variables['force'])

            if force and not accepts_force:
                editor.show_message('No ! allowed')
            elif accepts_force:
                base(editor, force=force)
            else:
                base(editor)

        command_wrapper._vith_wrapped_command = base
        COMMANDS_TO_HANDLERS[name] = command_wrapper
        # See `location_cmd`: the base is returned for module-local calls;
        # stacks bind the unwrapped base (docs/bugs/075.md).
        return func

    return decorator


def set_cmd(name, accepts_value=False):
    """
    Decorator that registers a ':set'-command.
    """

    def decorator(func):
        SET_COMMANDS[name] = func
        if accepts_value:
            SET_COMMANDS_TAKING_VALUE.add(name)
        return func

    return decorator


# Actual command implementations


@_cmd('set')
def set_command_execute(editor, variables):
    """
    Execute a ':set' command.
    """
    option = variables.get('set_option')
    value = variables.get('set_value')

    if option in SET_COMMANDS:
        # Call the correct handler.
        if option in SET_COMMANDS_TAKING_VALUE:
            SET_COMMANDS[option](editor, value)
        else:
            SET_COMMANDS[option](editor)
    else:
        editor.show_message('Unknown option: %s' % option)


@cmd('bn', accepts_force=True)
def _bn(editor, force=False):
    """
    Go to next buffer.
    """
    if _confirm_unsaved_changes(editor, force):
        editor.window_arrangement.go_to_next_buffer()


@cmd('bp', accepts_force=True)
def _bp(editor, force=False):
    """
    Go to previous buffer.
    """
    if _confirm_unsaved_changes(editor, force):
        editor.window_arrangement.go_to_previous_buffer()


@cmd('only')
def only(editor):
    """
    Keep only the current window.
    """
    editor.window_arrangement.keep_only_current_window()


@cmd('hide')
def hide(editor):
    """
    Hide the current window.
    """
    editor.window_arrangement.close_window()


@location_cmd('sp')
@location_cmd('split')
def horizontal_split(editor, location):
    """
    Split window horizontally.
    """
    editor.window_arrangement.hsplit(location or None)


@location_cmd('vsp')
@location_cmd('vsplit')
def vertical_split(editor, location):
    """
    Split window vertically.
    """
    editor.window_arrangement.vsplit(location or None)


@cmd('new')
def new_buffer(editor):
    """
    Create new buffer.
    """
    editor.window_arrangement.hsplit(new=True)


@cmd('vnew')
def new_vertical_buffer(editor):
    """
    Create new buffer, splitting vertically.
    """
    editor.window_arrangement.vsplit(new=True)


@location_cmd('badd')
def buffer_add(editor, location):
    """
    Add a new buffer.
    """
    editor.window_arrangement.open_buffer(location)


@cmd('buffers')
def buffer_list(editor):
    """
    List all buffers.
    """

    def handler():
        for line in buffer_list_lines(editor):
            print(line)

    _run_pager(handler)


def buffer_list_lines(editor):
    """
    Build the `:ls`/`:buffers` pager rows: 1-based buffer numbers (matching
    `:b <n>` and the overlay, docs/bugs/087.md) with `[New file]` for
    untitled buffers.
    """
    wa = editor.window_arrangement
    lines = []
    for info in wa.list_open_buffers():
        char = '%' if info.is_active else ''
        eb = info.editor_buffer
        display = eb.get_display_name()
        lines.append(
            ' %3i %-2s %-20s  line %i'
            % (
                info.index + 1,
                char,
                display,
                (eb.buffer.document.cursor_position_row + 1),
            )
        )
    return lines


@cmd('compression')
@cmd('comp')
@cmd('coldtext')
def cold_text_compression(editor):
    """
    Show compressed undo/redo history size and ratio.
    """
    eb = editor.window_arrangement.active_editor_buffer
    buffer = eb.buffer if eb is not None else None
    stats_getter = getattr(buffer, 'cold_text_stats', None)

    if stats_getter is None:
        editor.show_message('Cold-text compression is not available for this buffer.')
        return

    stats = stats_getter()

    def handler():
        for line in format_cold_text_stats(stats):
            print(line)

    _run_pager(handler)


@_cmd('b')
@_cmd('buffer')
def _buffer(editor, variables):
    """
    Go to one of the open buffers.
    """
    buffer_name = variables.get('buffer_name')
    if buffer_name and _confirm_unsaved_changes(editor, bool(variables['force'])):
        editor.window_arrangement.go_to_buffer(buffer_name)


@cmd('bw', accepts_force=True)
@cmd('bd', accepts_force=True)
def buffer_wipe(editor, force=False):
    """
    Wipe buffer.
    """
    if _confirm_unsaved_changes(editor, force):
        editor.window_arrangement.close_buffer()


@location_cmd('o', accepts_force=True)
@location_cmd('open', accepts_force=True)
@location_cmd('e', accepts_force=True)
@location_cmd('edit', accepts_force=True)
def buffer_edit(editor, location, force=False):
    """
    Edit new buffer.
    """
    if location is None:
        # Edit/open without a location will reload the current file, if there are
        # no changes.
        eb = editor.window_arrangement.active_editor_buffer
        if eb.location is None:
            editor.show_message(_NO_FILE_NAME)
        elif _confirm_unsaved_changes(editor, force):
            eb.reload()
    elif _confirm_unsaved_changes(editor, force):
        editor.window_arrangement.open_buffer(location, show_in_current_window=True)


@cmd('q', accepts_force=True)
@cmd('quit', accepts_force=True)
def quit(editor, all_=False, force=False):
    """
    Quit. With several windows/tabs open this closes the current window (Vim
    semantics); the editor exits only on the last window.
    """
    window_arrangement = editor.window_arrangement
    ebs = window_arrangement.editor_buffers
    has_multiple_windows = (
        len(window_arrangement.tab_pages) > 1 or window_arrangement.active_tab.window_count() > 1
    )

    # ":q" closes the current window (or the whole tab when it has a single
    # window); hidden buffers or dirty buffers in other windows do not block it
    # (docs/bugs/067.md, docs/bugs/095.md).
    if not all_ and has_multiple_windows:
        active_eb = window_arrangement.active_editor_buffer
        if not force and active_eb is not None and active_eb.has_unsaved_changes:
            editor.show_message(_NO_WRITE_SINCE_LAST_CHANGE_TEXT)
            return
        window_arrangement.close_window()
        return

    # When closing the last window or quitting all (:qa), any unsaved buffer blocks exit.
    if not force and any(eb.has_unsaved_changes for eb in ebs):
        editor.show_message(_NO_WRITE_SINCE_LAST_CHANGE_TEXT)
        return

    editor.application.exit()


@cmd('qa', accepts_force=True)
@cmd('qall', accepts_force=True)
def quit_all(editor, force=False):
    """
    Quit all.
    """
    quit(editor, all_=True, force=force)


@location_cmd('w', accepts_force=True)
@location_cmd('write', accepts_force=True)
def write(editor, location, force=False):
    """
    Write file. Returns whether the buffer was written.
    """
    if location and not force and os.path.exists(os.path.expanduser(location)):
        editor.show_message('File exists (add ! to override)')
        return False
    eb = editor.window_arrangement.active_editor_buffer
    if location is None and eb.location is None:
        editor.show_message(_NO_FILE_NAME)
        return False
    return eb.write(location)


@location_cmd('wq', accepts_force=True)
def write_and_quit(editor, location, force=False):
    """
    Write file and quit. Does not quit when the write failed.
    """
    if write(editor, location, force=force):
        editor.application.exit()


@cmd('cq')
def quit_nonzero(editor):
    """
    Quit with non zero exit status.
    """
    # Note: the try/finally in `prompt_toolkit.Interface.read_input`
    # will ensure that the render output is reset, leaving the alternate
    # screen before quitting.
    editor.application.exit()


@cmd('wqa')
def write_and_quit_all(editor):
    """
    Write every buffer with unsaved changes and quit. Does not quit when any
    write failed (docs/bugs/067.md).
    """
    for eb in editor.window_arrangement.editor_buffers:
        if eb.has_unsaved_changes and not eb.write():
            return

    editor.application.exit()


@cmd('h')
@cmd('help')
def help(editor):
    """
    Show help.
    """
    editor.show_help()


@location_cmd('tabe')
@location_cmd('tabedit')
@location_cmd('tabnew')
def tab_new(editor, location):
    """
    Create new tab page.
    """
    editor.window_arrangement.create_tab(location or None)


@cmd('tabclose')
@cmd('tabc')
def tab_close(editor):
    """
    Close tab page.
    """
    editor.window_arrangement.close_tab()


@cmd('tabnext')
@cmd('tabn')
def tab_next(editor):
    """
    Go to next tab.
    """
    editor.window_arrangement.go_to_next_tab()


@cmd('tabprevious')
@cmd('tabp')
def tab_previous(editor):
    """
    Go to previous tab.
    """
    editor.window_arrangement.go_to_previous_tab()


@cmd('pwd')
def pwd(editor):
    "Print working directory."
    directory = os.getcwd()
    editor.show_message('{}'.format(directory))


@location_cmd('cd', accepts_force=False)
def cd(editor, location):
    "Change working directory."
    target = os.path.expanduser(location or '~')
    try:
        os.chdir(target)
    except OSError as e:
        editor.show_message('{}'.format(e))


@_cmd('colorscheme')
@_cmd('colo')
def color_scheme(editor, variables):
    """
    Apply a colorscheme by name.
    """
    colorscheme = variables.get('colorscheme')
    if colorscheme:
        editor.use_colorscheme(colorscheme)


def _set_int_option(editor, value, attribute, display_name, minimum):
    """
    Shared implementation of the integer-valued `:set` handlers.

    `value is None` displays the current value; invalid input shows vim-like
    error messages; otherwise the attribute is set when `value >= minimum`.
    """
    if value is None:
        editor.show_message('%s=%i' % (display_name, getattr(editor, attribute)))
        return
    try:
        number = int(value)
    except ValueError:
        editor.show_message('Number required after =')
        return
    if number < minimum:
        editor.show_message('Argument must be positive')
    else:
        setattr(editor, attribute, number)


#: Canonical `:set` option catalog — the single source of truth for option
#: names, editor attributes and validation bounds. Both the generated
#: `:set` handlers below and the command-line previewer
#: (`vith/commands/preview.py`) are built from this table (docs/bugs/073.md
#: finalizing the 028/partial-073 Table-6 dedup).
#: (option, editor attribute, ON aliases, OFF aliases)
SET_TOGGLE_GROUPS = (
    ('number', 'show_line_numbers', ('nu', 'number'), ('nonu', 'nonumber')),
    ('hlsearch', 'highlight_search', ('hlsearch', 'hls'), ('nohlsearch', 'nohls')),
    ('paste', 'paste_mode', ('paste',), ('nopaste',)),
    ('ruler', 'show_ruler', ('ruler', 'ru'), ('noruler', 'noru')),
    ('wildmenu', 'show_wildmenu', ('wildmenu', 'wmnu'), ('nowildmenu', 'nowmnu')),
    ('expandtab', 'expand_tab', ('expandtab', 'et'), ('noexpandtab', 'noet')),
    ('incsearch', 'incsearch', ('incsearch', 'is'), ('noincsearch', 'nois')),
    ('ignorecase', 'ignore_case', ('ignorecase', 'ic'), ('noignorecase', 'noic')),
    ('list', 'display_unprintable_characters', ('list',), ('nolist',)),
    ('jedi', 'enable_jedi', ('jedi',), ('nojedi',)),
    ('relativenumber', 'relative_number', ('relativenumber', 'rnu'), ('norelativenumber', 'nornu')),
    ('wrap', 'wrap_lines', ('wrap',), ('nowrap',)),
    ('breakindent', 'break_indent', ('breakindent', 'bri'), ('nobreakindent', 'nobri')),
    ('mouse', 'enable_mouse_support', ('mouse',), ('nomouse',)),
    ('cursorline', 'cursorline', ('cursorline', 'cul'), ('nocursorline', 'nocul')),
    ('cursorcolumn', 'cursorcolumn', ('cursorcolumn', 'cuc'), ('nocursorcolumn', 'nocuc')),
)

#: (display name, editor attribute, aliases, minimum)
SET_INT_OPTIONS = (
    ('tabstop', 'tabstop', ('tabstop', 'ts'), 1),
    ('scrolloff', 'scroll_offset', ('scrolloff', 'so'), 0),
)

#: (display name, editor attribute, aliases) — comma-separated integer list.
SET_LIST_OPTIONS = (('colorcolumn', 'colorcolumn', ('colorcolumn', 'cc')),)


def _register_generated_set_options() -> None:
    """Materialize the `:set` catalog into `SET_COMMANDS` (docs/bugs/073.md)."""
    for _display, attribute, on_names, off_names in SET_TOGGLE_GROUPS:

        def _on(editor, _attribute=attribute):
            setattr(editor, _attribute, True)

        def _off(editor, _attribute=attribute):
            setattr(editor, _attribute, False)

        for name in on_names:
            SET_COMMANDS[name] = _on
        for name in off_names:
            SET_COMMANDS[name] = _off

    for display, attribute, names, minimum in SET_INT_OPTIONS:

        def _int_option(editor, value, _display=display, _attribute=attribute, _minimum=minimum):
            _set_int_option(editor, value, _attribute, _display, _minimum)

        for name in names:
            SET_COMMANDS[name] = _int_option
            SET_COMMANDS_TAKING_VALUE.add(name)

    for _display, attribute, names in SET_LIST_OPTIONS:

        def _list_option(editor, value, _attribute=attribute):
            try:
                if value:
                    numbers = [int(val) for val in value.split(',')]
                else:
                    numbers = []
            except ValueError:
                editor.show_message('Invalid value. Expecting comma separated list of integers')
            else:
                setattr(editor, _attribute, numbers)

        for name in names:
            SET_COMMANDS[name] = _list_option
            SET_COMMANDS_TAKING_VALUE.add(name)


_register_generated_set_options()


@set_cmd('tildeop')
@set_cmd('top')
def enable_tildeop(editor):
    """Enable tilde operator."""
    editor.application.vi_state.tilde_operator = True


@set_cmd('notildeop')
@set_cmd('notop')
def disable_tildeop(editor):
    """Disable tilde operator."""
    editor.application.vi_state.tilde_operator = False


def substitute(editor, range_start, range_end, search, replace, flags):
    """Substitute /search/ with /replace/ over a range of text"""

    search_state = editor.application.current_search_state
    eb = editor.window_arrangement.active_editor_buffer
    if eb is None:
        return
    buffer = eb.buffer
    cursor_position_row = buffer.document.cursor_position_row

    # Resolve search pattern from history when omitted.
    if not search:
        search = search_state.text
    if not search:
        editor.show_message('No previous regular expression')
        return

    if replace is None:
        replace = editor.last_substitute_text

    # Parse and validate flags (docs/bugs/083.md, docs/bugs/094.md).
    clean_flags = flags.replace('/', '')
    ignore_case = bool(getattr(editor, 'ignore_case', False))
    sub_count = 1

    for ch in clean_flags:
        match ch:
            case 'g':
                sub_count = 0
            case 'i':
                ignore_case = True
            case 'I':
                ignore_case = False
            case _:
                editor.show_message(f'E488: Trailing characters: {ch}')
                return

    # Compile the regex once; reject malformed patterns up front.
    regex_flags = re.IGNORECASE if ignore_case else 0
    try:
        pattern = re.compile(search, flags=regex_flags)
    except re.error as e:
        editor.show_message('Invalid regex: %s' % e)
        return

    # Convert and validate the captured range. (`:0,1s` maps `0` to the
    # first line, like Vim — E19 was raised for the 0-row trick before,
    # docs/bugs/083.md.)
    if not range_start:
        assert not range_end
        range_start_row = range_end_row = cursor_position_row
    else:
        range_start_row = max(0, int(range_start) - 1)
        range_end_row = max(0, int(range_end) - 1) if range_end else range_start_row
    if not 0 <= range_start_row <= range_end_row < len(buffer.document.lines):
        editor.show_message('E19: No such line in current buffer')
        return
    line_numbers = list(range(range_start_row, range_end_row + 1))

    # Transform each line once, tracking the last substituted row and
    # rejecting invalid replacement escapes without mutating the buffer.
    buffer_lines = buffer.document.lines
    substituted = {}
    last_substituted_row = None
    any_match = False
    regex_error = None
    for row in line_numbers:
        original = buffer_lines[row]
        try:
            result = pattern.sub(replace, original, count=sub_count)
        except re.error as e:
            regex_error = e
            break
        substituted[row] = result
        # Track whether the pattern matched on this row, not whether the
        # text changed: a substitution whose replacement is identical to the
        # matched text (e.g. `:s/foo/foo/`) is still a successful match.
        if pattern.search(original) is not None:
            any_match = True
            last_substituted_row = row

    if regex_error is not None:
        editor.show_message('Invalid regex: %s' % regex_error)
        return

    if not any_match:
        editor.show_message('Pattern not found')
        return

    # Apply substitution.
    buffer.save_to_undo_stack()
    # Reuse the already-split lines: only the shallow per-row list copy is
    # needed (ptk caches `Document.lines` per document; docs/bugs/072.md row
    # 5 as corrected in 090's audit).
    lines = list(buffer_lines)
    for row in line_numbers:
        lines[row] = substituted[row]
    new_text = "\n".join(lines)

    doc = Document(new_text)
    buffer.document = doc
    # The renderer gate: `_on_text_changed` notes only the *cursor line*,
    # and at `document =` time the synthetic Document cursor is still at the
    # end of `new_text` — substitute/paste-class mass edits introducing Thai
    # on other rows could leave the 002/062-era sanitizer disarmed while the
    # screen shows Thai (docs/bugs/081.md). Note the full replaced text.
    note_document_text(new_text)
    row = last_substituted_row
    buffer.cursor_position = doc.translate_row_col_to_index(row, 0) + (
        len(doc.lines[row]) - len(doc.lines[row].lstrip())
    )
    # (The former private `buffer._search(...)` call here discarded its
    # result and did nothing; the cursor targets the last substituted row
    # directly. docs/bugs/071.md)

    # update editor state
    editor.last_substitute_text = replace
    search_state.text = search
