"""
Buffer navigation, window split/close, and quit Ex commands.
"""

from __future__ import annotations

from typing import Any

from .common import (
    NO_FILE_NAME_TEXT,
    NO_WRITE_SINCE_LAST_CHANGE_TEXT,
    buffer_visible_count,
    confirm_unsaved_changes,
    has_sole_dirty_buffer,
    show_info_lines,
)
from .registry import _cmd, cmd, location_cmd


def _has_multiple_windows(wa: Any) -> bool:
    active_tab = wa.active_tab
    return len(wa.tab_pages) > 1 or (active_tab is not None and active_tab.window_count() > 1)


def _can_close_active_window(editor: Any, force: bool) -> bool:
    wa = editor.window_arrangement
    active_eb = wa.active_editor_buffer
    if active_eb is not None and buffer_visible_count(wa, active_eb) <= 1:
        return confirm_unsaved_changes(editor, force)
    return True


@cmd('bnext', accepts_force=True)
@cmd('bn', accepts_force=True)
def _bn(editor: Any, force: bool = False) -> None:
    """
    Go to next buffer.
    """
    if confirm_unsaved_changes(editor, force):
        editor.window_arrangement.go_to_next_buffer()


@cmd('bprevious', accepts_force=True)
@cmd('bprev', accepts_force=True)
@cmd('bp', accepts_force=True)
def _bp(editor: Any, force: bool = False) -> None:
    """
    Go to previous buffer.
    """
    if confirm_unsaved_changes(editor, force):
        editor.window_arrangement.go_to_previous_buffer()


@cmd('bfirst', accepts_force=True)
@cmd('bf', accepts_force=True)
def _bfirst(editor: Any, force: bool = False) -> None:
    """
    Go to first buffer (`:bf` / `:bfirst`).
    """
    if confirm_unsaved_changes(editor, force):
        editor.window_arrangement.go_to_first_buffer()


@cmd('blast', accepts_force=True)
@cmd('bl', accepts_force=True)
def _blast(editor: Any, force: bool = False) -> None:
    """
    Go to last buffer (`:bl` / `:blast`).
    """
    if confirm_unsaved_changes(editor, force):
        editor.window_arrangement.go_to_last_buffer()


@cmd('next', accepts_force=True)
@cmd('n', accepts_force=True)
def _next(editor: Any, force: bool = False) -> None:
    """
    Go to next file: the next tab page when several are open (keeping the
    tab bar in sync), else the next buffer (docs/bugs/126.md).
    """
    window_arrangement = editor.window_arrangement
    if len(window_arrangement.tab_pages) > 1:
        window_arrangement.go_to_next_tab()
    elif confirm_unsaved_changes(editor, force):
        window_arrangement.go_to_next_buffer()


@cmd('previous', accepts_force=True)
@cmd('prev', accepts_force=True)
@cmd('N', accepts_force=True)
def _previous(editor: Any, force: bool = False) -> None:
    """
    Go to previous file: the previous tab page when several are open,
    else the previous buffer (docs/bugs/126.md).
    """
    window_arrangement = editor.window_arrangement
    if len(window_arrangement.tab_pages) > 1:
        window_arrangement.go_to_previous_tab()
    elif confirm_unsaved_changes(editor, force):
        window_arrangement.go_to_previous_buffer()


@cmd('on', accepts_force=True)
@cmd('only', accepts_force=True)
def only(editor: Any, force: bool = False) -> None:
    """
    Keep only the current window in the current tab (`:only`), leaving other
    tab pages untouched (docs/bugs/132.md).
    """
    wa = editor.window_arrangement
    active_tab = wa.active_tab
    if active_tab is None:
        return
    if (
        not force
        and active_tab.window_count() > 1
        and has_sole_dirty_buffer(wa, active_tab.windows(), exclude_eb=wa.active_editor_buffer)
    ):
        editor.show_message('E445: Other window contains changes')
        return
    wa.keep_only_current_window()


@cmd('clo', accepts_force=True)
@cmd('close', accepts_force=True)
def close_window(editor: Any, force: bool = False) -> None:
    """
    Close the current window (without exiting on the last window).

    Matches Vim `:close`: reports E444 on the last remaining window and guards
    unsaved changes when closing the last window showing a dirty buffer unless
    `!` is passed.
    """
    wa = editor.window_arrangement
    if wa.active_tab is None:
        return

    if not _has_multiple_windows(wa):
        wa.close_window()
        return

    if _can_close_active_window(editor, force):
        wa.close_window()


@cmd('hide')
def hide(editor: Any) -> None:
    """
    Hide the current window.
    """
    editor.window_arrangement.close_window()


