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


def test_171_generic_render_failure_shows_diagnostic(
    editor: Any, tmp_path: Any, monkeypatch: Any
) -> None:
    """A permanent renderer exception must not be hidden behind a 'stale' badge."""
    import vith.render as render_module

    controller, preview = _open_render_controller(editor, tmp_path)
    controller._apply_result(1, render_module.RenderResult('good', [[('', 'good')]], 'markdown'))

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
    controller._apply_result(1, render_module.RenderResult('good', [[('', 'good')]], 'markdown'))
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


def test_177_reload_clears_stale_undo_history(editor: Any, tmp_path: Any) -> None:
    """`:e` reload must not leave snapshots that restore pre-reload content."""
    from vith.editor_buffer import EditorBuffer

    path = tmp_path / 'undo.txt'
    path.write_text('old text\n', encoding='utf-8')
    eb = EditorBuffer(editor, str(path))
    eb.buffer.text = 'edited text\n'
    eb.buffer.save_to_undo_stack()

    path.write_text('disk text\n', encoding='utf-8')
    eb.reload()
    assert eb.buffer.text == 'disk text'
    assert not eb.buffer._undo_stack
    eb.buffer.undo()
    assert eb.buffer.text == 'disk text'


def test_189_reload_untitled_buffer_is_guarded(editor: Any) -> None:
    """Calling reload() on a [New file] buffer must not raise TypeError."""
    from vith.editor_buffer import EditorBuffer

    eb = EditorBuffer(editor)
    eb.reload()
    assert 'E32' in (editor.message or '')


def test_178_malformed_compression_config_does_not_crash_startup(editor: Any) -> None:
    """A non-string cold_text_compression value must be reported, not applied."""
    from vith.rc_file import apply_toml_config

    assert apply_toml_config(editor, {'editor': {'cold_text_compression': 5}}) is False
    assert editor.cold_text_compression != 5
    editor.load_initial_files([None])  # Would raise AttributeError before the fix.


def test_188_zstd_levels_above_9_are_accepted(editor: Any) -> None:
    """Config validation must allow the zstd compressor's 1..22 level range."""
    from vith.rc_file import apply_toml_config

    assert apply_toml_config(
        editor,
        {'editor': {'cold_text_compression': 'zstd', 'cold_text_compression_level': 12}},
    )
    assert editor.cold_text_compression_level == 12
    editor.message = ''
    assert apply_toml_config(editor, {'editor': {'cold_text_compression_level': 23}}) is False


def test_179_config_fileformat_applies_to_new_buffers(editor: Any) -> None:
    """`[editor] fileformat` must be inherited by newly created buffers."""
    from vith.rc_file import apply_toml_config

    assert apply_toml_config(editor, {'editor': {'fileformat': 'dos'}})
    editor.load_initial_files([None])
    eb = editor.window_arrangement.active_editor_buffer
    assert eb.fileformat == 'dos'
    assert eb.line_ending() == '\r\n'


def test_180_create_tab_on_empty_arrangement_makes_one_tab(editor: Any) -> None:
    """create_tab() on a fresh editor must not create two tabs for one buffer."""
    wa = editor.window_arrangement
    assert not wa.tab_pages
    wa.create_tab('somefile.py')
    assert len(wa.tab_pages) == 1
    assert wa.active_tab_index == 0
    assert len(wa.editor_buffers) == 1

    # Normal path: an existing arrangement still inserts a new tab.
    wa.create_tab('other.py')
    assert len(wa.tab_pages) == 2
    assert wa.active_tab_index == 1


def test_181_visual_block_highlight_matches_yank_on_short_row(editor: Any) -> None:
    """A block past a short tabbed row's EOL must neither highlight nor yank it."""
    from prompt_toolkit.application.current import set_app
    from prompt_toolkit.document import Document
    from prompt_toolkit.selection import SelectionState, SelectionType

    lines = ['\tABCDEFGHIJ', 'abcdef', '\tABCDEFGHIJ']
    text = '\n'.join(lines)
    anchor = 5  # row 0, codepoint 5 -> visual column 12
    cursor = text.index('\n', text.index('\n') + 1) + 1 + 10  # row 2, col 10 -> visual 17
    doc = Document(
        text, cursor_position=cursor, selection=SelectionState(anchor, SelectionType.BLOCK)
    )

    with set_app(editor.application):
        # Highlight must agree with yank/delete: the short middle row is past
        # the block's visual columns and is selected by neither API.
        assert doc.selection_range_at_line(1) is None
        highlighted_rows = [
            row for row in (0, 1, 2) if doc.selection_range_at_line(row) is not None
        ]
        ranges = list(doc.selection_ranges())
        assert len(ranges) == 2
        assert highlighted_rows == [0, 2]
        for start, end in ranges:
            assert 0 <= start < end <= len(text)


