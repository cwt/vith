"""
File I/O, shell filter, directory, and config-sourcing Ex commands
(`:r`, `:f`, `:w`, `:[range]!`, `:sav`, `:up`, `:x`, `:wq`, `:wa`, `:xa`,
`:wqa`, `:pwd`, `:cd`, `:source`).
"""

from __future__ import annotations

import os
from typing import Any

from vith.io.base import find_backend
from vith.thai import note_document_text

from .buffers import quit
from .common import (
    NO_FILE_NAME_TEXT,
    active_writable_buffer,
    insert_lines_at,
    resolve_command_range,
    run_shell,
    split_text_to_lines,
)
from .ranges import is_buffer_read_only, replace_buffer_lines, resolve_insert_address
from .registry import _cmd, cmd, location_cmd


def _format_file_status(eb: Any) -> str:
    """Format Vim-style `:file` status line for `eb`."""
    display_name = eb.get_display_name()
    flags = []
    if is_buffer_read_only(eb.buffer):
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


def _read_location_text(editor: Any, location: str) -> str | None:
    """
    Read text from `location` through `editor.io_backends` without mutating any
    `EditorBuffer` metadata (`location`, `is_new`, `isdir`, `encoding`).
    """
    io = find_backend(editor.io_backends, location)
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


def _read_shell_text(editor: Any, shell_cmd: str) -> tuple[str | None, int | None]:
    """
    Execute `shell_cmd` capturing combined stdout/stderr (matching Unix Vim's
    `shellredir` `2>&1`, Decision D3). Returns `(output, returncode)` or
    `(None, None)` on launch/timeout failure.
    """
    proc = run_shell(editor, shell_cmd)
    if proc is None:
        return None, None
    return proc.stdout or '', proc.returncode


@_cmd('r')
@_cmd('read')
def read_command(editor: Any, variables: Any) -> None:
    """
    Insert a file or shell command output after an addressed line
    (`:[addr]r [file]` / `:[addr]r !{cmd}`).
    """
    eb = active_writable_buffer(editor)
    if eb is None:
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
            editor.show_message(NO_FILE_NAME_TEXT)
            return
        raw_text = _read_location_text(editor, target)
        if raw_text is None:
            return
        returncode = 0
        source_label = target

    normalized, inserted_lines = split_text_to_lines(raw_text)
    if not inserted_lines:
        if returncode != 0:
            editor.show_message(f'shell returned {returncode}')
        else:
            editor.show_message(f'"{source_label}" 0L')
        return

    new_lines, first_row = insert_lines_at(eb, insert_index, inserted_lines)
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
def file_info(editor: Any, location: str | None) -> None:
    """
    Show current file name/status (`:f` / `:file`), or rename the current
    buffer's location (`:file {name}`) without writing to disk.
    """
    eb = editor.window_arrangement.active_editor_buffer
    if eb is None:
        editor.show_message(NO_FILE_NAME_TEXT)
        return

    if location is not None:
        # `:f {name}` renames the buffer; normalize metadata the same way
        # writes do (docs/bugs/147.md).
        normalized = os.path.abspath(os.path.expanduser(location))
        eb.location = normalized
        eb.is_new = not os.path.exists(normalized)
        eb.isdir = os.path.isdir(normalized)
        eb._detected_filetype = None
        eb.run_reporter()
        note_document_text(eb.location)
        editor.update_title()

    editor.show_message(_format_file_status(eb))


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


def write(editor: Any, location: str | None, force: bool = False) -> bool:
    """
    Write file. Returns whether the buffer was written.
    """
    eb = editor.window_arrangement.active_editor_buffer
    if eb is None or (location is None and eb.location is None):
        editor.show_message(NO_FILE_NAME_TEXT)
        return False
    if _reject_existing_write_target(editor, eb, location, force):
        return False
    return bool(eb.write(location, force=force))


