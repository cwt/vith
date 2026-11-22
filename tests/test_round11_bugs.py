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
