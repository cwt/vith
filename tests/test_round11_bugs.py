"""Regression tests for the Round 11 correctness findings (docs/bugs/167-191)."""

from __future__ import annotations

import asyncio
from typing import Any

from vith.commands.handler import handle_command


def test_167_bare_shell_command_is_scheduled_and_executed(editor: Any, tmp_path: Any) -> None:
    """A bare `:!cmd` must schedule prompt_toolkit's async runner, not drop it."""
    marker = tmp_path / 'shell-ran'

    async def fake_run_system_command(command: str, **kwargs: Any) -> None:
        marker.write_text(command, encoding='utf-8')

    editor.application.run_system_command = fake_run_system_command

    async def scenario() -> None:
        handle_command(editor, ':!echo hi')
        # Let the background task start and finish.
        for _ in range(5):
            if marker.exists():
                break
            await asyncio.sleep(0)

    asyncio.run(scenario())
    assert marker.read_text(encoding='utf-8') == 'echo hi'


def test_168_star_and_hash_accept_real_keypress_arguments(editor: Any) -> None:
    """`*` / `#` must normalize KeyPressEvent.arg (None or str), not crash."""
    from unittest.mock import MagicMock

    from vith.key_bindings import create_key_bindings

    editor.load_initial_files([None])
    editor.sync_with_prompt_toolkit()
    eb = editor.window_arrangement.active_editor_buffer
    eb.buffer.text = 'foo bar foo'
    kb = create_key_bindings(editor)
    star = next(b for b in kb.bindings if b.keys == ('*',)).handler
    hash_ = next(b for b in kb.bindings if b.keys == ('#',)).handler

    for arg in (None, '2'):
        event = MagicMock(app=editor.application, current_buffer=eb.buffer, arg=arg)
        eb.buffer.cursor_position = 0
        star(event)
        assert editor.application.current_search_state.text == 'foo'
        eb.buffer.cursor_position = 10
        hash_(event)
        assert editor.application.current_search_state.text == 'foo'