def _write_with_encoding_fallback(
    editor: Any,
    io: Any,
    target: str,
    payload: str,
    encoding: str,
) -> bool:
    """Write `payload` via `io`, falling back to UTF-8 on encoding errors."""
    try:
        try:
            io.write(target, payload, encoding)
        except UnicodeEncodeError:
            io.write(target, payload, 'utf-8')
    except Exception as exc:
        editor.show_message('%s' % exc)
        return False
    return True


def _write_payload_to_location(editor: Any, eb: Any, target: str, payload: str) -> bool:
    """Write `payload` to `target` via `editor.io_backends` without mutating `eb`."""
    io = find_backend(editor.io_backends, target)
    if io is None:
        editor.show_message('Unknown location: %r' % target)
        return False
    return _write_with_encoding_fallback(editor, io, target, payload, eb.encoding)


def _write_pipe_shell(editor: Any, shell_cmd: str, payload: str) -> None:
    """Pipe `payload` to `shell_cmd`'s stdin (`:[range]w !{cmd}`)."""
    cmd_text = shell_cmd.strip()
    if not cmd_text:
        editor.show_message('E34: No previous command')
        return
    proc = run_shell(editor, cmd_text, payload=payload)
    if proc is None:
        return

    output = (proc.stdout or '').replace('\r\n', '\n').rstrip('\n')
    if output:
        if '\n' in output:
            editor.show_info(output, title=f'[!{cmd_text}]')
        else:
            editor.show_message(output)
    if proc.returncode != 0:
        editor.show_message(f'shell returned {proc.returncode}')


def _file_payload(eb: Any, lines: list[str]) -> str:
    """Join lines with the buffer's fileformat EOL (docs/bugs/140.md)."""
    return eb.line_ending().join(lines) + eb.line_ending()


def _write_append_file(editor: Any, eb: Any, target: str, payload: str, force: bool) -> None:
    """
    Append `payload` to `target` through the editor's I/O backends
    (`:[range]w[!] >> {file}`, docs/bugs/147.md).
    """
    io = find_backend(editor.io_backends, target)
    if io is None:
        editor.show_message('Unknown location: %r' % target)
        return
    exists = io.exists(target)
    if exists is False and not force:
        editor.show_message('File does not exist (add ! to create)')
        return
    try:
        old_text = ''
        if exists:
            old_text, _encoding = io.read(target)
    except Exception as exc:
        editor.show_message('%s' % exc)
        return
    _write_with_encoding_fallback(editor, io, target, old_text + payload, eb.encoding)


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

    if write_shell is None and write_append is None and cmd_range is None:
        write(editor, location, force=force)
        return

    eb = editor.window_arrangement.active_editor_buffer
    if eb is None:
        if write_shell is None and write_append is None:
            editor.show_message(NO_FILE_NAME_TEXT)
        return

    bounds = resolve_command_range(editor, eb, cmd_range, default_range='%')
    if bounds is None:
        return
    start_row, end_row = bounds
    lines = eb.buffer.document.lines
    selected = lines[start_row : end_row + 1]

    if write_shell is not None:
        payload = '\n'.join(selected) + '\n'
        _write_pipe_shell(editor, write_shell, payload)
        return

    if write_append is not None:
        payload = _file_payload(eb, selected)
        _write_append_file(editor, eb, write_append, payload, force=force)
        return

    is_partial = start_row > 0 or end_row < len(lines) - 1
    if not is_partial:
        write(editor, location, force=force)
        return
    target = location or eb.location
    if not target:
        editor.show_message(NO_FILE_NAME_TEXT)
        return
    if not force and location is None:
        editor.show_message('Use ! to write partial buffer')
        return
    if _reject_existing_write_target(editor, eb, location, force):
        return
    _write_payload_to_location(editor, eb, target, _file_payload(eb, selected))


