"""
Ex commands for real-time asynchronous document rendering splits
(`:render`, `:render-split-v`, `:render-split-h`).
"""

from __future__ import annotations

from typing import Any

from vith.render import close_live_render_for_buffer, open_or_update_live_render

from .registry import cmd


def _resolve_source_eb(editor: Any) -> Any | None:
    eb = editor.window_arrangement.active_editor_buffer
    if eb is None:
        return None
    owned = getattr(eb, '_owned_render_controller', None)
    if owned is not None and owned.source_eb is not None:
        return owned.source_eb
    return eb


@cmd('render-split-v', accepts_force=True)
def render_split_vertical(editor: Any, force: bool = False) -> None:
    """
    Open a live vertical preview split (editor left, rendered preview right)
    for the current buffer. With `!`, close the active live preview.
    """
    if force:
        source_eb = _resolve_source_eb(editor)
        if source_eb is not None:
            close_live_render_for_buffer(editor, source_eb)
        return
    open_or_update_live_render(editor, 'vertical')


@cmd('render-split-h', accepts_force=True)
def render_split_horizontal(editor: Any, force: bool = False) -> None:
    """
    Open a live horizontal preview split (editor top, rendered preview bottom)
    for the current buffer. With `!`, close the active live preview.
    """
    if force:
        source_eb = _resolve_source_eb(editor)
        if source_eb is not None:
            close_live_render_for_buffer(editor, source_eb)
        return
    open_or_update_live_render(editor, 'horizontal')


@cmd('render', accepts_force=True)
def render_command(editor: Any, force: bool = False) -> None:
    """
    Alias for `:render-split-v` or `:render-split-h` according to
    `editor.render_default_split` (`'vertical'` by default). With `!`, close
    the active live preview for the current buffer.
    """
    if force:
        source_eb = _resolve_source_eb(editor)
        if source_eb is not None:
            close_live_render_for_buffer(editor, source_eb)
        return
    orientation = getattr(editor, 'render_default_split', 'vertical')
    if orientation not in ('vertical', 'horizontal'):
        orientation = 'vertical'
    open_or_update_live_render(editor, orientation)
