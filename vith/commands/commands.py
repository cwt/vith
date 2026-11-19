import os
import re
import subprocess
from dataclasses import dataclass
from typing import Any

from vith.thai import note_document_text

from .ranges import (
    MODIFIABLE_OFF_TEXT as _MODIFIABLE_OFF_TEXT,
)
from .ranges import (
    is_buffer_read_only as _is_buffer_read_only,
)
from .ranges import (
    parse_range as parse_substitute_range,
)
from .ranges import (
    replace_buffer_lines,
    resolve_insert_address,
    resolve_range,
)

__all__ = (
    'has_command_handler',
    'call_command_handler',
    'press_enter_to_continue',
    'parse_substitute_range',
)


COMMANDS_TO_HANDLERS: dict[str, Any] = {}  # Global mapping Vi commands to their handler.
COMMANDS_TAKING_LOCATIONS: set[str] = set()  # Name of commands that accept locations.
SET_COMMANDS: dict[str, Any] = {}  # Mapping ':set'-commands to their handler.
SET_COMMANDS_TAKING_VALUE: set[str] = set()


_NO_WRITE_SINCE_LAST_CHANGE_TEXT = 'No write since last change (add ! to override)'
_NO_FILE_NAME = 'No file name'

# Heuristic guard against catastrophic backtracking (docs/bugs/123.md): a
# group that contains a quantifier and is itself quantified, e.g. `(a+)+`,
# `([a-z]+)*$` or `(\w+\s?)+`. Escaped characters and `[...]` classes are
# stripped first, so `[a+]+` and `\(a+\)+` stay allowed.
_UNSAFE_NESTED_QUANTIFIER = re.compile(r'\([^()]*[+*?][^()]*\)[+*]')


def _strip_regex_literals(pattern_text):
    """
    Remove escaped characters and `[...]` classes from `pattern_text`, so
    the nested-quantifier guard only sees regex structure.
    """
    return re.sub(r'\[[^\]]*\]', '', re.sub(r'\\.', '', pattern_text))


def _confirm_unsaved_changes(editor, force):
    """
    Standard unsaved-changes guard for buffer switch/close/edit commands
    (shared by `:b`, `:bn`, `:bp`, `:bd`/`:bw` and `:e`; docs/bugs/073.md).

    Returns `True` when the operation should proceed.
    """
    eb = editor.window_arrangement.active_editor_buffer
    if eb is None:
        return True
    if not force and eb.has_unsaved_changes:
        editor.show_message(_NO_WRITE_SINCE_LAST_CHANGE_TEXT)
        return False
    return True


def press_enter_to_continue():
    """
    Wait for the user to press Enter, degrading gracefully on EOF or
    interrupt (non-TTY stdin; used by `vith.rc_file`, docs/bugs/113.md,
    docs/bugs/115.md).
    """
    try:
        input('\nPress ENTER to continue...')
    except (EOFError, KeyboardInterrupt, OSError):
        pass


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
@_cmd('setlocal')
@_cmd('setl')
def set_command_execute(editor, variables):
    """
    Execute a ':set' or ':setlocal' command.
    """
    option = variables.get('set_option')
    value = variables.get('set_value')
    command = variables.get('command')
    is_local = command in ('setlocal', 'setl')

    if option in SET_COMMANDS:
        # Call the correct handler.
        if option in SET_COMMANDS_TAKING_VALUE:
            if is_local and option in ('tabstop', 'ts'):
                _set_int_option(editor, value, 'tabstop', 'tabstop', 1, is_local=True)
            elif is_local and option in ('filetype', 'ft'):
                set_filetype(editor, value)
            else:
                SET_COMMANDS[option](editor, value)
        else:
            if is_local and option in ('tabstop', 'ts'):
                _set_int_option(editor, None, 'tabstop', 'tabstop', 1, is_local=True)
            elif is_local and option in ('filetype', 'ft'):
                set_filetype(editor, None)
            else:
                SET_COMMANDS[option](editor)
    else:
        editor.show_message('Unknown option: %s' % option)


@cmd('bnext', accepts_force=True)
@cmd('bn', accepts_force=True)
def _bn(editor, force=False):
    """
    Go to next buffer.
    """
    if _confirm_unsaved_changes(editor, force):
        editor.window_arrangement.go_to_next_buffer()


