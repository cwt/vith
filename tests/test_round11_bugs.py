"""Regression tests for the Round 11 correctness findings (docs/bugs/167-191)."""

from __future__ import annotations

import asyncio
from typing import Any

from vith.commands.handler import handle_command
from vith.render import render_document


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


def test_169_csv_cells_are_rendered_literally_not_as_rich_markup() -> None:
    """Cells such as '[/]' and '[bold]X' must not be parsed as Rich markup."""
    result = render_document('a,b\n[bold]X,[/]Y\n', filetype='csv')
    assert '[bold]X' in result.plain_text
    assert '[/]Y' in result.plain_text
    assert len(result.plain_text.split('\n')) == len(result.styled_lines)


def test_182_csv_bare_carriage_return_does_not_raise() -> None:
    """A bare CR in an unquoted field must not escape as csv.Error."""
    result = render_document('a,b\r1,2\n', filetype='csv')
    assert '1' in result.plain_text and '2' in result.plain_text
    assert len(result.plain_text.split('\n')) == len(result.styled_lines)


def test_170_join_with_non_numeric_argument_reports_error(editor: Any, tmp_path: Any) -> None:
    """`:j x` must show E488 instead of raising ValueError out of handle_command."""
    path = tmp_path / 'join.txt'
    path.write_text('one\ntwo\nthree\n', encoding='utf-8')
    editor.load_initial_files([str(path)])
    eb = editor.window_arrangement.active_editor_buffer
    original = eb.buffer.text

    for command in (':j x', ':join foo', ':1,2j abc', ':j "', ':j 1x', ':j 0'):
        handle_command(editor, command)
        assert eb.buffer.text == original
        assert editor.message.startswith('E488:')


def _open_render_controller(editor: Any, tmp_path: Any) -> tuple[Any, Any]:
    """Open a real live-render split and return (controller, preview buffer)."""
    path = tmp_path / 'doc.md'
    path.write_text('# Title\nhello\n', encoding='utf-8')
    editor.load_initial_files([str(path)])
    handle_command(editor, ':render-split-v')
    source = editor.window_arrangement.active_editor_buffer
    controller = source._live_render_controllers[0]
    return controller, controller.preview_eb


def test_171_generic_render_failure_shows_diagnostic(editor: Any, tmp_path: Any, monkeypatch: Any) -> None:
    """A permanent renderer exception must not be hidden behind a 'stale' badge."""
    import vith.render as render_module

    controller, preview = _open_render_controller(editor, tmp_path)
    controller._apply_result(
        1, render_module.RenderResult('good', [[('', 'good')]], 'markdown')
    )

    def boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError('kaboom')

    monkeypatch.setattr(render_module, 'render_document', boom)
    controller.render_sync()
    assert 'kaboom' in preview.buffer.text
    assert 'stale' not in (preview.scratch_title or '')


def test_172_dependency_error_clears_good_render_flag(editor: Any, tmp_path: Any) -> None:
    """A diagnostic shown after a good render must not later be labeled stale."""
    import vith.render as render_module

    controller, preview = _open_render_controller(editor, tmp_path)
    controller._apply_result(
        1, render_module.RenderResult('good', [[('', 'good')]], 'markdown')
    )
    controller._apply_error(2, "Optional package 'rich-rst' is required", incomplete=False)
    assert controller._has_good_render is False
    controller._apply_error(3, 'still broken', incomplete=True)
    assert 'stale' not in (preview.scratch_title or '')


def test_173_multi_line_error_message_stays_line_aligned(editor: Any, tmp_path: Any) -> None:
    """A multi-line diagnostic must produce one styled line per plain-text line."""
    controller, preview = _open_render_controller(editor, tmp_path)
    controller._apply_error(1, 'ERR line1\nERR line2\nERR line3', incomplete=False)
    assert len(preview.buffer.text.split('\n')) == len(preview._rendered_lines)
    assert controller._preview_line_starts == [0, 10, 20]


def test_174_set_fileformat_query(editor: Any, tmp_path: Any) -> None:
    """`:set ff?` and friends must report the buffer's fileformat."""
    path = tmp_path / 'ff.txt'
    path.write_text('one\n', encoding='utf-8')
    editor.load_initial_files([str(path)])
    for command in (':set ff?', ':set fileformat?', ':setlocal ff?', ':set ts? ff?'):
        editor.message = ''
        handle_command(editor, command)
        assert 'fileformat=unix' in (editor.message or ''), command


def test_186_setlocal_all_lists_options(editor: Any, tmp_path: Any) -> None:
    """`:setlocal all` is an inspection form, not a local assignment."""
    path = tmp_path / 'opts.txt'
    path.write_text('one\n', encoding='utf-8')
    editor.load_initial_files([str(path)])
    for command in (':set all', ':setlocal all', ':setl all'):
        editor.message = ''
        handle_command(editor, command)
        assert 'E518' not in (editor.message or ''), command


def test_175_bare_substitute_ignores_intervening_search(editor: Any, tmp_path: Any) -> None:
    """A bare `:s` repeats the last substitute pattern, not a later `/` search."""
    path = tmp_path / 'sub.txt'
    path.write_text('aaa bbb aaa\n', encoding='utf-8')
    editor.load_initial_files([str(path)])
    eb = editor.window_arrangement.active_editor_buffer

    handle_command(editor, ':s/a/X/')
    assert eb.buffer.text == 'Xaa bbb aaa'

    # Simulate a subsequent `/b` search, which must not hijack bare `:s`.
    editor.application.current_search_state.text = 'b'
    handle_command(editor, ':%s')
    # Repeats pattern 'a' with replacement 'X' on the current text; using the
    # intervening search pattern 'b' would have produced 'Xaa Xbb aaa'.
    assert eb.buffer.text == 'XXa bbb aaa'


def test_176_numeric_sort_keeps_ties_in_original_order(editor: Any, tmp_path: Any) -> None:
    """`:sort n` must be stable for equal keys, like Vim."""
    path = tmp_path / 'sort.txt'
    path.write_text('b2\na2\nc2\n', encoding='utf-8')
    editor.load_initial_files([str(path)])
    eb = editor.window_arrangement.active_editor_buffer

    handle_command(editor, ':sort n')
    assert eb.buffer.text.splitlines() == ['b2', 'a2', 'c2']

    eb.buffer.text = 'c\na\nb'
    handle_command(editor, ':sort n')
    assert eb.buffer.text.splitlines() == ['c', 'a', 'b']
