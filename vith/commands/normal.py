"""
`:normal` / `:norm` Ex command and Normal-mode key replay helper reused by
`:global` / `:vglobal`.
"""

from __future__ import annotations

from typing import Any

from vith.thai import note_document_text

from .common import resolve_command_range
from .ranges import MODIFIABLE_OFF_TEXT, is_buffer_read_only
from .registry import _cmd


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


def _reset_normal_state(vi_state: Any, buff: Any, navigation_mode: Any) -> None:
    """Return Vi state and buffer selection to a clean Normal-mode baseline."""
    vi_state.input_mode = navigation_mode
    vi_state.operator_func = None
    vi_state.operator_arg = None
    buff.exit_selection()


def execute_normal_on_rows(
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

    if is_buffer_read_only(eb.buffer):
        editor.show_message(MODIFIABLE_OFF_TEXT)
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
                if target_row < 0 or not doc.lines:
                    continue
                if target_row >= len(doc.lines):
                    if track_line_delta:
                        continue
                    # Vim addresses the range by current line number and
                    # clamps the final address to the last line (docs/bugs/
                    # 143.md): a line-deleting `:norm` still processes it.
                    target_row = len(doc.lines) - 1
                before_line_count = len(doc.lines)

                _reset_normal_state(vi_state, buff, InputMode.NAVIGATION)
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
                _reset_normal_state(vi_state, buff, InputMode.NAVIGATION)
                kp.reset()

                if track_line_delta:
                    line_offset += len(buff.document.lines) - before_line_count
    finally:
        if hasattr(buff, '_suppress_undo_saves'):
            buff._suppress_undo_saves = max(0, buff._suppress_undo_saves - 1)
        editor._normal_command_depth = max(0, getattr(editor, '_normal_command_depth', 1) - 1)
        _reset_normal_state(vi_state, buff, InputMode.NAVIGATION)
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

    bounds = resolve_command_range(editor, eb, variables.get('cmd_range'), default_range='.')
    if bounds is None:
        return

    start_row, end_row = bounds
    execute_normal_on_rows(
        editor,
        eb,
        list(range(start_row, end_row + 1)),
        norm_keys,
        ignore_user_mappings=bool(variables.get('force')),
    )
