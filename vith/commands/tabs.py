"""
Tab page management Ex commands (`:tabe`, `:tabc`, `:tabn`, `:tabp`, `:tabo`,
`:tabfir`, `:tabl`, `:tabm`, `:tabs`).
"""

from __future__ import annotations

from typing import Any

from .common import E37_NO_WRITE_TEXT, has_sole_dirty_buffer, show_info_lines
from .registry import _cmd, cmd, location_cmd


@location_cmd('tabe')
@location_cmd('tabedit')
@location_cmd('tabnew')
def tab_new(editor: Any, location: str | None) -> None:
    """
    Create new tab page.
    """
    editor.window_arrangement.create_tab(location or None)


@cmd('tabclose', accepts_force=True)
@cmd('tabc', accepts_force=True)
def tab_close(editor: Any, force: bool = False) -> None:
    """
    Close tab page (`:tabc` / `:tabclose`), with an E37 dirty-buffer guard
    (docs/bugs/132.md).
    """
    wa = editor.window_arrangement
    active_tab = wa.active_tab
    if active_tab is None or len(wa.tab_pages) <= 1:
        return
    if not force and has_sole_dirty_buffer(wa, active_tab.windows()):
        editor.show_message(E37_NO_WRITE_TEXT)
        return
    wa.close_tab()


@cmd('tabnext')
@cmd('tabn')
def tab_next(editor: Any) -> None:
    """
    Go to next tab.
    """
    editor.window_arrangement.go_to_next_tab()


@cmd('tabprevious')
@cmd('tabp')
def tab_previous(editor: Any) -> None:
    """
    Go to previous tab.
    """
    editor.window_arrangement.go_to_previous_tab()


@cmd('tabonly', accepts_force=True)
@cmd('tabo', accepts_force=True)
def tab_only(editor: Any, force: bool = False) -> None:
    """
    Close all tab pages except the current tab (`:tabo` / `:tabonly`), after
    the E37 dirty-buffer guard (docs/bugs/132.md).
    """
    wa = editor.window_arrangement
    active_tab = wa.active_tab
    if not force:
        other_windows = (
            win for tab in wa.tab_pages if tab is not active_tab for win in tab.windows()
        )
        if has_sole_dirty_buffer(wa, other_windows):
            editor.show_message(E37_NO_WRITE_TEXT)
            return
    wa.keep_only_current_tab()


@cmd('tabfirst')
@cmd('tabfir')
@cmd('tabrewind')
@cmd('tabr')
def tab_first(editor: Any) -> None:
    """
    Go to the first tab page (`:tabfir` / `:tabfirst`).
    """
    editor.window_arrangement.go_to_first_tab()


@cmd('tablast')
@cmd('tabl')
def tab_last(editor: Any) -> None:
    """
    Go to the last tab page (`:tabl` / `:tablast`).
    """
    editor.window_arrangement.go_to_last_tab()


@_cmd('tabmove')
@_cmd('tabm')
def tab_move(editor: Any, variables: Any) -> None:
    """
    Move the active tab page (`:tabm [N|+N|-N|$]` / `:tabmove`).
    """
    if variables.get('force'):
        editor.show_message('No ! allowed')
        return
    editor.window_arrangement.move_tab(variables.get('tab_arg'))


def tabs_lines(editor: Any) -> list[str]:
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
def show_tabs(editor: Any) -> None:
    """
    Display all tab pages and their windows/buffers in a read-only split
    inside vith (`:tabs`).
    """
    show_info_lines(
        editor,
        tabs_lines(editor),
        title='[Tabs]',
        empty_message='No open tabs',
    )