def filter_range_command(editor: Any, range_str: str | None, shell_cmd: str) -> None:
    """
    Pipe addressed lines through an external shell command and replace the
    range with combined output (`:[range]!{cmd}`, Decision D3).
    """
    eb = active_writable_buffer(editor)
    if eb is None:
        return

    cmd_text = shell_cmd.strip()
    if not cmd_text:
        editor.show_message('E34: No previous command')
        return

    bounds = resolve_command_range(editor, eb, range_str, default_range='.')
    if bounds is None:
        return

    start_row, end_row = bounds
    lines = list(eb.buffer.document.lines)
    stdin_text = '\n'.join(lines[start_row : end_row + 1]) + '\n'

    proc = run_shell(editor, cmd_text, payload=stdin_text)
    if proc is None:
        return

    output, replacement = split_text_to_lines(proc.stdout or '')
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
def save_as(editor: Any, location: str | None, force: bool = False) -> bool:
    """
    Write the active buffer to `location` and rename the buffer to it
    (`:sav[!] {file}` / `:saveas[!] {file}`).
    """
    if not location:
        editor.show_message('E471: Argument required')
        return False
    eb = editor.window_arrangement.active_editor_buffer
    if eb is None:
        editor.show_message(NO_FILE_NAME_TEXT)
        return False
    if _reject_existing_write_target(editor, eb, location, force):
        return False
    return bool(eb.write(location, force=force, rename=True))


@location_cmd('up', accepts_force=True)
@location_cmd('update', accepts_force=True)
def update(editor: Any, location: str | None, force: bool = False) -> bool:
    """
    Write the active buffer only when modified or given a destination path
    (`:up` / `:update`). Returns whether the buffer is cleanly up to date.
    """
    eb = editor.window_arrangement.active_editor_buffer
    if eb is None:
        if location is not None:
            editor.show_message(NO_FILE_NAME_TEXT)
            return False
        return True
    if location is not None or eb.has_unsaved_changes:
        return write(editor, location, force=force)
    return True


@location_cmd('x', accepts_force=True)
@location_cmd('xit', accepts_force=True)
@location_cmd('exit', accepts_force=True)
def write_if_modified_and_quit(editor: Any, location: str | None, force: bool = False) -> bool:
    """
    Write the active buffer if modified (or when a location is provided)
    and close the current window / exit (`:x` / `:xit` / `:exit`).
    """
    if not update(editor, location, force=force):
        return False
    return quit(editor, all_=False, force=force)


@location_cmd('wq', accepts_force=True)
def write_and_quit(editor: Any, location: str | None, force: bool = False) -> bool:
    """
    Write file and quit. Does not quit when the write failed.
    """
    if write(editor, location, force=force):
        return quit(editor, all_=False, force=force)
    return False


@cmd('wall', accepts_force=True)
@cmd('wa', accepts_force=True)
def write_all(editor: Any, force: bool = False) -> bool:
    """
    Write every buffer with unsaved changes (`:wa` / `:wall`).
    Returns whether every dirty buffer was written successfully.
    """
    for eb in editor.window_arrangement.editor_buffers:
        if eb.has_unsaved_changes:
            if eb.location is None:
                editor.show_message(NO_FILE_NAME_TEXT)
                return False
            if not eb.write(force=force):
                return False
    return True


@cmd('xall', accepts_force=True)
@cmd('xa', accepts_force=True)
@cmd('wqall', accepts_force=True)
@cmd('wqa', accepts_force=True)
def write_and_quit_all(editor: Any, force: bool = False) -> None:
    """
    Write every buffer with unsaved changes and quit. Does not quit when any
    write failed (docs/bugs/067.md).
    """
    if write_all(editor, force=force):
        editor.application.exit()


@cmd('pwd')
def pwd(editor: Any) -> None:
    """Print working directory."""
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
def cd(editor: Any, location: str | None) -> None:
    """Change working directory."""
    target = os.path.expanduser(location or '~')
    try:
        os.chdir(target)
    except OSError as exc:
        editor.show_message('{}'.format(exc))
