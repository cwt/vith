import codecs
import os
import re
import subprocess
from dataclasses import dataclass
from typing import Any

from prompt_toolkit.clipboard import ClipboardData
from prompt_toolkit.filters import to_filter
from prompt_toolkit.selection import SelectionType

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
    resolve_range_str,
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


def parse_set_tokens(variables: Any) -> list[tuple[str, str | None]]:
    """
    Extract `(option, value)` tokens from matched `:set` / `:setlocal`
    grammar variables (supporting bare `:set` and multi-argument `:set`).
    """
    first_opt = variables.get('set_option')
    if first_opt is None:
        return []
    tokens: list[tuple[str, str | None]] = [(first_opt, variables.get('set_value'))]
    rest = variables.get('set_rest')
    if rest:
        for raw_tok in rest.split():
            if '=' in raw_tok:
                opt_name, opt_val = raw_tok.split('=', 1)
                tokens.append((opt_name, opt_val))
            else:
                tokens.append((raw_tok, None))
    return tokens


@_cmd('set')
@_cmd('setlocal')
@_cmd('setl')
def set_command_execute(editor: Any, variables: Any) -> None:
    """
    Execute a `:set` / `:setlocal` / `:setl` command (supporting bare `:set`,
    `:set all`, queries `opt?`, toggles `opt!` / `invopt`, and multi-argument
    option lists).
    """
    if variables.get('force'):
        editor.show_message('No ! allowed')
        return

    command = variables.get('command')
    is_local = command in ('setlocal', 'setl')
    tokens = parse_set_tokens(variables)

    if not tokens:
        lines = set_options_lines(editor, show_all=False)
        editor.show_info('\n'.join(lines), title='[Options]')
        return

    queries: list[str] = []
    for opt, value in tokens:
        if not _apply_set_token(editor, opt, value, is_local=is_local, queries=queries):
            return

    if queries:
        editor.show_message('  '.join(queries))


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


@cmd('on', accepts_force=True)
@cmd('only', accepts_force=True)
def only(editor, force=False):
    """
    Keep only the current window in the current tab (`:only`), leaving other
    tab pages untouched (docs/bugs/132.md).
    """
    wa = editor.window_arrangement
    active_tab = wa.active_tab
    if active_tab is None:
        return
    if not force and active_tab.window_count() > 1:
        active_eb = wa.active_editor_buffer
        for win in active_tab.windows():
            eb = win.editor_buffer
            if (
                eb is not None
                and eb is not active_eb
                and eb.has_unsaved_changes
                and _buffer_visible_count(wa, eb) <= 1
            ):
                editor.show_message('E445: Other window contains changes')
                return
    wa.keep_only_current_window()


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


def _buffer_visible_count(window_arrangement: Any, editor_buffer: Any) -> int:
    """Number of windows currently showing `editor_buffer` (across all tabs)."""
    return sum(
        1
        for tab in window_arrangement.tab_pages
        for win in tab.windows()
        if win.editor_buffer is editor_buffer
    )


def _exit_if_no_unsaved_changes(editor: Any, force: bool) -> bool:
    """Exit the application unless an unsaved buffer blocks it (`:qa` rule)."""
    window_arrangement = editor.window_arrangement
    if not force and any(eb.has_unsaved_changes for eb in window_arrangement.editor_buffers):
        editor.show_message(_NO_WRITE_SINCE_LAST_CHANGE_TEXT)
        return False
    editor.application.exit()
    return True


def close_or_exit(editor: Any, force: bool = False) -> bool:
    """
    Shared quit-family helper: close the current window, exiting only when it
    is the last one (docs/bugs/131.md).

    A dirty buffer blocks the close only when this is its last visible window —
    the same rule as `:close` (docs/bugs/067.md, 095.md). Hidden buffers or
    dirty buffers in other windows never block a single-window close.
    """
    wa = editor.window_arrangement
    active_tab = wa.active_tab
    has_multiple_windows = len(wa.tab_pages) > 1 or (
        active_tab is not None and active_tab.window_count() > 1
    )

    if has_multiple_windows:
        active_eb = wa.active_editor_buffer
        if (
            not force
            and active_eb is not None
            and active_eb.has_unsaved_changes
            and _buffer_visible_count(wa, active_eb) <= 1
        ):
            editor.show_message(_NO_WRITE_SINCE_LAST_CHANGE_TEXT)
            return False
        wa.close_window()
        return True

    return _exit_if_no_unsaved_changes(editor, force)


@cmd('q', accepts_force=True)
@cmd('quit', accepts_force=True)
def quit(editor, all_=False, force=False):
    """
    Quit. With several windows/tabs open this closes the current window (Vim
    semantics); the editor exits only on the last window.
    """
    if all_:
        return _exit_if_no_unsaved_changes(editor, force)
    return close_or_exit(editor, force)


@cmd('qa', accepts_force=True)
@cmd('qall', accepts_force=True)
def quit_all(editor, force=False):
    """
    Quit all.
    """
    quit(editor, all_=True, force=force)


def _normalized_write_target(location: str | None) -> str | None:
    """Normalize a write path for same-file comparison (tilde/symlink safe)."""
    if not location:
        return None
    return os.path.realpath(os.path.abspath(os.path.expanduser(location)))


def _is_same_write_target(eb: Any, location: str | None) -> bool:
    """Whether `location` names the buffer's own file."""
    target = _normalized_write_target(location)
    current = _normalized_write_target(eb.location)
    return target is not None and current is not None and target == current


def _reject_existing_write_target(editor: Any, eb: Any, location: str | None, force: bool) -> bool:
    """
    Shared overwrite guard for explicit write targets (docs/bugs/130.md).

    Returns True (after showing the standard message) when a write to an
    existing *foreign* file must be blocked. The active buffer's own file is
    exempt, matching Vim's `:w %`, `:wq %`, `:up %`, `:x %` and
    `:sav <current>` without `!`.
    """
    if not location or force:
        return False
    if _is_same_write_target(eb, location):
        return False
    if os.path.exists(os.path.expanduser(location)):
        editor.show_message('File exists (add ! to override)')
        return True
    return False


def write(editor, location, force=False):
    """
    Write file. Returns whether the buffer was written.
    """
    eb = editor.window_arrangement.active_editor_buffer
    if eb is None or (location is None and eb.location is None):
        editor.show_message(_NO_FILE_NAME)
        return False
    if _reject_existing_write_target(editor, eb, location, force):
        return False
    return eb.write(location, force=force)


def _write_payload_to_location(editor: Any, eb: Any, target: str, payload: str) -> bool:
    """Write `payload` to `target` via `editor.io_backends` without mutating `eb`."""
    io = next(
        (backend for backend in editor.io_backends if backend.can_open_location(target)),
        None,
    )
    if io is None:
        editor.show_message('Unknown location: %r' % target)
        return False
    try:
        try:
            io.write(target, payload, eb.encoding)
        except UnicodeEncodeError:
            io.write(target, payload, 'utf-8')
    except Exception as exc:
        editor.show_message('%s' % exc)
        return False
    return True