@location_cmd('sp')
@location_cmd('split')
def horizontal_split(editor: Any, location: str | None) -> None:
    """
    Split window horizontally.
    """
    editor.window_arrangement.hsplit(location or None)


@location_cmd('vsp')
@location_cmd('vsplit')
def vertical_split(editor: Any, location: str | None) -> None:
    """
    Split window vertically.
    """
    editor.window_arrangement.vsplit(location or None)


@cmd('new')
def new_buffer(editor: Any) -> None:
    """
    Create new buffer.
    """
    editor.window_arrangement.hsplit(new=True)


@cmd('vnew')
def new_vertical_buffer(editor: Any) -> None:
    """
    Create new buffer, splitting vertically.
    """
    editor.window_arrangement.vsplit(new=True)


@location_cmd('badd')
def buffer_add(editor: Any, location: str | None) -> None:
    """
    Add a new buffer.
    """
    editor.window_arrangement.open_buffer(location)


def buffer_list_lines(editor: Any) -> list[str]:
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


@cmd('ls')
@cmd('files')
@cmd('buffers')
def buffer_list(editor: Any) -> None:
    """
    List all open buffers in a read-only split inside vith (`:ls` / `:files` / `:buffers`).
    """
    show_info_lines(
        editor,
        buffer_list_lines(editor),
        title='[Buffers]',
        empty_message='No open buffers',
    )


@_cmd('b')
@_cmd('buffer')
def _buffer(editor: Any, variables: Any) -> None:
    """
    Go to one of the open buffers.
    """
    buffer_name = variables.get('buffer_name')
    if buffer_name and confirm_unsaved_changes(editor, bool(variables['force'])):
        editor.window_arrangement.go_to_buffer(buffer_name)


@_cmd('sbuffer')
@_cmd('sb')
def split_buffer(editor: Any, variables: Any) -> None:
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
def buffer_wipe(editor: Any, variables: Any) -> None:
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
            editor.show_message(NO_WRITE_SINCE_LAST_CHANGE_TEXT)
            return
        wa.close_buffer(target=target)
    elif confirm_unsaved_changes(editor, force):
        editor.window_arrangement.close_buffer()


@cmd('enew', accepts_force=True)
@cmd('ene', accepts_force=True)
def buffer_new_empty(editor: Any, force: bool = False) -> None:
    """
    Open a new untitled buffer in the current window (`:enew` / `:ene`).
    """
    if confirm_unsaved_changes(editor, force):
        editor.window_arrangement.open_buffer(None, show_in_current_window=True)


@location_cmd('o', accepts_force=True)
@location_cmd('open', accepts_force=True)
@location_cmd('e', accepts_force=True)
@location_cmd('edit', accepts_force=True)
def buffer_edit(editor: Any, location: str | None, force: bool = False) -> None:
    """
    Edit new buffer.
    """
    if location is None:
        # Edit/open without a location will reload the current file, if there are
        # no changes.
        eb = editor.window_arrangement.active_editor_buffer
        if eb is None or eb.location is None:
            editor.show_message(NO_FILE_NAME_TEXT)
        elif confirm_unsaved_changes(editor, force):
            eb.reload()
    elif confirm_unsaved_changes(editor, force):
        editor.window_arrangement.open_buffer(location, show_in_current_window=True)


def _exit_if_no_unsaved_changes(editor: Any, force: bool) -> bool:
    """Exit the application unless an unsaved buffer blocks it (`:qa` rule)."""
    window_arrangement = editor.window_arrangement
    if not force and any(eb.has_unsaved_changes for eb in window_arrangement.editor_buffers):
        editor.show_message(NO_WRITE_SINCE_LAST_CHANGE_TEXT)
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
    if _has_multiple_windows(wa):
        if not _can_close_active_window(editor, force):
            return False
        wa.close_window()
        return True

    return _exit_if_no_unsaved_changes(editor, force)


@cmd('q', accepts_force=True)
@cmd('quit', accepts_force=True)
def quit(editor: Any, all_: bool = False, force: bool = False) -> bool:
    """
    Quit. With several windows/tabs open this closes the current window (Vim
    semantics); the editor exits only on the last window.
    """
    if all_:
        return _exit_if_no_unsaved_changes(editor, force)
    return close_or_exit(editor, force)


@cmd('qa', accepts_force=True)
@cmd('qall', accepts_force=True)
def quit_all(editor: Any, force: bool = False) -> None:
    """
    Quit all.
    """
    quit(editor, all_=True, force=force)


@cmd('cq')
def quit_nonzero(editor: Any) -> None:
    """
    Quit with non zero exit status.
    """
    # Note: the try/finally in `prompt_toolkit.Interface.read_input`
    # will ensure that the render output is reset, leaving the alternate
    # screen before quitting.
    editor.application.exit(result=1)