@cmd('bprevious', accepts_force=True)
@cmd('bprev', accepts_force=True)
@cmd('bp', accepts_force=True)
def _bp(editor, force=False):
    """
    Go to previous buffer.
    """
    if _confirm_unsaved_changes(editor, force):
        editor.window_arrangement.go_to_previous_buffer()


@cmd('bfirst', accepts_force=True)
@cmd('bf', accepts_force=True)
def _bfirst(editor, force=False):
    """
    Go to first buffer (`:bf` / `:bfirst`).
    """
    if _confirm_unsaved_changes(editor, force):
        editor.window_arrangement.go_to_first_buffer()


@cmd('blast', accepts_force=True)
@cmd('bl', accepts_force=True)
def _blast(editor, force=False):
    """
    Go to last buffer (`:bl` / `:blast`).
    """
    if _confirm_unsaved_changes(editor, force):
        editor.window_arrangement.go_to_last_buffer()


@cmd('next', accepts_force=True)
@cmd('n', accepts_force=True)
def _next(editor, force=False):
    """
    Go to next file: the next tab page when several are open (keeping the
    tab bar in sync), else the next buffer (docs/bugs/126.md).
    """
    window_arrangement = editor.window_arrangement
    if len(window_arrangement.tab_pages) > 1:
        window_arrangement.go_to_next_tab()
    elif _confirm_unsaved_changes(editor, force):
        window_arrangement.go_to_next_buffer()


@cmd('previous', accepts_force=True)
@cmd('prev', accepts_force=True)
@cmd('N', accepts_force=True)
def _previous(editor, force=False):
    """
    Go to previous file: the previous tab page when several are open,
    else the previous buffer (docs/bugs/126.md).
    """
    window_arrangement = editor.window_arrangement
    if len(window_arrangement.tab_pages) > 1:
        window_arrangement.go_to_previous_tab()
    elif _confirm_unsaved_changes(editor, force):
        window_arrangement.go_to_previous_buffer()


@cmd('on')
@cmd('only')
def only(editor):
    """
    Keep only the current window.
    """
    editor.window_arrangement.keep_only_current_window()


@cmd('clo', accepts_force=True)
@cmd('close', accepts_force=True)
def close_window(editor, force=False):
    """
    Close the current window (without exiting on the last window).

    Matches Vim `:close`: reports E444 on the last remaining window and guards
    unsaved changes when closing the last window showing a dirty buffer unless
    `!` is passed.
    """
    wa = editor.window_arrangement
    active_tab = wa.active_tab
    if active_tab is None:
        return

    has_multiple_windows = len(wa.tab_pages) > 1 or active_tab.window_count() > 1
    if not has_multiple_windows:
        wa.close_window()
        return

    active_eb = wa.active_editor_buffer
    if active_eb is not None:
        visible_count = sum(
            1 for tab in wa.tab_pages for win in tab.windows() if win.editor_buffer is active_eb
        )
        if visible_count <= 1 and not _confirm_unsaved_changes(editor, force):
            return

    wa.close_window()


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


@cmd('ls')
@cmd('files')
@cmd('buffers')
def buffer_list(editor):
    """
    List all open buffers in a read-only split inside vith (`:ls` / `:files` / `:buffers`).
    """
    lines = buffer_list_lines(editor)
    if not lines:
        editor.show_message('No open buffers', record=False)
        return
    editor.show_info('\n'.join(lines), title='[Buffers]')