def _write_pipe_shell(editor: Any, shell_cmd: str, payload: str) -> None:
    """Pipe `payload` to `shell_cmd`'s stdin (`:[range]w !{cmd}`)."""
    cmd = shell_cmd.strip()
    if not cmd:
        editor.show_message('E34: No previous command')
        return
    try:
        proc = subprocess.run(
            cmd,
            shell=True,
            input=payload,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        editor.show_message(f'Command timed out after 30s: {cmd}')
        return
    except OSError as exc:
        editor.show_message(f'Cannot execute shell command: {exc}')
        return

    output = (proc.stdout or '').replace('\r\n', '\n').rstrip('\n')
    if output:
        if '\n' in output:
            editor.show_info(output, title=f'[!{cmd}]')
        else:
            editor.show_message(output)
    if proc.returncode != 0:
        editor.show_message(f'shell returned {proc.returncode}')


def _write_append_file(editor: Any, eb: Any, target: str, payload: str, force: bool) -> None:
    """Append `payload` to `target` (`:[range]w[!] >> {file}`)."""
    expanded = os.path.expanduser(target)
    if not force and not os.path.exists(expanded):
        editor.show_message('File does not exist (add ! to create)')
        return
    try:
        try:
            with open(expanded, 'a', encoding=eb.encoding) as fh:
                fh.write(payload)
        except UnicodeEncodeError:
            with open(expanded, 'a', encoding='utf-8') as fh:
                fh.write(payload)
    except OSError as exc:
        editor.show_message('%s' % exc)


@_cmd('write')
@_cmd('w')
def write_command(editor: Any, variables: Any) -> None:
    """
    Write buffer or range to file, append (`>>`), or pipe to shell (`!cmd`)
    (`:[range]w[!] [file | >> file | !cmd]`).
    """
    cmd_range = variables.get('cmd_range')
    force = bool(variables.get('force'))
    location = variables.get('location')
    write_append = variables.get('write_append')
    write_shell = variables.get('write_shell')

    if write_shell is not None:
        eb = editor.window_arrangement.active_editor_buffer
        if eb is None:
            return
        bounds, err = resolve_range_str(cmd_range, eb, default_range='%')
        if err is not None or bounds is None:
            editor.show_message(err)
            return
        start_row, end_row = bounds
        payload = '\n'.join(eb.buffer.document.lines[start_row : end_row + 1]) + '\n'
        _write_pipe_shell(editor, write_shell, payload)
        return

    if write_append is not None:
        eb = editor.window_arrangement.active_editor_buffer
        if eb is None:
            return
        bounds, err = resolve_range_str(cmd_range, eb, default_range='%')
        if err is not None or bounds is None:
            editor.show_message(err)
            return
        start_row, end_row = bounds
        payload = '\n'.join(eb.buffer.document.lines[start_row : end_row + 1]) + '\n'
        _write_append_file(editor, eb, write_append, payload, force=force)
        return

    if cmd_range is not None:
        eb = editor.window_arrangement.active_editor_buffer
        if eb is None:
            editor.show_message(_NO_FILE_NAME)
            return
        bounds, err = resolve_range_str(cmd_range, eb, default_range='%')
        if err is not None or bounds is None:
            editor.show_message(err)
            return
        start_row, end_row = bounds
        lines = eb.buffer.document.lines
        is_partial = start_row > 0 or end_row < len(lines) - 1
        if not is_partial:
            write(editor, location, force=force)
            return
        target = location or eb.location
        if not target:
            editor.show_message(_NO_FILE_NAME)
            return
        if not force and location is None:
            editor.show_message('Use ! to write partial buffer')
            return
        if _reject_existing_write_target(editor, eb, location, force):
            return
        payload = '\n'.join(lines[start_row : end_row + 1]) + '\n'
        _write_payload_to_location(editor, eb, target, payload)
        return

    write(editor, location, force=force)


def filter_range_command(editor: Any, range_str: str | None, shell_cmd: str) -> None:
    """
    Pipe addressed lines through an external shell command and replace the
    range with combined output (`:[range]!{cmd}`, Decision D3).
    """
    eb = editor.window_arrangement.active_editor_buffer
    if eb is None:
        return
    if _is_buffer_read_only(eb.buffer):
        editor.show_message(_MODIFIABLE_OFF_TEXT)
        return

    cmd = shell_cmd.strip()
    if not cmd:
        editor.show_message('E34: No previous command')
        return

    bounds, err = resolve_range_str(range_str, eb, default_range='.')
    if err is not None or bounds is None:
        editor.show_message(err)
        return

    start_row, end_row = bounds
    lines = list(eb.buffer.document.lines)
    stdin_text = '\n'.join(lines[start_row : end_row + 1]) + '\n'

    try:
        proc = subprocess.run(
            cmd,
            shell=True,
            input=stdin_text,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        editor.show_message(f'Command timed out after 30s: {cmd}')
        return
    except OSError as exc:
        editor.show_message(f'Cannot execute shell command: {exc}')
        return

    output = (proc.stdout or '').replace('\r\n', '\n')
    if output == '':
        replacement: list[str] = []
    else:
        if output.endswith('\n'):
            output = output[:-1]
        replacement = output.split('\n')

    new_lines = lines[:start_row] + replacement + lines[end_row + 1 :]
    cursor_row = min(start_row, max(0, len(new_lines) - 1))
    replace_buffer_lines(
        editor,
        eb,
        new_lines,
        cursor_row,
        first_non_blank=True,
        inserted_text=output,
    )
    if proc.returncode != 0:
        editor.show_message(f'shell returned {proc.returncode}')


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
    eb = editor.window_arrangement.active_editor_buffer
    if eb is None:
        editor.show_message(_NO_FILE_NAME)
        return False
    if _reject_existing_write_target(editor, eb, location, force):
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
        return False
    return quit(editor, all_=False, force=force)


@location_cmd('wq', accepts_force=True)
def write_and_quit(editor, location, force=False):
    """
    Write file and quit. Does not quit when the write failed.
    """
    if write(editor, location, force=force):
        return quit(editor, all_=False, force=force)
    return False


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


def _format_register_type(selection_type: Any) -> str:
    if selection_type == SelectionType.LINES:
        return 'l'
    if selection_type == SelectionType.BLOCK:
        return 'b'
    return 'c'


def _format_register_preview(text: str, max_len: int = 200) -> str:
    preview = text.replace('\r', '^M').replace('\n', '^J').replace('\t', '^I')
    if len(preview) > max_len:
        return preview[: max_len - 3] + '...'
    return preview


def _register_sort_key(name: str) -> tuple[int, str]:
    if name.isdigit():
        return (0, name)
    if 'a' <= name <= 'z':
        return (1, name)
    return (2, name)


def registers_lines(editor: Any) -> list[str]:
    """
    Build Vim-style `:registers` lines from the unnamed clipboard (`""`) and
    non-empty named registers on `editor.application.vi_state.named_registers`.
    """
    rows: list[str] = []
    app = getattr(editor, 'application', None)
    if app is None:
        return []

    clipboard = getattr(app, 'clipboard', None)
    if clipboard is not None:
        unnamed = clipboard.get_data()
        if unnamed is not None and unnamed.text:
            rtype = _format_register_type(getattr(unnamed, 'type', SelectionType.CHARACTERS))
            rows.append(f'  {rtype}  ""   {_format_register_preview(unnamed.text)}')

    vi_state = getattr(app, 'vi_state', None)
    named_registers = getattr(vi_state, 'named_registers', None) or {}
    for name in sorted(named_registers.keys(), key=_register_sort_key):
        data = named_registers[name]
        if data is not None and data.text:
            rtype = _format_register_type(getattr(data, 'type', SelectionType.CHARACTERS))
            rows.append(f'  {rtype}  "{name}   {_format_register_preview(data.text)}')

    if not rows:
        return []
    return ['Type Name Content', *rows]


@cmd('registers')
@cmd('reg')
def show_registers(editor: Any) -> None:
    """
    Display non-empty registers in a read-only split inside vith
    (`:reg` / `:registers`).
    """
    lines = registers_lines(editor)
    if not lines:
        editor.show_message('No registers', record=False)
        return
    editor.show_info('\n'.join(lines), title='[Registers]')


def marks_lines(editor: Any) -> list[str]:
    """
    Build Vim-style `:marks` lines from buffer-local marks and `'<` / `'>`
    visual line marks on the active buffer.
    """
    eb = editor.window_arrangement.active_editor_buffer
    if eb is None:
        return []

    doc = eb.buffer.document
    doc_lines = doc.lines
    line_count = len(doc_lines)
    collected: list[tuple[str, int, int]] = []

    raw_marks: dict[str, Any] = {}
    buf_marks = getattr(eb.buffer, 'marks', None)
    if isinstance(buf_marks, dict):
        raw_marks.update(buf_marks)
    eb_marks = getattr(eb, 'marks', None)
    if isinstance(eb_marks, dict):
        raw_marks.update(eb_marks)

    for name in sorted(raw_marks.keys()):
        pos = raw_marks[name]
        if isinstance(pos, tuple) and len(pos) == 2:
            row, col = int(pos[0]), int(pos[1])
        elif isinstance(pos, int):
            clamped = max(0, min(pos, len(doc.text)))
            row, col = doc.translate_index_to_position(clamped)
        else:
            continue
        collected.append((str(name), row, col))

    visual_marks = getattr(eb, 'visual_marks', None)
    if visual_marks is not None:
        start_row, end_row = visual_marks
        collected.append(('<', int(start_row), 0))
        collected.append(('>', int(end_row), 0))

    if not collected:
        return []

    rows = ['mark line  col file/text']
    for name, row, col in collected:
        line_text = doc_lines[row] if 0 <= row < line_count else ''
        rows.append(f' {name:<3} {row + 1:>4} {col:>4} {line_text}')
    return rows


@cmd('marks')
def show_marks(editor: Any) -> None:
    """
    Display marks recorded on the active buffer (including `'<` / `'>`) in a
    read-only split inside vith (`:marks`).
    """
    lines = marks_lines(editor)
    if not lines:
        editor.show_message('No marks', record=False)
        return
    editor.show_info('\n'.join(lines), title='[Marks]')


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


@cmd('tabclose', accepts_force=True)
@cmd('tabc', accepts_force=True)
def tab_close(editor, force=False):
    """
    Close tab page (`:tabc` / `:tabclose`), with an E37 dirty-buffer guard
    (docs/bugs/132.md).
    """
    wa = editor.window_arrangement
    active_tab = wa.active_tab
    if active_tab is None or len(wa.tab_pages) <= 1:
        return
    if not force:
        for win in active_tab.windows():
            eb = win.editor_buffer
            if eb is not None and eb.has_unsaved_changes and _buffer_visible_count(wa, eb) <= 1:
                editor.show_message('E37: No write since last change (add ! to override)')
                return
    wa.close_tab()


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
    Close all tab pages except the current tab (`:tabo` / `:tabonly`), after
    the E37 dirty-buffer guard (docs/bugs/132.md).
    """
    wa = editor.window_arrangement
    active_tab = wa.active_tab
    if not force:
        for tab in wa.tab_pages:
            if tab is active_tab:
                continue
            for win in tab.windows():
                eb = win.editor_buffer
                if eb is not None and eb.has_unsaved_changes and _buffer_visible_count(wa, eb) <= 1:
                    editor.show_message('E37: No write since last change (add ! to override)')
                    return
    wa.keep_only_current_tab()


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


def _set_int_option(editor, value, attribute, display_name, minimum, is_local=False) -> bool:
    """
    Shared implementation of the integer-valued `:set` handlers.

    `value is None` displays the current value; invalid input shows vim-like
    error messages; otherwise the attribute is set when `value >= minimum`.
    Returns `True` on success, `False` on validation/state error.
    """
    if value is None:
        if attribute == 'tabstop':
            eb = getattr(editor.window_arrangement, 'active_editor_buffer', None)
            current_value = eb.tabstop if eb is not None else getattr(editor, attribute)
        elif attribute == 'shiftwidth':
            eb = getattr(editor.window_arrangement, 'active_editor_buffer', None)
            override = getattr(eb, '_shiftwidth_override', None) if eb is not None else None
            current_value = override if override is not None else getattr(editor, attribute)
        else:
            current_value = getattr(editor, attribute)
        editor.show_message('%s=%i' % (display_name, current_value))
        return True
    try:
        number = int(value)
    except ValueError:
        editor.show_message('Number required after =')
        return False
    if number < minimum:
        editor.show_message('Argument must be positive')
        return False
    if attribute in ('tabstop', 'shiftwidth'):
        eb = getattr(editor.window_arrangement, 'active_editor_buffer', None)
        if is_local:
            if eb is not None:
                setattr(eb, attribute, number)
            else:
                editor.show_message('No buffer')
                return False
        else:
            setattr(editor, attribute, number)
            if eb is not None:
                setattr(eb, attribute, number)
    else:
        setattr(editor, attribute, number)
    try:
        from prompt_toolkit.application.current import get_app

        get_app().invalidate()
    except Exception:
        pass
    return True


def _set_list_option(editor: Any, value: str | None, attribute: str, display_name: str) -> bool:
    """Set or display a comma-separated integer list option (e.g. `colorcolumn`)."""
    if value is None:
        current = getattr(editor, attribute)
        formatted = ','.join(str(v) for v in current)
        editor.show_message(f'{display_name}={formatted}')
        return True
    try:
        if value:
            numbers = [int(val) for val in value.split(',')]
        else:
            numbers = []
    except ValueError:
        editor.show_message('Invalid value. Expecting comma separated list of integers')
        return False
    setattr(editor, attribute, numbers)
    return True


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
    ('smartcase', 'smartcase', ('smartcase', 'scs'), ('nosmartcase', 'noscs')),
    ('autoindent', 'autoindent', ('autoindent', 'ai'), ('noautoindent', 'noai')),
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
    ('shiftwidth', 'shiftwidth', ('shiftwidth', 'sw'), 0),
    ('scrolloff', 'scroll_offset', ('scrolloff', 'so'), 0),
)

#: (display name, editor attribute, aliases) — comma-separated integer list.
SET_LIST_OPTIONS = (('colorcolumn', 'colorcolumn', ('colorcolumn', 'cc')),)

_TOGGLE_ON_MAP: dict[str, tuple[str, str]] = {}
_TOGGLE_OFF_MAP: dict[str, tuple[str, str]] = {}
_INT_OPTION_MAP: dict[str, tuple[str, str, int]] = {}
_LIST_OPTION_MAP: dict[str, tuple[str, str]] = {}


def _register_generated_set_options() -> None:
    """Materialize the `:set` catalog into `SET_COMMANDS` (docs/bugs/073.md)."""
    for display, attribute, on_names, off_names in SET_TOGGLE_GROUPS:

        def _on(editor, _attribute=attribute):
            setattr(editor, _attribute, True)
            if _attribute == 'highlight_search':
                editor.clear_hlsearch_suppression()

        def _off(editor, _attribute=attribute):
            setattr(editor, _attribute, False)

        for name in on_names:
            SET_COMMANDS[name] = _on
            _TOGGLE_ON_MAP[name] = (display, attribute)
        for name in off_names:
            SET_COMMANDS[name] = _off
            _TOGGLE_OFF_MAP[name] = (display, attribute)

    for display, attribute, names, minimum in SET_INT_OPTIONS:

        def _int_option(editor, value, _display=display, _attribute=attribute, _minimum=minimum):
            _set_int_option(editor, value, _attribute, _display, _minimum)

        for name in names:
            SET_COMMANDS[name] = _int_option
            SET_COMMANDS_TAKING_VALUE.add(name)
            _INT_OPTION_MAP[name] = (display, attribute, minimum)

    for display, attribute, names in SET_LIST_OPTIONS:

        def _list_option(editor, value, _attribute=attribute, _display=display):
            _set_list_option(editor, value, _attribute, _display)

        for name in names:
            SET_COMMANDS[name] = _list_option
            SET_COMMANDS_TAKING_VALUE.add(name)
            _LIST_OPTION_MAP[name] = (display, attribute)


_register_generated_set_options()


def _format_toggle_value(display: str, enabled: bool) -> str:
    return display if enabled else f'no{display}'


def _query_set_option(editor: Any, name: str) -> tuple[str | None, str | None]:
    """
    Format the current value of option `name` for `:set {opt}?` (or bare
    value-option query). Returns `(formatted, None)` or `(None, error)`.
    """
    toggle_info = _TOGGLE_ON_MAP.get(name) or _TOGGLE_OFF_MAP.get(name)
    if toggle_info is not None:
        display, attr = toggle_info
        return _format_toggle_value(display, bool(getattr(editor, attr))), None

    if name in ('tildeop', 'top', 'notildeop', 'notop'):
        enabled = bool(editor.application.vi_state.tilde_operator)
        return _format_toggle_value('tildeop', enabled), None

    if name in ('readonly', 'ro', 'noreadonly', 'noro'):
        eb = getattr(editor.window_arrangement, 'active_editor_buffer', None)
        if eb is None:
            return None, 'No buffer'
        return _format_toggle_value('readonly', _is_buffer_read_only(eb.buffer)), None

    int_info = _INT_OPTION_MAP.get(name)
    if int_info is not None:
        display, attr, _min = int_info
        if attr == 'tabstop':
            eb = getattr(editor.window_arrangement, 'active_editor_buffer', None)
            val = eb.tabstop if eb is not None else getattr(editor, attr)
        elif attr == 'shiftwidth':
            eb = getattr(editor.window_arrangement, 'active_editor_buffer', None)
            override = getattr(eb, '_shiftwidth_override', None) if eb is not None else None
            val = override if override is not None else getattr(editor, attr)
        else:
            val = getattr(editor, attr)
        return f'{display}={val}', None

    list_info = _LIST_OPTION_MAP.get(name)
    if list_info is not None:
        display, attr = list_info
        val = getattr(editor, attr)
        return f"{display}={','.join(str(v) for v in val)}", None

    if name in ('filetype', 'ft'):
        eb = getattr(editor.window_arrangement, 'active_editor_buffer', None)
        if eb is None:
            return None, 'No buffer'
        return f'filetype={eb.filetype or ""}', None

    if name in ('fileencoding', 'fenc'):
        eb = getattr(editor.window_arrangement, 'active_editor_buffer', None)
        if eb is None:
            return None, 'No buffer'
        return f'fileencoding={eb.encoding or ""}', None

    return None, f'Unknown option: {name}'


def _toggle_boolean_option(editor: Any, base_name: str) -> tuple[bool, bool]:
    """
    Invert boolean option `base_name` (used by `opt!` and `invopt`).
    Returns `(handled, ok)` where `ok=False` means an error was shown.
    """
    toggle_info = _TOGGLE_ON_MAP.get(base_name)
    if toggle_info is not None:
        _display, attr = toggle_info
        new_value = not bool(getattr(editor, attr))
        setattr(editor, attr, new_value)
        if attr == 'highlight_search' and new_value:
            editor.clear_hlsearch_suppression()
        return True, True
    if base_name in ('tildeop', 'top'):
        vi_state = editor.application.vi_state
        vi_state.tilde_operator = not bool(vi_state.tilde_operator)
        return True, True
    if base_name in ('readonly', 'ro'):
        eb = getattr(editor.window_arrangement, 'active_editor_buffer', None)
        if eb is None:
            editor.show_message('No buffer')
            return True, False
        eb.buffer.read_only = to_filter(not _is_buffer_read_only(eb.buffer))
        return True, True
    return False, False


def set_options_lines(editor: Any, show_all: bool = False) -> list[str]:
    """
    Build formatted option lines for bare `:set` (changed from defaults) and
    `:set all` (all options), for display inside vith via `Editor.show_info`.
    """
    defaults = getattr(editor, '_default_options', {})
    eb = getattr(editor.window_arrangement, 'active_editor_buffer', None)
    entries: list[str] = []

    for display, attr, _on, _off in SET_TOGGLE_GROUPS:
        cur = bool(getattr(editor, attr))
        default_val = bool(defaults.get(attr, cur))
        if show_all or cur != default_val:
            entries.append(_format_toggle_value(display, cur))

    tilde_cur = bool(editor.application.vi_state.tilde_operator)
    if show_all or tilde_cur is not False:
        entries.append(_format_toggle_value('tildeop', tilde_cur))

    ro_cur = _is_buffer_read_only(eb.buffer) if eb is not None else False
    if show_all or ro_cur is not False:
        entries.append(_format_toggle_value('readonly', ro_cur))

    for display, attr, _names, _min in SET_INT_OPTIONS:
        if attr == 'tabstop' and eb is not None:
            cur_int = eb.tabstop
        elif attr == 'shiftwidth' and eb is not None:
            override = getattr(eb, '_shiftwidth_override', None)
            cur_int = override if override is not None else int(getattr(editor, attr))
        else:
            cur_int = int(getattr(editor, attr))
        default_int = int(defaults.get(attr, cur_int))
        if show_all or cur_int != default_int:
            entries.append(f'{display}={cur_int}')

    for display, attr, _names in SET_LIST_OPTIONS:
        cur_list = list(getattr(editor, attr))
        default_list = list(defaults.get(attr, []))
        if show_all or cur_list != default_list:
            entries.append(f"{display}={','.join(str(v) for v in cur_list)}")

    cur_ft = (eb.filetype or '') if eb is not None else ''
    if show_all or cur_ft != '':
        entries.append(f'filetype={cur_ft}')

    cur_fenc = (eb.encoding or 'utf-8') if eb is not None else 'utf-8'
    if show_all or cur_fenc != 'utf-8':
        entries.append(f'fileencoding={cur_fenc}')

    entries.sort()
    lines = ['--- Options ---']
    if entries:
        lines.extend(entries)
    else:
        lines.append('(all options at default values)')
    return lines


def _apply_set_token(
    editor: Any,
    opt: str,
    value: str | None,
    *,
    is_local: bool,
    queries: list[str],
) -> bool:
    """Apply a single `:set` / `:setlocal` token; returns `False` on error."""
    if opt == 'all':
        if value is not None:
            editor.show_message(f'E474: Invalid argument: {opt}={value}')
            return False
        lines = set_options_lines(editor, show_all=True)
        editor.show_info('\n'.join(lines), title='[Options]')
        return True

    if opt.endswith('?'):
        if value is not None:
            editor.show_message(f'E474: Invalid argument: {opt}={value}')
            return False
        text, err = _query_set_option(editor, opt[:-1])
        if err is not None or text is None:
            editor.show_message(err or f'Unknown option: {opt[:-1]}')
            return False
        queries.append(text)
        return True

    if opt.endswith('!'):
        if value is not None:
            editor.show_message(f'E474: Invalid argument: {opt}={value}')
            return False
        base = opt[:-1]
        handled, ok = _toggle_boolean_option(editor, base)
        if handled:
            return ok
        if (
            base in _INT_OPTION_MAP
            or base in _LIST_OPTION_MAP
            or base in ('filetype', 'ft', 'fileencoding', 'fenc')
        ):
            editor.show_message(f'E474: Invalid argument: {opt}')
            return False
        editor.show_message(f'Unknown option: {opt}')
        return False

    if opt.startswith('inv') and opt not in SET_COMMANDS:
        base = opt[3:]
        if base in _TOGGLE_ON_MAP or base in ('tildeop', 'top', 'readonly', 'ro'):
            if value is not None:
                editor.show_message(f'E474: Invalid argument: {opt}={value}')
                return False
            _handled, ok = _toggle_boolean_option(editor, base)
            return ok
        editor.show_message(f'Unknown option: {opt}')
        return False

    if (
        opt in _TOGGLE_ON_MAP
        or opt in _TOGGLE_OFF_MAP
        or opt
        in (
            'tildeop',
            'top',
            'notildeop',
            'notop',
            'readonly',
            'ro',
            'noreadonly',
            'noro',
        )
    ):
        if value is not None:
            editor.show_message(f'E474: Invalid argument: {opt}={value}')
            return False
        result = SET_COMMANDS[opt](editor)
        return True if result is None else bool(result)

    int_info = _INT_OPTION_MAP.get(opt)
    if int_info is not None:
        display, attr, minimum = int_info
        if value is None:
            text, err = _query_set_option(editor, opt)
            if err is not None or text is None:
                editor.show_message(err or f'Unknown option: {opt}')
                return False
            queries.append(text)
            return True
        return _set_int_option(editor, value, attr, display, minimum, is_local=is_local)

    list_info = _LIST_OPTION_MAP.get(opt)
    if list_info is not None:
        display, attr = list_info
        if value is None:
            text, err = _query_set_option(editor, opt)
            if err is not None or text is None:
                editor.show_message(err or f'Unknown option: {opt}')
                return False
            queries.append(text)
            return True
        return _set_list_option(editor, value, attr, display)

    if opt in ('filetype', 'ft'):
        if value is None:
            text, err = _query_set_option(editor, opt)
            if err is not None or text is None:
                editor.show_message(err or f'Unknown option: {opt}')
                return False
            queries.append(text)
            return True
        return bool(set_filetype(editor, value))

    if opt in ('fileencoding', 'fenc'):
        if value is None:
            text, err = _query_set_option(editor, opt)
            if err is not None or text is None:
                editor.show_message(err or f'Unknown option: {opt}')
                return False
            queries.append(text)
            return True
        return bool(set_fileencoding(editor, value))

    if opt in SET_COMMANDS:
        if opt in SET_COMMANDS_TAKING_VALUE:
            SET_COMMANDS[opt](editor, value)
        else:
            if value is not None:
                editor.show_message(f'E474: Invalid argument: {opt}={value}')
                return False
            SET_COMMANDS[opt](editor)
        return True

    editor.show_message('Unknown option: %s' % opt)
    return False


@set_cmd('readonly')
@set_cmd('ro')
def enable_readonly(editor: Any) -> bool:
    """Mark the active buffer read-only (`:set ro`)."""
    eb = editor.window_arrangement.active_editor_buffer
    if eb is None:
        editor.show_message('No buffer')
        return False
    eb.buffer.read_only = to_filter(True)
    return True


@set_cmd('noreadonly')
@set_cmd('noro')
def disable_readonly(editor: Any) -> bool:
    """Mark the active buffer writable (`:set noro`)."""
    eb = editor.window_arrangement.active_editor_buffer
    if eb is None:
        editor.show_message('No buffer')
        return False
    eb.buffer.read_only = to_filter(False)
    return True


@set_cmd('fileencoding', accepts_value=True)
@set_cmd('fenc', accepts_value=True)
def set_fileencoding(editor: Any, value: str | None) -> bool:
    """Get or set the active buffer's file encoding (`:set fenc=utf-8`)."""
    eb = editor.window_arrangement.active_editor_buffer
    if eb is None:
        editor.show_message('No buffer')
        return False
    if value is None:
        editor.show_message(f'fileencoding={eb.encoding or ""}')
        return True
    name = value.strip()
    if not name:
        editor.show_message(f'E474: Invalid argument: {value}')
        return False
    try:
        codecs.lookup(name)
    except LookupError:
        editor.show_message(f'E474: Invalid argument: {value}')
        return False
    eb.encoding = name.lower()
    return True


@set_cmd('filetype', accepts_value=True)
@set_cmd('ft', accepts_value=True)
def set_filetype(editor, value) -> bool:
    """Override the Pygments lexer for the active buffer (`:set ft=python`)."""
    eb = editor.window_arrangement.active_editor_buffer
    if eb is None:
        editor.show_message('No buffer')
        return False
    if value is None:
        ft = eb.filetype
        if ft:
            editor.show_message('filetype=%s' % ft)
        else:
            editor.show_message('filetype=')
        return True
    text = value.strip()
    if text == '' or text.lower() in ('off', 'none', 'auto'):
        eb.filetype_override = None
        eb.tabstop = None
        try:
            from prompt_toolkit.application.current import get_app

            get_app().invalidate()
        except Exception:
            pass
        return True
    try:
        from pygments.lexers import get_lexer_by_name

        lexer = get_lexer_by_name(text)
    except Exception:
        editor.show_message('Unknown filetype: %s' % value)
        return False
    aliases = getattr(lexer, 'aliases', None)
    eb.filetype_override = aliases[0] if aliases else text
    eb.tabstop = None
    try:
        from prompt_toolkit.application.current import get_app

        get_app().invalidate()
    except Exception:
        pass
    return True


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


def _should_ignore_case(editor: Any, pattern: str | None) -> bool:
    """Resolve effective case-folding honoring `ignorecase` and `smartcase`."""
    checker = getattr(editor, 'should_ignore_case', None)
    if callable(checker):
        return bool(checker(pattern))
    if not bool(getattr(editor, 'ignore_case', False)):
        return False
    if (
        bool(getattr(editor, 'smartcase', False))
        and pattern
        and any(ch.isupper() for ch in pattern)
    ):
        return False
    return True


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
        default_ignore_case=_should_ignore_case(editor, search),
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


def _apply_optional_line_count(
    start_row: int, end_row: int, cmd_arg: str | None, line_count: int
) -> tuple[tuple[int, int] | None, str | None]:
    """Adjust `(start_row, end_row)` for an optional Ex line count (`:2d 3`)."""
    if not cmd_arg:
        return (start_row, end_row), None
    count = int(cmd_arg)
    if count < 1:
        return None, f'E488: Trailing characters: {cmd_arg}'
    new_start = end_row
    new_end = min(line_count - 1, new_start + count - 1)
    return (new_start, new_end), None


@_cmd('delete')
@_cmd('d')
def delete_lines(editor, variables):
    """
    Delete addressed lines and place them on the clipboard (`:[range]d [count]`).
    """
    if variables.get('force'):
        editor.show_message('No ! allowed')
        return
    eb = editor.window_arrangement.active_editor_buffer
    if eb is None:
        return
    if _is_buffer_read_only(eb.buffer):
        editor.show_message(_MODIFIABLE_OFF_TEXT)
        return

    bounds, err = resolve_range_str(variables.get('cmd_range'), eb, default_range='.')
    if err is not None or bounds is None:
        editor.show_message(err)
        return

    lines = list(eb.buffer.document.lines)
    bounds, count_err = _apply_optional_line_count(
        bounds[0], bounds[1], variables.get('cmd_arg'), len(lines)
    )
    if count_err is not None or bounds is None:
        editor.show_message(count_err)
        return

    start_row, end_row = bounds
    deleted = lines[start_row : end_row + 1]
    editor.application.clipboard.set_data(ClipboardData('\n'.join(deleted), SelectionType.LINES))

    remaining = lines[:start_row] + lines[end_row + 1 :]
    if not remaining:
        remaining = ['']
    target_row = min(start_row, len(remaining) - 1)
    if not replace_buffer_lines(editor, eb, remaining, target_row, first_non_blank=True):
        return

    if len(deleted) >= 2:
        editor.show_message(f'{len(deleted)} fewer lines')


@_cmd('yank')
@_cmd('y')
def yank_lines(editor, variables):
    """
    Copy addressed lines to the clipboard (`:[range]y [count]`).
    """
    if variables.get('force'):
        editor.show_message('No ! allowed')
        return
    eb = editor.window_arrangement.active_editor_buffer
    if eb is None:
        return

    bounds, err = resolve_range_str(variables.get('cmd_range'), eb, default_range='.')
    if err is not None or bounds is None:
        editor.show_message(err)
        return

    lines = eb.buffer.document.lines
    bounds, count_err = _apply_optional_line_count(
        bounds[0], bounds[1], variables.get('cmd_arg'), len(lines)
    )
    if count_err is not None or bounds is None:
        editor.show_message(count_err)
        return

    start_row, end_row = bounds
    yanked = lines[start_row : end_row + 1]
    editor.application.clipboard.set_data(ClipboardData('\n'.join(yanked), SelectionType.LINES))
    line_word = 'line' if len(yanked) == 1 else 'lines'
    editor.show_message(f'{len(yanked)} {line_word} yanked')


@_cmd('put')
@_cmd('pu')
def put_lines(editor, variables):
    """
    Insert clipboard text after the addressed line, or before with `!`
    (`:[addr]pu[!]`).
    """
    if variables.get('cmd_arg'):
        editor.show_message(f"E488: Trailing characters: {variables['cmd_arg']}")
        return
    eb = editor.window_arrangement.active_editor_buffer
    if eb is None:
        return
    if _is_buffer_read_only(eb.buffer):
        editor.show_message(_MODIFIABLE_OFF_TEXT)
        return

    doc = eb.buffer.document
    range_start, range_end = parse_substitute_range(variables.get('cmd_range'))
    addr_token = range_end or range_start
    if addr_token == '%':
        addr_token = '$'

    insert_index, err = resolve_insert_address(
        addr_token,
        doc.cursor_position_row,
        len(doc.lines),
        getattr(eb, 'visual_marks', None),
    )
    if err is not None or insert_index is None:
        editor.show_message(err)
        return

    if variables.get('force'):
        insert_index = max(0, insert_index - 1)

    clip_data = editor.application.clipboard.get_data()
    if clip_data is None or clip_data.text == '':
        editor.show_message('E353: Nothing in register "')
        return

    clip_text = clip_data.text.replace('\r\n', '\n')
    if clip_text.endswith('\n'):
        clip_text = clip_text[:-1]
    segment = clip_text.split('\n')

    if eb.buffer.text == '':
        new_lines = segment
        cursor_row = len(segment) - 1
    else:
        new_lines = list(doc.lines)
        new_lines[insert_index:insert_index] = segment
        cursor_row = insert_index + len(segment) - 1

    replace_buffer_lines(
        editor,
        eb,
        new_lines,
        cursor_row,
        first_non_blank=True,
        inserted_text=clip_text,
    )


def _resolve_move_copy_dest(editor, eb, dest_addr: str | None) -> int | None:
    if not dest_addr:
        editor.show_message('E14: Invalid address')
        return None
    doc = eb.buffer.document
    dest, err = resolve_insert_address(
        dest_addr,
        doc.cursor_position_row,
        len(doc.lines),
        getattr(eb, 'visual_marks', None),
    )
    if err is not None or dest is None:
        editor.show_message(err)
        return None
    return dest


@_cmd('move')
@_cmd('m')
def move_lines(editor, variables):
    """
    Move addressed lines after destination `{addr}` (`:[range]m {addr}`).
    """
    eb = editor.window_arrangement.active_editor_buffer
    if eb is None:
        return
    if _is_buffer_read_only(eb.buffer):
        editor.show_message(_MODIFIABLE_OFF_TEXT)
        return

    bounds, err = resolve_range_str(variables.get('cmd_range'), eb, default_range='.')
    if err is not None or bounds is None:
        editor.show_message(err)
        return

    dest = _resolve_move_copy_dest(editor, eb, variables.get('dest_addr'))
    if dest is None:
        return

    start_row, end_row = bounds
    line1 = start_row + 1
    line2 = end_row + 1
    if line1 <= dest < line2:
        editor.show_message('E134: Cannot move a range of lines into itself')
        return

    lines = list(eb.buffer.document.lines)
    moved = lines[start_row : end_row + 1]
    remaining = lines[:start_row] + lines[end_row + 1 :]
    insert_at = dest if dest <= start_row else dest - len(moved)
    remaining[insert_at:insert_at] = moved
    cursor_row = insert_at + len(moved) - 1
    replace_buffer_lines(editor, eb, remaining, cursor_row, first_non_blank=True)


@_cmd('copy')
@_cmd('co')
@_cmd('t')
def copy_lines(editor, variables):
    """
    Copy addressed lines after destination `{addr}` (`:[range]t {addr}` / `:co`).
    """
    eb = editor.window_arrangement.active_editor_buffer
    if eb is None:
        return
    if _is_buffer_read_only(eb.buffer):
        editor.show_message(_MODIFIABLE_OFF_TEXT)
        return

    bounds, err = resolve_range_str(variables.get('cmd_range'), eb, default_range='.')
    if err is not None or bounds is None:
        editor.show_message(err)
        return

    dest = _resolve_move_copy_dest(editor, eb, variables.get('dest_addr'))
    if dest is None:
        return

    start_row, end_row = bounds
    lines = list(eb.buffer.document.lines)
    copied = lines[start_row : end_row + 1]
    lines[dest:dest] = copied
    cursor_row = dest + len(copied) - 1
    replace_buffer_lines(
        editor,
        eb,
        lines,
        cursor_row,
        first_non_blank=True,
        inserted_text='\n'.join(copied),
    )


@_cmd('join')
@_cmd('j')
def join_lines(editor, variables):
    """
    Join addressed lines (`:[range]j[!] [count]` / `:join[!]`).
    """
    eb = editor.window_arrangement.active_editor_buffer
    if eb is None:
        return
    if _is_buffer_read_only(eb.buffer):
        editor.show_message(_MODIFIABLE_OFF_TEXT)
        return

    bounds, err = resolve_range_str(variables.get('cmd_range'), eb, default_range='.')
    if err is not None or bounds is None:
        editor.show_message(err)
        return

    lines = list(eb.buffer.document.lines)
    start_row, end_row = bounds
    cmd_arg = variables.get('cmd_arg')
    if cmd_arg:
        count = int(cmd_arg)
        if count < 1:
            editor.show_message(f'E488: Trailing characters: {cmd_arg}')
            return
        start_row = end_row
        end_row = min(len(lines) - 1, start_row + max(1, count - 1))
    elif start_row == end_row:
        end_row = min(len(lines) - 1, start_row + 1)

    if start_row >= end_row:
        return

    segment = lines[start_row : end_row + 1]
    if variables.get('force'):
        joined = ''.join(segment)
    else:
        joined = segment[0]
        for part in segment[1:]:
            stripped = part.lstrip()
            if not stripped:
                continue
            if joined and not joined[-1].isspace():
                joined = joined + ' ' + stripped
            else:
                joined = joined + stripped

    new_lines = lines[:start_row] + [joined] + lines[end_row + 1 :]
    replace_buffer_lines(editor, eb, new_lines, start_row, first_non_blank=True)


_FIRST_INT_RE = re.compile(r'-?\d+')


@_cmd('sort')
def sort_lines(editor: Any, variables: Any) -> None:
    """
    Sort lines in range (`:[range]sort[!] [i][u][r][n]`, default range `%`).
    """
    eb = editor.window_arrangement.active_editor_buffer
    if eb is None:
        return
    if _is_buffer_read_only(eb.buffer):
        editor.show_message(_MODIFIABLE_OFF_TEXT)
        return

    bounds, err = resolve_range_str(variables.get('cmd_range'), eb, default_range='%')
    if err is not None or bounds is None:
        editor.show_message(err)
        return

    reverse = bool(variables.get('force'))
    ignore_case = False
    unique = False
    numeric = False
    flags_text = variables.get('cmd_arg') or ''
    for ch in flags_text:
        match ch:
            case 'i':
                ignore_case = True
            case 'u':
                unique = True
            case 'r':
                reverse = True
            case 'n':
                numeric = True
            case _:
                editor.show_message(f'E488: Trailing characters: {ch}')
                return

    start_row, end_row = bounds
    lines = list(eb.buffer.document.lines)
    segment = lines[start_row : end_row + 1]

    def sort_key(line: str) -> Any:
        text_part = line.casefold() if ignore_case else line
        if numeric:
            m = _FIRST_INT_RE.search(line)
            if m is None:
                return (0, 0, text_part)
            return (1, int(m.group(0)), text_part)
        return text_part

    sorted_segment = sorted(segment, key=sort_key, reverse=reverse)

    if unique:
        deduped: list[str] = []
        seen: set[Any] = set()
        for line in sorted_segment:
            if numeric:
                m = _FIRST_INT_RE.search(line)
                if m is None:
                    dedup_key: Any = (0, line.casefold() if ignore_case else line)
                else:
                    dedup_key = (1, int(m.group(0)))
            else:
                dedup_key = line.casefold() if ignore_case else line
            if dedup_key not in seen:
                seen.add(dedup_key)
                deduped.append(line)
        sorted_segment = deduped

    new_lines = lines[:start_row] + sorted_segment + lines[end_row + 1 :]
    replace_buffer_lines(editor, eb, new_lines, start_row, first_non_blank=True)


def _encode_retab_ws(start_col: int, end_col: int, new_ts: int, expand_tab: bool) -> str:
    width = end_col - start_col
    if width <= 0:
        return ''
    if expand_tab:
        return ' ' * width
    out: list[str] = []
    col = start_col
    while col < end_col:
        next_stop = col + (new_ts - (col % new_ts))
        if next_stop <= end_col:
            out.append('\t')
            col = next_stop
        else:
            out.append(' ' * (end_col - col))
            col = end_col
    return ''.join(out)


def _retab_line(line: str, old_ts: int, new_ts: int, expand_tab: bool, force: bool) -> str:
    from prompt_toolkit.utils import get_cwidth

    out: list[str] = []
    col = 0
    i = 0
    length = len(line)

    while i < length:
        if line[i] in (' ', '\t'):
            start_col = col
            start_i = i
            has_tab = False
            while i < length and line[i] in (' ', '\t'):
                if line[i] == '\t':
                    has_tab = True
                    col += old_ts - (col % old_ts)
                else:
                    col += 1
                i += 1
            run = line[start_i:i]
            if expand_tab:
                if has_tab:
                    out.append(_encode_retab_ws(start_col, col, new_ts, True))
                else:
                    out.append(run)
            elif start_col == 0 or has_tab or (force and len(run) >= 2):
                out.append(_encode_retab_ws(start_col, col, new_ts, False))
            else:
                out.append(run)
        else:
            ch = line[i]
            out.append(ch)
            col += max(0, get_cwidth(ch))
            i += 1

    return ''.join(out)


@_cmd('retab')
def retab_lines(editor, variables):
    """
    Re-expand tabs and spaces in range (`:[range]retab[!] [N]`, default `%`).
    """
    eb = editor.window_arrangement.active_editor_buffer
    if eb is None:
        return
    if _is_buffer_read_only(eb.buffer):
        editor.show_message(_MODIFIABLE_OFF_TEXT)
        return

    bounds, err = resolve_range_str(variables.get('cmd_range'), eb, default_range='%')
    if err is not None or bounds is None:
        editor.show_message(err)
        return

    old_ts = eb.tabstop
    cmd_arg = variables.get('cmd_arg')
    if cmd_arg is not None:
        try:
            new_ts = int(cmd_arg)
        except ValueError:
            editor.show_message(f'E474: Invalid argument: {cmd_arg}')
            return
        if new_ts < 1:
            editor.show_message(f'E474: Invalid argument: {cmd_arg}')
            return
    else:
        new_ts = old_ts

    force = bool(variables.get('force'))
    expand_tab = bool(getattr(editor, 'expand_tab', True))
    start_row, end_row = bounds
    lines = list(eb.buffer.document.lines)
    for row in range(start_row, end_row + 1):
        lines[row] = _retab_line(lines[row], old_ts, new_ts, expand_tab, force)

    if cmd_arg is not None:
        eb.tabstop = new_ts

    replace_buffer_lines(editor, eb, lines, start_row, first_non_blank=True)


_GLOBAL_SUB_RE = re.compile(r'^(?P<name>substitute|s)(?P<args>\s*[^a-zA-Z0-9_\\\s"].*)?$')
_GLOBAL_MOVE_COPY_RE = re.compile(r'^(?P<name>move|copy|co|m|t)\s*(?P<dest>.+)?$')
_GLOBAL_NORM_RE = re.compile(r'^(?P<name>normal|norm)(?P<force>!?)\s+(?P<keys>.+)$')


def _parse_global_args(global_args: str) -> tuple[str, str]:
    """
    Parse `/<pattern>/<sub_cmd>` (supporting arbitrary non-alphanumeric
    delimiters with `\\<delim>` unescaping) into `(pattern_text, sub_cmd)`.
    """
    stripped = global_args.lstrip()
    delim = stripped[0]
    pattern_chars: list[str] = []
    i = 1
    length = len(stripped)
    while i < length:
        ch = stripped[i]
        if ch == '\\' and i + 1 < length:
            next_ch = stripped[i + 1]
            if next_ch == delim:
                pattern_chars.append(delim)
            else:
                pattern_chars.append(ch)
                pattern_chars.append(next_ch)
            i += 2
            continue
        if ch == delim:
            return ''.join(pattern_chars), stripped[i + 1 :].strip()
        pattern_chars.append(ch)
        i += 1
    return ''.join(pattern_chars), ''


def _global_execute_substitute(
    editor: Any, eb: Any, lines: list[str], marked_rows: list[int], sub_args: str | None
) -> None:
    search, replace, flags = parse_substitute_args(sub_args)
    search_state = editor.application.current_search_state
    if not search:
        search = search_state.text
    if not search:
        editor.show_message('No previous regular expression')
        return
    if replace is None:
        replace = editor.last_substitute_text
    if _UNSAFE_NESTED_QUANTIFIER.search(_strip_regex_literals(search)):
        editor.show_message('Unsafe pattern (nested quantifier): %s' % search)
        return

    options, flag_err = _parse_substitute_flags(
        flags,
        default_ignore_case=_should_ignore_case(editor, search),
    )
    if flag_err is not None or options is None:
        editor.show_message(flag_err)
        return

    regex_flags = re.IGNORECASE if options.ignore_case else 0
    try:
        sub_pat = re.compile(search, flags=regex_flags)
    except re.error as exc:
        editor.show_message('Invalid regex: %s' % exc)
        return

    new_lines = list(lines)
    total_matches = 0
    matched_lines = 0
    last_row = marked_rows[-1]
    for row in marked_rows:
        try:
            result, count = sub_pat.subn(replace, new_lines[row], count=options.sub_count)
        except re.error as exc:
            editor.show_message('Invalid regex: %s' % exc)
            return
        if count > 0:
            new_lines[row] = result
            total_matches += count
            matched_lines += 1
            last_row = row

    if total_matches == 0:
        editor.show_message('Pattern not found')
        return
    if options.report_only:
        match_word = 'match' if total_matches == 1 else 'matches'
        line_word = 'line' if matched_lines == 1 else 'lines'
        editor.show_message(f'{total_matches} {match_word} on {matched_lines} {line_word}')
        return
    if not replace_buffer_lines(editor, eb, new_lines, last_row, first_non_blank=True):
        return
    editor.last_substitute_text = replace
    if options.print_last:
        editor.show_message(eb.buffer.document.lines[last_row])


def _global_execute_move_or_copy(
    editor: Any,
    eb: Any,
    lines: list[str],
    marked_rows: list[int],
    is_move: bool,
    dest_addr: str | None,
) -> None:
    if _is_buffer_read_only(eb.buffer):
        editor.show_message(_MODIFIABLE_OFF_TEXT)
        return
    if not dest_addr or not dest_addr.strip():
        editor.show_message('E14: Invalid address')
        return

    visual_marks = getattr(eb, 'visual_marks', None)
    tagged: list[tuple[int | None, str]] = [(idx, text) for idx, text in enumerate(lines)]
    last_row = marked_rows[0]

    for line_id in marked_rows:
        cur_pos = next(pos for pos, (tid, _) in enumerate(tagged) if tid == line_id)
        dest, err = resolve_insert_address(dest_addr, cur_pos, len(tagged), visual_marks)
        if err is not None or dest is None:
            editor.show_message(err)
            return
        if is_move:
            item = tagged.pop(cur_pos)
            insert_at = dest if dest <= cur_pos else dest - 1
            tagged.insert(insert_at, item)
            last_row = insert_at
        else:
            tagged.insert(dest, (None, tagged[cur_pos][1]))
            last_row = dest

    result_lines = [text for _, text in tagged]
    replace_buffer_lines(editor, eb, result_lines, last_row, first_non_blank=True)


@_cmd('vglobal')
@_cmd('global')
@_cmd('g')
@_cmd('v')
def global_command(editor: Any, variables: Any) -> None:
    """
    Execute an Ex sub-command on lines matching (`:g` / `:global`) or not
    matching (`:g!` / `:v` / `:vglobal`) a regex pattern (`:[range]g[!]/pat/[cmd]`).
    """
    command = variables.get('command')
    force = bool(variables.get('force'))
    if command in ('v', 'vglobal') and force:
        editor.show_message('No ! allowed')
        return

    global_args = variables.get('global_args')
    if not global_args:
        editor.show_message('E148: Regular expression missing from global')
        return

    eb = editor.window_arrangement.active_editor_buffer
    if eb is None:
        return

    bounds, err = resolve_range_str(variables.get('cmd_range'), eb, default_range='%')
    if err is not None or bounds is None:
        editor.show_message(err)
        return

    pattern_text, sub_cmd = _parse_global_args(global_args)
    search_state = editor.application.current_search_state
    if not pattern_text:
        pattern_text = search_state.text
    if not pattern_text:
        editor.show_message('No previous regular expression')
        return

    if _UNSAFE_NESTED_QUANTIFIER.search(_strip_regex_literals(pattern_text)):
        editor.show_message('Unsafe pattern (nested quantifier): %s' % pattern_text)
        return

    regex_flags = re.IGNORECASE if _should_ignore_case(editor, pattern_text) else 0
    try:
        pattern = re.compile(pattern_text, flags=regex_flags)
    except re.error as exc:
        editor.show_message('Invalid regex: %s' % exc)
        return

    invert = command in ('v', 'vglobal') or force
    start_row, end_row = bounds
    lines = list(eb.buffer.document.lines)
    marked_rows = [
        row
        for row in range(start_row, end_row + 1)
        if (pattern.search(lines[row]) is None) == invert
    ]

    if not marked_rows:
        editor.show_message('Pattern not found')
        return

    search_state.text = pattern_text
    editor.clear_hlsearch_suppression()

    sub = sub_cmd.strip() or 'p'
    if sub in ('p', 'print'):
        output = '\n'.join(lines[row] for row in marked_rows)
        editor.show_info(output, title='[Global]')
        return

    if sub in ('d', 'delete'):
        if _is_buffer_read_only(eb.buffer):
            editor.show_message(_MODIFIABLE_OFF_TEXT)
            return
        marked_set = set(marked_rows)
        deleted = [lines[row] for row in marked_rows]
        editor.application.clipboard.set_data(
            ClipboardData('\n'.join(deleted), SelectionType.LINES)
        )
        remaining = [line for idx, line in enumerate(lines) if idx not in marked_set]
        if not remaining:
            remaining = ['']
        cursor_row = min(marked_rows[0], len(remaining) - 1)
        if not replace_buffer_lines(editor, eb, remaining, cursor_row, first_non_blank=True):
            return
        if len(deleted) >= 2:
            editor.show_message(f'{len(deleted)} fewer lines')
        return

    if sub in ('y', 'yank'):
        yanked = [lines[row] for row in marked_rows]
        editor.application.clipboard.set_data(ClipboardData('\n'.join(yanked), SelectionType.LINES))
        line_word = 'line' if len(yanked) == 1 else 'lines'
        editor.show_message(f'{len(yanked)} {line_word} yanked')
        return

    sub_match = _GLOBAL_SUB_RE.match(sub)
    if sub_match is not None:
        _global_execute_substitute(editor, eb, lines, marked_rows, sub_match.group('args'))
        return

    mc_match = _GLOBAL_MOVE_COPY_RE.match(sub)
    if mc_match is not None:
        is_move = mc_match.group('name') in ('m', 'move')
        _global_execute_move_or_copy(
            editor, eb, lines, marked_rows, is_move, mc_match.group('dest')
        )
        return

    norm_match = _GLOBAL_NORM_RE.match(sub)
    if norm_match is not None:
        _execute_normal_on_rows(
            editor,
            eb,
            marked_rows,
            norm_match.group('keys'),
            ignore_user_mappings=bool(norm_match.group('force')),
            track_line_delta=True,
        )
        return

    editor.show_message(f'Not an editor command: {sub}')


def _parse_normal_keys(keys_str: str) -> list[Any]:
    """Convert raw Ex `:normal` key string into `prompt_toolkit` `KeyPress` items."""
    from prompt_toolkit.key_binding.key_processor import KeyPress
    from prompt_toolkit.keys import Keys

    key_presses: list[Any] = []
    for ch in keys_str:
        code = ord(ch)
        if ch == '\x1b':
            key_presses.append(KeyPress(Keys.Escape, ch))
        elif ch in ('\r', '\n'):
            key_presses.append(KeyPress(Keys.ControlM, '\r'))
        elif ch == '\t':
            key_presses.append(KeyPress(Keys.ControlI, ch))
        elif ch in ('\x7f', '\x08'):
            key_presses.append(KeyPress(Keys.ControlH, ch))
        elif 1 <= code <= 26:
            key_presses.append(KeyPress(Keys(f'c-{chr(code + 96)}'), ch))
        else:
            key_presses.append(KeyPress(ch, ch))
    return key_presses


def _build_normal_key_processor(editor: Any, ignore_user_mappings: bool) -> Any:
    """
    Build an isolated `KeyProcessor` for `:normal` so key replay never
    interferes with the active outer `Application.key_processor` and `:norm!`
    bypasses user-added `editor.add_key_binding` hooks.
    """
    from prompt_toolkit.key_binding.defaults import load_key_bindings
    from prompt_toolkit.key_binding.key_bindings import KeyBindings, merge_key_bindings
    from prompt_toolkit.key_binding.key_processor import KeyProcessor

    if ignore_user_mappings:
        builtin_count = getattr(
            editor, '_builtin_key_binding_count', len(editor.key_bindings.bindings)
        )
        builtin_kb = KeyBindings()
        builtin_kb.bindings.extend(editor.key_bindings.bindings[:builtin_count])
        bindings = merge_key_bindings([load_key_bindings(), builtin_kb])
    else:
        bindings = merge_key_bindings([load_key_bindings(), editor.key_bindings])
    return KeyProcessor(bindings)


def _execute_normal_on_rows(
    editor: Any,
    eb: Any,
    rows: list[int],
    keys_str: str,
    *,
    ignore_user_mappings: bool,
    track_line_delta: bool = False,
) -> None:
    """
    Replay Normal-mode `keys_str` on each row in `rows` starting at column 0,
    wrapped in a single undo snapshot and guarded against recursion, read-only
    buffers, and unfinished Insert/selection states (Decision D4).
    """
    from prompt_toolkit.application.current import set_app
    from prompt_toolkit.key_binding.key_processor import KeyPress
    from prompt_toolkit.key_binding.vi_state import InputMode
    from prompt_toolkit.keys import Keys

    if _is_buffer_read_only(eb.buffer):
        editor.show_message(_MODIFIABLE_OFF_TEXT)
        return

    depth = getattr(editor, '_normal_command_depth', 0)
    if depth >= 1:
        editor.show_message('E169: Command too recursive')
        return

    key_presses = _parse_normal_keys(keys_str)
    if not key_presses:
        editor.show_message('E471: Argument required')
        return

    kp = _build_normal_key_processor(editor, ignore_user_mappings)
    app = editor.application
    vi_state = app.vi_state
    buff = eb.buffer

    # Ensure the target editor buffer has focus before feeding Normal keys.
    active_win = editor.window_arrangement.active_pt_window
    if active_win is not None:
        app.layout.focus(active_win)
    else:
        editor.sync_with_prompt_toolkit()

    buff.save_to_undo_stack()
    editor._normal_command_depth = depth + 1
    if hasattr(buff, '_suppress_undo_saves'):
        buff._suppress_undo_saves += 1

    try:
        with set_app(app):
            line_offset = 0
            for raw_row in rows:
                target_row = raw_row + line_offset if track_line_delta else raw_row
                doc = buff.document
                if target_row < 0 or target_row >= len(doc.lines):
                    continue
                before_line_count = len(doc.lines)

                vi_state.input_mode = InputMode.NAVIGATION
                vi_state.operator_func = None
                vi_state.operator_arg = None
                buff.exit_selection()
                buff.cursor_position = doc.translate_row_col_to_index(target_row, 0)

                kp.reset()
                kp.feed_multiple(list(key_presses))
                kp.process_keys()

                # Auto-exit Insert/Replace mode at the end of each line (like Vim `:norm`).
                if vi_state.input_mode in (InputMode.INSERT, InputMode.REPLACE):
                    kp.feed(KeyPress(Keys.Escape, '\x1b'))
                    kp.process_keys()

                # Recover cleanly if the sequence opened command mode or left state pending.
                if app.layout.has_focus(editor.command_buffer):
                    editor.leave_command_mode()
                vi_state.input_mode = InputMode.NAVIGATION
                vi_state.operator_func = None
                vi_state.operator_arg = None
                buff.exit_selection()
                kp.reset()

                if track_line_delta:
                    line_offset += len(buff.document.lines) - before_line_count
    finally:
        if hasattr(buff, '_suppress_undo_saves'):
            buff._suppress_undo_saves = max(0, buff._suppress_undo_saves - 1)
        editor._normal_command_depth = max(0, getattr(editor, '_normal_command_depth', 1) - 1)
        vi_state.input_mode = InputMode.NAVIGATION
        vi_state.operator_func = None
        vi_state.operator_arg = None
        buff.exit_selection()
        note_document_text(buff.text)


@_cmd('normal')
@_cmd('norm')
def normal_command(editor: Any, variables: Any) -> None:
    """
    Execute Normal-mode commands on each line in the addressed range
    (`:[range]norm[!] {keys}` / `:normal[!] {keys}`, Decision D4).
    """
    norm_keys = variables.get('norm_keys')
    if not norm_keys:
        editor.show_message('E471: Argument required')
        return

    eb = editor.window_arrangement.active_editor_buffer
    if eb is None:
        return

    bounds, err = resolve_range_str(variables.get('cmd_range'), eb, default_range='.')
    if err is not None or bounds is None:
        editor.show_message(err)
        return

    start_row, end_row = bounds
    _execute_normal_on_rows(
        editor,
        eb,
        list(range(start_row, end_row + 1)),
        norm_keys,
        ignore_user_mappings=bool(variables.get('force')),
    )