def test_183_immediate_refresh_preserved_while_render_in_flight(
    editor: Any, tmp_path: Any, monkeypatch: Any
) -> None:
    """An immediate request made during a render must stay immediate after coalescing."""
    import threading

    import vith.render as render_module

    controller, _ = _open_render_controller(editor, tmp_path)
    editor.render_debounce_ms = 400
    gate = threading.Event()
    calls: list[int] = []

    def slow_render(*args: Any, **kwargs: Any) -> Any:
        calls.append(1)
        if len(calls) == 1:
            gate.wait(5)
        return render_module.RenderResult('ok', [[('', 'ok')]], 'markdown')

    monkeypatch.setattr(render_module, 'render_document', slow_render)

    async def scenario() -> None:
        loop = asyncio.get_running_loop()
        original_call_later = loop.call_later
        delays: list[float] = []

        def recording_call_later(delay: float, callback: Any, *args: Any) -> Any:
            if getattr(callback, '__self__', None) is controller:
                delays.append(delay)
            return original_call_later(delay, callback, *args)

        monkeypatch.setattr(loop, 'call_later', recording_call_later)
        controller.schedule(immediate=True)
        await asyncio.sleep(0.05)
        assert controller._in_flight
        controller.schedule(immediate=True)
        assert controller._pending_immediate is True
        gate.set()
        for _ in range(150):
            if len(calls) >= 2 and not controller._in_flight:
                break
            await asyncio.sleep(0.01)

        assert len(calls) >= 2
        assert delays[0] == 0.0
        assert delays[-1] == 0.0  # coalesced follow-up must not be debounced
        assert controller._pending_immediate is False

    try:
        asyncio.run(scenario())
    finally:
        gate.set()


def test_184_custom_command_alias_is_resolved_consistently() -> None:
    """A command configured as 'md' must win for ft=markdown and vice versa."""
    from vith.render import detect_render_format

    commands = {'md': 'glow'}
    assert detect_render_format(None, 'markdown', 'hi', commands) == 'md'
    assert detect_render_format('note.md', None, 'hi', commands) == 'md'
    assert detect_render_format(None, 'md', 'hi', {'markdown': 'glow'}) == 'markdown'


def test_187_named_yank_and_delete_also_update_unnamed_register(editor: Any, tmp_path: Any) -> None:
    """`:y a` / `:d b` must also make the text available to a plain paste."""
    path = tmp_path / 'reg.txt'
    path.write_text('one\ntwo\nthree\n', encoding='utf-8')
    editor.load_initial_files([str(path)])

    handle_command(editor, ':y a')
    assert editor.registers['a'] == 'one'
    unnamed = editor.application.clipboard.get_data()
    assert unnamed is not None and unnamed.text == 'one'

    handle_command(editor, ':d b')
    assert editor.registers['b'] == 'one'
    unnamed = editor.application.clipboard.get_data()
    assert unnamed is not None and unnamed.text == 'one'


def test_190_reporter_recovers_after_worker_exception(
    editor: Any, tmp_path: Any, monkeypatch: Any
) -> None:
    """A raising reporter must not leave diagnostics permanently disabled."""
    import vith.editor_buffer as eb_module

    calls: list[int] = []

    def flaky_report(*args: Any) -> list[Any]:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError('reporter boom')
        return []

    monkeypatch.setattr(eb_module, 'report', flaky_report)
    path = tmp_path / 'module.py'
    path.write_text('x = 1\n', encoding='utf-8')
    editor.load_initial_files([str(path)])
    eb = editor.window_arrangement.active_editor_buffer

    async def scenario() -> None:
        eb.run_reporter()
        await asyncio.sleep(0.05)
        assert eb._reporter_is_running is False
        eb.run_reporter()
        await asyncio.sleep(0.05)

    asyncio.run(scenario())
    assert len(calls) == 2


def test_191_unassigned_u0ec7_is_not_a_combining_mark() -> None:
    """U+0EC7 is Cn/width-1 and must not be treated as a zero-width mark."""
    import unicodedata

    from prompt_toolkit.utils import get_cwidth

    from vith.thai import is_combining_char, is_thai_combining

    char = chr(0x0EC7)
    assert unicodedata.category(char) == 'Cn'
    assert get_cwidth(char) == 1
    assert is_thai_combining(0x0EC7) is False
    assert is_combining_char(char) is False


def test_198_csv_row_cap_keeps_exactly_max_rows() -> None:
    from rich.table import Table

    from vith.render import _CSV_MAX_ROWS, _render_csv_table

    big = '\n'.join(f'a{i},b{i}' for i in range(_CSV_MAX_ROWS + 10)) + '\n'
    table = _render_csv_table(big, ',')
    # Table rows exclude header: kept data rows == MAX - 1 (header) + truncation note.
    from rich.console import Group

    assert isinstance(table, Group), 'over-cap table must carry truncation note'
    rendered_table = table.renderables[0]
    assert isinstance(rendered_table, Table)
    assert rendered_table.row_count == _CSV_MAX_ROWS - 1


def test_199_csv_landmark_respects_quoting() -> None:
    from vith.render import _extract_source_landmarks

    src = '"a,b",c\nplain,d\n'
    marks = _extract_source_landmarks(src, 'csv')
    assert marks, 'expected landmarks'
    assert marks[0][1] == 'a,b', marks[0]