def buffer_list_lines(editor):
    """
    Build the `:ls`/`:buffers` pager rows: 1-based buffer numbers (matching
    `:b <n>` and the overlay, docs/bugs/087.md) with `[New file]` for
    untitled buffers.
    """
    wa = editor.window_arrangement
    alt_eb = getattr(wa, 'alternate_editor_buffer', None)
    lines = []
    for info in wa.list_open_buffers():
        if info.is_active:
            char = '%'
        elif info.editor_buffer is alt_eb:
            char = '#'
        else:
            char = ''
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
    Show compressed undo/redo history size and ratio in a new window
    (like `:help`, close with `:q`).
    """
    editor.show_compression()


@_cmd('b')
@_cmd('buffer')
def _buffer(editor, variables):
    """
    Go to one of the open buffers.
    """
    buffer_name = variables.get('buffer_name')
    if buffer_name and _confirm_unsaved_changes(editor, bool(variables['force'])):
        editor.window_arrangement.go_to_buffer(buffer_name)


@_cmd('sbuffer')
@_cmd('sb')
def split_buffer(editor, variables):
    """
    Open a horizontal split showing an open buffer (`:sb [N|name]` /
    `:sbuffer [N|name]`), or split the active buffer when omitted.
    """
    wa = editor.window_arrangement
    if wa.active_tab is None:
        return
    buffer_name = variables.get('buffer_name')
    if not buffer_name:
        wa.hsplit()
        return
    target = wa.resolve_buffer(buffer_name)
    if target is not None:
        wa.active_tab.hsplit(target)


@_cmd('bwipeout')
@_cmd('bdelete')
@_cmd('bw')
@_cmd('bd')
def buffer_wipe(editor, variables):
    """
    Wipe the active buffer or a targeted buffer (`:bd[!] [N|name]` /
    `:bw[!] [N|name]`).
    """
    buffer_name = variables.get('buffer_name')
    force = bool(variables['force'])
    if buffer_name:
        wa = editor.window_arrangement
        target = wa.resolve_buffer(buffer_name)
        if target is None:
            return
        if not force and target.has_unsaved_changes:
            editor.show_message(_NO_WRITE_SINCE_LAST_CHANGE_TEXT)
            return
        wa.close_buffer(target=target)
    elif _confirm_unsaved_changes(editor, force):
        editor.window_arrangement.close_buffer()


def _format_file_status(eb) -> str:
    """Format Vim-style `:file` status line for `eb`."""
    display_name = eb.get_display_name()
    flags = []
    if _is_buffer_read_only(eb.buffer):
        flags.append('[readonly]')
    if eb.has_unsaved_changes:
        flags.append('[Modified]')
    if eb.is_new and eb.location is not None:
        flags.append('[New file]')

    doc = eb.buffer.document
    line_count = max(1, len(doc.lines))
    current_line = doc.cursor_position_row + 1
    percent = int((current_line * 100) / line_count)
    line_word = 'line' if line_count == 1 else 'lines'

    flag_part = (' ' + ''.join(flags)) if flags else ''
    return f'"{display_name}"{flag_part} {line_count} {line_word} --{percent}%--'


def _read_location_text(editor, location: str) -> str | None:
    """
    Read text from `location` through `editor.io_backends` without mutating any
    `EditorBuffer` metadata (`location`, `is_new`, `isdir`, `encoding`).
    """
    io = next(
        (backend for backend in editor.io_backends if backend.can_open_location(location)),
        None,
    )
    if io is None:
        editor.show_message(f"E484: Can't open file {location}")
        return None

    if io.exists(location) is False:
        editor.show_message(f"E484: Can't open file {location}")
        return None

    try:
        text, _encoding = io.read(location)
    except Exception as exc:
        editor.show_message(f"E484: Can't open file {location}: {exc}")
        return None
    return text


def _read_shell_text(editor, shell_cmd: str) -> tuple[str | None, int | None]:
    """
    Execute `shell_cmd` capturing combined stdout/stderr (matching Unix Vim's
    `shellredir` `2>&1`, Decision D3). Returns `(output, returncode)` or
    `(None, None)` on launch/timeout failure.
    """
    try:
        proc = subprocess.run(
            shell_cmd,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        editor.show_message(f'Command timed out after 30s: {shell_cmd}')
        return None, None
    except OSError as exc:
        editor.show_message(f'Cannot execute shell command: {exc}')
        return None, None
    return proc.stdout or '', proc.returncode


@_cmd('r')
@_cmd('read')
def read_command(editor, variables):
    """
    Insert a file or shell command output after an addressed line
    (`:[addr]r [file]` / `:[addr]r !{cmd}`).
    """
    eb = editor.window_arrangement.active_editor_buffer
    if eb is None:
        return
    if _is_buffer_read_only(eb.buffer):
        editor.show_message(_MODIFIABLE_OFF_TEXT)
        return

    doc = eb.buffer.document
    insert_index, addr_err = resolve_insert_address(
        variables.get('read_addr'),
        doc.cursor_position_row,
        len(doc.lines),
        getattr(eb, 'visual_marks', None),
    )
    if addr_err is not None or insert_index is None:
        editor.show_message(addr_err)
        return

    read_shell = variables.get('read_shell')
    if read_shell is not None:
        shell_cmd = read_shell.strip()
        if not shell_cmd:
            editor.show_message('E34: No previous command')
            return
        raw_text, returncode = _read_shell_text(editor, shell_cmd)
        if raw_text is None or returncode is None:
            return
        source_label = f'!{shell_cmd}'
    else:
        target = variables.get('location') or eb.location
        if not target:
            editor.show_message(_NO_FILE_NAME)
            return
        raw_text = _read_location_text(editor, target)
        if raw_text is None:
            return
        returncode = 0
        source_label = target

    normalized = raw_text.replace('\r\n', '\n')
    if normalized == '':
        if returncode != 0:
            editor.show_message(f'shell returned {returncode}')
        else:
            editor.show_message(f'"{source_label}" 0L')
        return

    if normalized.endswith('\n'):
        normalized = normalized[:-1]
    inserted_lines = normalized.split('\n')

    if eb.buffer.text == '':
        new_lines = inserted_lines
        first_row = 0
    else:
        new_lines = list(doc.lines)
        new_lines[insert_index:insert_index] = inserted_lines
        first_row = insert_index

    if not replace_buffer_lines(
        editor,
        eb,
        new_lines,
        first_row,
        first_non_blank=True,
        inserted_text=normalized,
    ):
        return

    if returncode != 0:
        editor.show_message(f'shell returned {returncode}')
    else:
        editor.show_message(f'"{source_label}" {len(inserted_lines)}L')


@location_cmd('f', accepts_force=False)
@location_cmd('file', accepts_force=False)
def file_info(editor, location):
    """
    Show current file name/status (`:f` / `:file`), or rename the current
    buffer's location (`:file {name}`) without writing to disk.
    """
    eb = editor.window_arrangement.active_editor_buffer
    if eb is None:
        editor.show_message(_NO_FILE_NAME)
        return

    if location is not None:
        eb.location = location
        eb._detected_filetype = None
        eb.run_reporter()
        note_document_text(eb.location)
        editor.update_title()

    editor.show_message(_format_file_status(eb))


@cmd('enew', accepts_force=True)
@cmd('ene', accepts_force=True)
def buffer_new_empty(editor, force=False):
    """
    Open a new untitled buffer in the current window (`:enew` / `:ene`).
    """
    if _confirm_unsaved_changes(editor, force):
        editor.window_arrangement.open_buffer(None, show_in_current_window=True)


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
        if eb is None or eb.location is None:
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
    active_tab = window_arrangement.active_tab
    has_multiple_windows = len(window_arrangement.tab_pages) > 1 or (
        active_tab is not None and active_tab.window_count() > 1
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
    if eb is None or (location is None and eb.location is None):
        editor.show_message(_NO_FILE_NAME)
        return False
    return eb.write(location, force=force)


@location_cmd('sav', accepts_force=True)
@location_cmd('saveas', accepts_force=True)
def save_as(editor, location, force=False):
    """
    Write the active buffer to `location` and rename the buffer to it
    (`:sav[!] {file}` / `:saveas[!] {file}`).
    """
    if not location:
        editor.show_message('E471: Argument required')
        return False
    if not force and os.path.exists(os.path.expanduser(location)):
        editor.show_message('File exists (add ! to override)')
        return False
    eb = editor.window_arrangement.active_editor_buffer
    if eb is None:
        editor.show_message(_NO_FILE_NAME)
        return False
    return eb.write(location, force=force, rename=True)


@location_cmd('up', accepts_force=True)
@location_cmd('update', accepts_force=True)
def update(editor, location, force=False):
    """
    Write the active buffer only when modified or given a destination path
    (`:up` / `:update`). Returns whether the buffer is cleanly up to date.
    """
    eb = editor.window_arrangement.active_editor_buffer
    if eb is None:
        if location is not None:
            editor.show_message(_NO_FILE_NAME)
            return False
        return True
    if location is not None or eb.has_unsaved_changes:
        return write(editor, location, force=force)
    return True


@location_cmd('x', accepts_force=True)
@location_cmd('xit', accepts_force=True)
@location_cmd('exit', accepts_force=True)
def write_if_modified_and_quit(editor, location, force=False):
    """
    Write the active buffer if modified (or when a location is provided)
    and close the current window / exit (`:x` / `:xit` / `:exit`).
    """
    if not update(editor, location, force=force):
        return
    quit(editor, all_=False, force=force)


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


@cmd('wall', accepts_force=True)
@cmd('wa', accepts_force=True)
def write_all(editor, force=False):
    """
    Write every buffer with unsaved changes (`:wa` / `:wall`).
    Returns whether every dirty buffer was written successfully.
    """
    for eb in editor.window_arrangement.editor_buffers:
        if eb.has_unsaved_changes:
            if eb.location is None:
                editor.show_message(_NO_FILE_NAME)
                return False
            if not eb.write(force=force):
                return False
    return True


@cmd('xall', accepts_force=True)
@cmd('xa', accepts_force=True)
@cmd('wqall', accepts_force=True)
@cmd('wqa', accepts_force=True)
def write_and_quit_all(editor, force=False):
    """
    Write every buffer with unsaved changes and quit. Does not quit when any
    write failed (docs/bugs/067.md).
    """
    if write_all(editor, force=force):
        editor.application.exit()


@cmd('u')
@cmd('undo')
def undo_command(editor):
    """
    Undo the latest change in the active buffer (`:u` / `:undo`).
    """
    eb = editor.window_arrangement.active_editor_buffer
    if eb is None:
        return
    if _is_buffer_read_only(eb.buffer):
        editor.show_message(_MODIFIABLE_OFF_TEXT)
        return

    before_text = eb.buffer.text
    eb.buffer.undo()
    if eb.buffer.text == before_text:
        editor.show_message('Already at oldest change')
    else:
        note_document_text(eb.buffer.text)


@cmd('red')
@cmd('redo')
def redo_command(editor):
    """
    Redo the latest undone change in the active buffer (`:red` / `:redo`).
    """
    eb = editor.window_arrangement.active_editor_buffer
    if eb is None:
        return
    if _is_buffer_read_only(eb.buffer):
        editor.show_message(_MODIFIABLE_OFF_TEXT)
        return

    redo_stack = getattr(eb.buffer, '_redo_stack', None)
    if not redo_stack:
        editor.show_message('Already at newest change')
        return

    before_text = eb.buffer.text
    while redo_stack:
        eb.buffer.redo()
        if eb.buffer.text != before_text:
            note_document_text(eb.buffer.text)
            return

    editor.show_message('Already at newest change')


@cmd('noh')
@cmd('nohlsearch')
def no_hlsearch(editor):
    """
    Temporarily clear search highlighting until the next search (`:noh`).
    """
    editor.suppress_hlsearch()


def version_lines(editor) -> list[str]:
    """
    Build the `:version` pager lines: vith, Python, prompt_toolkit, Thai word
    segmentation backend, and cold-text compression configuration.
    """
    import platform
    import sys

    import prompt_toolkit

    from vith import __version__ as vith_version
    from vith.cold_text import make_cold_text_compressor
    from vith.thai.words import _get_libthai

    thai_backend = 'libthai' if _get_libthai() is not None else 'orthographic fallback (TCC-lite)'

    eb = editor.window_arrangement.active_editor_buffer
    if eb is not None and hasattr(eb.buffer, 'cold_compression_name'):
        comp_name = eb.buffer.cold_compression_name
        comp_level = eb.buffer.cold_compression_level
    else:
        _, comp_name, comp_level = make_cold_text_compressor(
            editor.cold_text_compression,
            editor.cold_text_compression_level,
        )
    comp_text = comp_name if comp_level <= 0 else f'{comp_name} (level {comp_level})'

    return [
        f'vith {vith_version}',
        f'Python {platform.python_version()} ({platform.python_implementation()}) on {sys.platform}',
        f'prompt_toolkit {prompt_toolkit.__version__}',
        f'Thai word segmentation: {thai_backend}',
        f'Cold-text compression: {comp_text}',
    ]


@cmd('ver')
@cmd('version')
def show_version(editor):
    """
    Display vith and runtime environment version info in a split inside vith
    (`:ver` / `:version`).
    """
    editor.show_info('\n'.join(version_lines(editor)), title='[Version]')


def messages_lines(editor) -> list[str]:
    """
    Return the recorded editor message history for `:messages`.
    """
    return list(getattr(editor, '_messages', ()))


@cmd('mes')
@cmd('messages')
def show_messages(editor):
    """
    Display recorded editor messages in a split inside vith (`:mes` / `:messages`).
    """
    lines = messages_lines(editor)
    if not lines:
        editor.show_message('No messages', record=False)
        return
    editor.show_info('\n'.join(lines), title='[Messages]')


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


@cmd('tabonly', accepts_force=True)
@cmd('tabo', accepts_force=True)
def tab_only(editor, force=False):
    """
    Close all tab pages except the current tab (`:tabo` / `:tabonly`).
    """
    editor.window_arrangement.keep_only_current_tab()


@cmd('tabfirst')
@cmd('tabfir')
@cmd('tabrewind')
@cmd('tabr')
def tab_first(editor):
    """
    Go to the first tab page (`:tabfir` / `:tabfirst`).
    """
    editor.window_arrangement.go_to_first_tab()


@cmd('tablast')
@cmd('tabl')
def tab_last(editor):
    """
    Go to the last tab page (`:tabl` / `:tablast`).
    """
    editor.window_arrangement.go_to_last_tab()


@_cmd('tabmove')
@_cmd('tabm')
def tab_move(editor, variables):
    """
    Move the active tab page (`:tabm [N|+N|-N|$]` / `:tabmove`).
    """
    if variables.get('force'):
        editor.show_message('No ! allowed')
        return
    editor.window_arrangement.move_tab(variables.get('tab_arg'))


def tabs_lines(editor) -> list[str]:
    """
    Build Vim-style `:tabs` lines listing all tab pages and their windows.
    """
    wa = editor.window_arrangement
    lines: list[str] = []
    for idx, tab in enumerate(wa.tab_pages):
        is_active_tab = idx == wa.active_tab_index
        lines.append(f'Tab page {idx + 1}')
        for win in tab.windows():
            eb = win.editor_buffer
            active_mark = '>' if (is_active_tab and win is tab.active_window) else ' '
            mod_mark = '+' if eb.has_unsaved_changes else ' '
            lines.append(f'{active_mark} {mod_mark} {eb.get_display_name()}')
    return lines


@cmd('tabs')
def show_tabs(editor):
    """
    Display all tab pages and their windows/buffers in a read-only split
    inside vith (`:tabs`).
    """
    lines = tabs_lines(editor)
    if not lines:
        editor.show_message('No open tabs', record=False)
        return
    editor.show_info('\n'.join(lines), title='[Tabs]')


@cmd('pwd')
def pwd(editor):
    "Print working directory."
    directory = os.getcwd()
    editor.show_message('{}'.format(directory))


@location_cmd('so', accepts_force=False)
@location_cmd('source', accepts_force=False)
def source_file(editor: Any, location: str | None) -> None:
    """
    Reload a TOML or Python vith configuration file without leaving the TUI
    (`:so [file]` / `:source [file]`).
    """
    from vith.rc_file import find_config_file, run_rc_file

    target = location or find_config_file()
    if not target:
        editor.show_message('E484: Cannot find config file')
        return

    errors: list[str] = []

    def record_error(message: str) -> None:
        errors.append(message)

    run_rc_file(editor, target, error_handler=record_error)
    if errors:
        editor.show_message(errors[-1])


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


def _set_int_option(editor, value, attribute, display_name, minimum, is_local=False):
    """
    Shared implementation of the integer-valued `:set` handlers.

    `value is None` displays the current value; invalid input shows vim-like
    error messages; otherwise the attribute is set when `value >= minimum`.
    """
    if value is None:
        if attribute == 'tabstop':
            eb = getattr(editor.window_arrangement, 'active_editor_buffer', None)
            current_value = eb.tabstop if eb is not None else getattr(editor, attribute)
        else:
            current_value = getattr(editor, attribute)
        editor.show_message('%s=%i' % (display_name, current_value))
        return
    try:
        number = int(value)
    except ValueError:
        editor.show_message('Number required after =')
        return
    if number < minimum:
        editor.show_message('Argument must be positive')
    else:
        if attribute == 'tabstop':
            eb = getattr(editor.window_arrangement, 'active_editor_buffer', None)
            if is_local:
                if eb is not None:
                    eb.tabstop = number
                else:
                    editor.show_message('No buffer')
                    return
            else:
                setattr(editor, attribute, number)
                if eb is not None:
                    eb.tabstop = number
        else:
            setattr(editor, attribute, number)
        try:
            from prompt_toolkit.application.current import get_app

            get_app().invalidate()
        except Exception:
            pass


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
            if _attribute == 'highlight_search':
                editor.clear_hlsearch_suppression()

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


@set_cmd('filetype', accepts_value=True)
@set_cmd('ft', accepts_value=True)
def set_filetype(editor, value):
    """Override the Pygments lexer for the active buffer (`:set ft=python`)."""
    eb = editor.window_arrangement.active_editor_buffer
    if eb is None:
        editor.show_message('No buffer')
        return
    if value is None:
        ft = eb.filetype
        if ft:
            editor.show_message('filetype=%s' % ft)
        else:
            editor.show_message('filetype=')
        return
    text = value.strip()
    if text == '' or text.lower() in ('off', 'none', 'auto'):
        eb.filetype_override = None
        eb.tabstop = None
        try:
            from prompt_toolkit.application.current import get_app

            get_app().invalidate()
        except Exception:
            pass
        return
    try:
        from pygments.lexers import get_lexer_by_name

        lexer = get_lexer_by_name(text)
    except Exception:
        editor.show_message('Unknown filetype: %s' % value)
        return
    aliases = getattr(lexer, 'aliases', None)
    eb.filetype_override = aliases[0] if aliases else text
    eb.tabstop = None
    try:
        from prompt_toolkit.application.current import get_app

        get_app().invalidate()
    except Exception:
        pass


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


@dataclass(frozen=True)
class _SubstituteOptions:
    """Parsed execution options for `:substitute`."""

    sub_count: int
    ignore_case: bool
    report_only: bool
    print_last: bool


def _split_delimited_fields(text: str, delim: str) -> list[str]:
    """
    Split `text` into up to three segments (`search`, `replace`, `flags`) on
    unescaped occurrences of `delim`, unescaping `\\<delim>` in the search and
    replacement segments while preserving all other regex escapes.
    """
    fields: list[str] = []
    current: list[str] = []
    i = 0
    length = len(text)

    while i < length and len(fields) < 2:
        ch = text[i]
        if ch == '\\' and i + 1 < length:
            next_ch = text[i + 1]
            if next_ch == delim:
                current.append(delim)
            else:
                current.append(ch)
                current.append(next_ch)
            i += 2
            continue
        if ch == delim:
            fields.append(''.join(current))
            current = []
            i += 1
            continue
        current.append(ch)
        i += 1

    if len(fields) == 2:
        fields.append(text[i:])
    else:
        fields.append(''.join(current))
    return fields


def parse_substitute_args(sub_args: str | None) -> tuple[str | None, str | None, str]:
    """
    Parse the argument tail of `:s` (`/<search>/<replace>/<flags>` with any
    non-alphanumeric delimiter) into `(search, replace, flags)`.
    """
    if not sub_args or not sub_args.lstrip():
        return None, None, ''

    stripped = sub_args.lstrip()
    delim = stripped[0]
    fields = _split_delimited_fields(stripped[1:], delim)

    search = fields[0]
    replace = fields[1] if len(fields) >= 2 else None
    flags = fields[2].replace(delim, '').rstrip() if len(fields) == 3 else ''
    return search, replace, flags


def _parse_substitute_flags(
    flags: str, default_ignore_case: bool
) -> tuple[_SubstituteOptions | None, str | None]:
    """
    Parse and validate `:substitute` flags (`g`, `i`, `I`, `n`, `p`), returning
    either `(_SubstituteOptions, None)` or `(None, error_message)`.
    """
    clean_flags = flags.replace('/', '')
    ignore_case = default_ignore_case
    sub_count = 1
    report_only = False
    print_last = False

    for ch in clean_flags:
        match ch:
            case 'g':
                sub_count = 0
            case 'i':
                ignore_case = True
            case 'I':
                ignore_case = False
            case 'n':
                report_only = True
            case 'p':
                print_last = True
            case _:
                return None, f'E488: Trailing characters: {ch}'

    return (
        _SubstituteOptions(
            sub_count=sub_count,
            ignore_case=ignore_case,
            report_only=report_only,
            print_last=print_last,
        ),
        None,
    )


def _resolve_substitute_range(
    range_start: str | None,
    range_end: str | None,
    cursor_row: int,
    line_count: int,
    visual_marks: tuple[int, int] | None,
) -> tuple[list[int] | None, str | None]:
    """
    Resolve substitute range bounds to an ordered list of 0-based row indices,
    or return an error message when a mark is unset or the range is invalid.
    """
    bounds, err = resolve_range(
        range_start,
        range_end,
        cursor_row,
        line_count,
        visual_marks,
        default_range='.',
    )
    if err is not None or bounds is None:
        return None, err
    start_row, end_row = bounds
    return list(range(start_row, end_row + 1)), None


def substitute(editor, range_start, range_end, search, replace, flags):
    """Substitute /search/ with /replace/ over a range of text."""

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

    # Reject patterns prone to catastrophic backtracking before compiling:
    # nested quantifiers freeze the whole editor on innocent input
    # (docs/bugs/123.md). Escaped and class-wrapped quantifiers are exempt.
    if _UNSAFE_NESTED_QUANTIFIER.search(_strip_regex_literals(search)):
        editor.show_message('Unsafe pattern (nested quantifier): %s' % search)
        return

    # Parse and validate flags (docs/bugs/083.md, docs/bugs/094.md).
    options, flag_error = _parse_substitute_flags(
        flags,
        default_ignore_case=bool(getattr(editor, 'ignore_case', False)),
    )
    if flag_error is not None or options is None:
        editor.show_message(flag_error)
        return

    # Compile the regex once; reject malformed patterns up front.
    regex_flags = re.IGNORECASE if options.ignore_case else 0
    try:
        pattern = re.compile(search, flags=regex_flags)
    except re.error as e:
        editor.show_message('Invalid regex: %s' % e)
        return

    buffer_lines = buffer.document.lines
    line_numbers, range_error = _resolve_substitute_range(
        range_start,
        range_end,
        cursor_position_row,
        len(buffer_lines),
        getattr(eb, 'visual_marks', None),
    )
    if range_error is not None or line_numbers is None:
        editor.show_message(range_error)
        return

    # Transform each line once, tracking match counts and rejecting invalid
    # replacement escapes without mutating the buffer.
    substituted = {}
    last_substituted_row = None
    total_matches = 0
    matched_lines = 0
    for row in line_numbers:
        original = buffer_lines[row]
        try:
            result, count = pattern.subn(replace, original, count=options.sub_count)
        except re.error as e:
            editor.show_message('Invalid regex: %s' % e)
            return
        substituted[row] = result
        if count > 0:
            total_matches += count
            matched_lines += 1
            last_substituted_row = row

    if total_matches == 0:
        editor.show_message('Pattern not found')
        return

    if options.report_only:
        match_word = 'match' if total_matches == 1 else 'matches'
        line_word = 'line' if matched_lines == 1 else 'lines'
        editor.show_message(f'{total_matches} {match_word} on {matched_lines} {line_word}')
        search_state.text = search
        editor.clear_hlsearch_suppression()
        return

    # Apply substitution.
    lines = list(buffer_lines)
    for row in line_numbers:
        lines[row] = substituted[row]
    assert last_substituted_row is not None
    row = last_substituted_row
    if not replace_buffer_lines(editor, eb, lines, row, first_non_blank=True):
        return

    # update editor state
    editor.last_substitute_text = replace
    search_state.text = search
    editor.clear_hlsearch_suppression()

    if options.print_last:
        editor.show_message(eb.buffer.document.lines[row])
