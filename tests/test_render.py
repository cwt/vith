"""Tests for real-time async `:render`, `:render-split-v`, and `:render-split-h` splits."""

from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

import pytest

from vith.commands.handler import handle_command
from vith.lexer import DocumentLexer
from vith.rc_file import apply_toml_config
from vith.render import (
    ParseIncompleteError,
    RenderDependencyError,
    _rich_style_to_ptk,
    ansi_to_render_result,
    detect_render_format,
    render_document,
)
from vith.thai import is_thai_gate_latched, reset_thai_text_gate
from vith.window_arrangement import HSplit, VSplit


def test_rich_style_to_ptk_maps_attributes_and_colors() -> None:
    """Direct Rich Style -> prompt_toolkit style conversion maps modifiers, ANSI 16, and truecolor."""
    from rich.style import Style

    _rich_style_to_ptk.cache_clear()
    s1 = Style(bold=True, italic=True, underline=True, strike=True, dim=True, color='#8be9fd')
    ptk1 = _rich_style_to_ptk(s1)
    assert '#8be9fd' in ptk1
    for flag in ('bold', 'italic', 'underline', 'strike', 'dim'):
        assert flag in ptk1

    s2 = Style(color='cyan', bgcolor='red', reverse=True, blink=True, conceal=True)
    ptk2 = _rich_style_to_ptk(s2)
    assert 'ansicyan' in ptk2
    assert 'bg:ansired' in ptk2
    assert 'reverse' in ptk2 and 'blink' in ptk2 and 'hidden' in ptk2

    assert _rich_style_to_ptk(None) == ''
    assert _rich_style_to_ptk(Style()) == ''


def test_ansi_to_render_result_coalesces_adjacent_styles() -> None:
    """ANSI parser coalesces per-character prompt_toolkit ANSI tuples into multi-char spans."""
    raw = '\x1b[1;36mHello\x1b[0m plain\n\x1b[3;4mItalicUnder\x1b[0m\n'
    res = ansi_to_render_result(raw, format_name='ansi')
    assert res.format_name == 'ansi'
    assert res.plain_text == 'Hello plain\nItalicUnder'
    assert len(res.styled_lines) == 2
    # Coalesced: 'Hello' is a single span, not 5 separate single-character tuples.
    assert any('bold' in frag[0] and frag[1] == 'Hello' for frag in res.styled_lines[0])
    assert any(
        'italic' in frag[0] and 'underline' in frag[0] and frag[1] == 'ItalicUnder'
        for frag in res.styled_lines[1]
    )


def test_detect_render_format_precedence() -> None:
    """Format resolution prioritizes custom commands & explicit filetype, then extension, then content."""
    assert detect_render_format('README.md', None, 'hello') == 'markdown'
    assert detect_render_format('doc.rst', None, 'hello') == 'rst'
    assert detect_render_format('data.json', None, '{}') == 'json'
    assert detect_render_format('rows.csv', None, 'a,b\n1,2') == 'csv'
    assert detect_render_format('rows.tsv', None, 'a\tb\n1\t2') == 'tsv'
    assert detect_render_format('out.log', None, '\x1b[31mred\x1b[0m') == 'ansi'
    assert detect_render_format(None, 'rst', 'plain') == 'rst'
    assert detect_render_format(None, None, '# Title\n\nBody') == 'markdown'
    assert detect_render_format(None, None, '.. note:: hi\n') == 'rst'
    assert detect_render_format('page.html', None, '<p>hi</p>', {'html': 'cat'}) == 'html'
    assert detect_render_format('script.py', 'python', 'x = 1') == 'syntax'


def test_render_document_markdown_rst_json_csv_and_thai() -> None:
    """Built-in Rich / rich-rst backends render styled lines cleanly and preserve 1-to-1 line parity."""
    # 1. Markdown with headings, bold/italic, code block, GFM table, and Thai text (incl. SARA AM).
    md = (
        '# หัวข้อ\n\n'
        'ข้อความ **ตัวหนา** และ *ตัวเอียง* กับ `inline`\n\n'
        '| คำ | หมายเหตุ |\n'
        '| --- | --- |\n'
        '| ที่ดิน | คำอธิบาย |\n\n'
        '```python\n'
        'def answer():\n'
        '    return 42\n'
        '```\n'
    )
    md_res = render_document(md, location='notes.md', width=60)
    assert md_res.format_name == 'markdown'
    assert 'หัวข้อ' in md_res.plain_text
    assert 'ตัวหนา' in md_res.plain_text
    assert 'ที่ดิน' in md_res.plain_text
    assert 'def' in md_res.plain_text
    # Every plain line matches the concatenation of its styled fragments 1-to-1.
    plain_lines = md_res.plain_text.split('\n')
    assert len(plain_lines) == len(md_res.styled_lines)
    for plain, frags in zip(plain_lines, md_res.styled_lines):
        assert ''.join(frag[1] for frag in frags) == plain
    # Styled formatting (bold, italic, syntax colors) is actually present.
    all_styles = ' '.join(frag[0] for line in md_res.styled_lines for frag in line)
    assert 'bold' in all_styles
    assert 'italic' in all_styles
    assert '#' in all_styles  # truecolor syntax highlighting on fenced Python block

    # Incomplete mid-edit Markdown (unclosed ** and fence) still renders without raising.
    incomplete_md = '# WIP\n\nStill typing **bold without end\n\n```python\nx = 1'
    inc_res = render_document(incomplete_md, location='wip.md', width=50)
    assert 'WIP' in inc_res.plain_text and 'Still typing' in inc_res.plain_text

    # 2. reStructuredText via rich-rst (including incomplete directive).
    rst = 'Section\n=======\n\n.. note:: Hello **world**\n\n.. broken-dir:\n   text\n'
    rst_res = render_document(rst, location='guide.rst', width=60)
    assert rst_res.format_name == 'rst'
    assert 'Section' in rst_res.plain_text
    assert 'Hello' in rst_res.plain_text and 'world' in rst_res.plain_text
    assert any('bold' in frag[0] for line in rst_res.styled_lines for frag in line)

    # 3. JSON valid vs incomplete mid-edit.
    json_res = render_document('{"name": "vith", "ok": true}', location='cfg.json', width=50)
    assert json_res.format_name == 'json'
    assert '"name"' in json_res.plain_text and '"vith"' in json_res.plain_text
    with pytest.raises(ParseIncompleteError):
        render_document('{"name": "vith",', location='cfg.json', width=50)

    # 4. CSV / TSV box-drawn table.
    csv_res = render_document('Lang,Script\nThai,ไทย\nLao,ລາວ\n', location='langs.csv', width=50)
    assert csv_res.format_name == 'csv'
    assert (
        'Thai' in csv_res.plain_text and 'ไทย' in csv_res.plain_text and 'ລາວ' in csv_res.plain_text
    )

    # 5. External command override (`[render.commands]`).
    py_cmd = f'{sys.executable} -c "import sys; print(\'\\x1b[1;32m\' + sys.stdin.read().upper() + \'\\x1b[0m\')"'
    ext_res = render_document(
        'hello',
        location='page.html',
        width=40,
        custom_commands={'html': py_cmd},
    )
    assert ext_res.format_name == 'html'
    assert ext_res.plain_text == 'HELLO'
    assert any('bold' in frag[0] and frag[1] == 'HELLO' for frag in ext_res.styled_lines[0])


def test_missing_optional_dependency_degrades_gracefully(editor, monkeypatch) -> None:
    """When `rich` or `rich-rst` is missing, `:render` shows an install hint without crashing."""
    import vith.render as render_mod

    editor.load_initial_files([None])
    eb = editor.window_arrangement.active_editor_buffer
    eb.buffer.text = '# Hello'

    monkeypatch.setattr(render_mod, '_has_package', lambda _name: False)
    with pytest.raises(RenderDependencyError, match=r'vith\[render\]'):
        render_document('# Hello', location='a.md')

    handle_command(editor, ':render-split-v')
    assert editor.message is not None and 'vith[render]' in editor.message
    # No empty preview split was opened on missing dependency.
    assert editor.window_arrangement.active_tab.window_count() == 1

    # Missing rich-rst specifically when rich IS available:
    monkeypatch.setattr(render_mod, '_has_package', lambda name: name == 'rich')
    with pytest.raises(RenderDependencyError, match='rich-rst'):
        render_document('Title\n=====', location='doc.rst')


def test_render_split_commands_placement_focus_reorient_close_and_lexer(editor, tmp_path) -> None:
    """`:render-split-v`, `:render-split-h`, `:render`, `:render!` place splits cleanly and keep editor focus."""
    md_file = tmp_path / 'sample.md'
    md_file.write_text('# Heading\n\n**Bold** text and ที่ดิน\n', encoding='utf-8')
    reset_thai_text_gate()

    editor.load_initial_files([str(md_file)])
    source_win = editor.window_arrangement.active_tab.active_window
    source_eb = source_win.editor_buffer

    # `:render` defaults to vertical split (editor left, preview right, focus stays on editor).
    handle_command(editor, ':render')
    tab = editor.window_arrangement.active_tab
    assert tab.window_count() == 2
    assert isinstance(tab.root, VSplit)
    assert tab.root[0] is source_win
    preview_win = tab.root[1]
    assert tab.active_window is source_win  # Focus stays in editing pane!

    preview_eb = preview_win.editor_buffer
    assert preview_eb.is_scratch is True
    assert preview_eb.is_render_preview is True
    assert preview_eb.has_unsaved_changes is False
    assert 'Heading' in preview_eb.buffer.text
    assert 'Bold' in preview_eb.buffer.text
    assert is_thai_gate_latched()  # Rendered Thai content latches sticky gate.

    # DocumentLexer serves styled lines directly in O(1) and invalidates on version bump.
    lexer = DocumentLexer(preview_eb)
    h1 = lexer.invalidation_hash()
    get_line = lexer.lex_document(preview_eb.buffer.document)
    assert any(
        'bold' in st for i in range(len(preview_eb.buffer.document.lines)) for st, _ in get_line(i)
    )
    assert get_line(-1) == []
    assert get_line(9999) == []

    # Live edit on source buffer updates preview immediately (sync path when no loop running).
    source_eb.buffer.insert_text('\n## Subheading\n')
    assert 'Subheading' in preview_eb.buffer.text
    assert lexer.invalidation_hash() != h1
    assert preview_eb.has_unsaved_changes is False

    # Re-running `:render-split-h` re-orients instead of spawning a 3rd window!
    handle_command(editor, ':render-split-h')
    assert tab.window_count() == 2
    assert any(
        isinstance(node, HSplit)
        for node in (tab.root, *(tab.root if isinstance(tab.root, list) else ()))
    )
    assert tab.active_window is source_win

    # Running `:render!` closes the preview split and detaches controller.
    handle_command(editor, ':render!')
    assert tab.window_count() == 1
    assert tab.active_window is source_win
    assert not source_eb._live_render_controllers
    assert not editor._live_render_controllers

    # Invoking `:render-split-v` while focused in the preview resolves to source,
    # and `:render-split-v!` / `:render-split-h!` close cleanly.
    handle_command(editor, ':render-split-v')
    tab.active_window = tab.root[1]  # focus preview pane
    handle_command(editor, ':render-split-h')  # re-orients preview for its source buffer
    assert tab.window_count() == 2
    handle_command(editor, ':render-split-h!')
    assert tab.window_count() == 1
    handle_command(editor, ':render-split-v')
    handle_command(editor, ':render-split-v!')
    assert tab.window_count() == 1


def test_live_render_async_debounce_coalescing_stale_fallback_and_resize(editor, tmp_path) -> None:
    """Async live rendering debounces rapid keystrokes, keeps last-good JSON on error, and reacts to resize."""
    json_file = tmp_path / 'data.json'
    json_file.write_text('{"status": "ok", "count": 1}', encoding='utf-8')
    editor.load_initial_files([str(json_file)])
    editor.render_debounce_ms = 30
    source_eb = editor.window_arrangement.active_editor_buffer

    async def scenario() -> None:
        handle_command(editor, ':render-split-v')
        assert len(source_eb._live_render_controllers) == 1
        ctrl = source_eb._live_render_controllers[0]
        preview_eb = ctrl.preview_eb
        assert preview_eb is not None

        # Wait for initial async render to finish.
        for _ in range(40):
            if '"status"' in preview_eb.buffer.text:
                break
            await asyncio.sleep(0.01)
        assert '"status"' in preview_eb.buffer.text
        good_text = preview_eb.buffer.text

        # Rapidly type invalid mid-edit JSON: preview MUST keep last valid render + mark stale!
        source_eb.buffer.text = '{"status": "ok", "count": '
        await asyncio.sleep(0.12)
        assert preview_eb.buffer.text == good_text
        assert preview_eb.scratch_title is not None and 'stale' in preview_eb.scratch_title

        # Complete valid JSON across multiple rapid edits (coalesced by debounce):
        source_eb.buffer.text = '{"status": "updated", "count": 2}'
        source_eb.buffer.text = '{"status": "final", "count": 3}'
        for _ in range(50):
            if '"final"' in preview_eb.buffer.text:
                break
            await asyncio.sleep(0.01)
        assert '"final"' in preview_eb.buffer.text
        assert preview_eb.scratch_title is not None and 'stale' not in preview_eb.scratch_title

        # Width change via `check_width()` triggers re-render at the new width.
        preview_win = ctrl.preview_window
        assert preview_win is not None and preview_win.pt_window is not None
        preview_win.pt_window.render_info = SimpleNamespace(window_width=32, window_height=20)
        ctrl.check_width()
        assert ctrl._last_width == 32
        await asyncio.sleep(0.10)

        # Closing preview window (`:only`) cleans up controller & timers automatically.
        handle_command(editor, ':only')
        assert ctrl.is_closed is True
        assert not source_eb._live_render_controllers
        assert not editor._live_render_controllers

    asyncio.run(scenario())


def test_toml_render_config_and_validation(editor) -> None:
    """`[render]` and `[render.commands]` TOML options apply cleanly and reject invalid values safely."""
    errors: list[str] = []
    ok = apply_toml_config(
        editor,
        {
            'render': {
                'default_split': 'horizontal',
                'debounce_ms': 200,
                'sync_scroll': False,
                'commands': {'HTML': 'w3m -dump -T text/html'},
            }
        },
        error_handler=errors.append,
    )
    assert ok is True
    assert not errors
    assert editor.render_default_split == 'horizontal'
    assert editor.render_debounce_ms == 200
    assert editor.render_sync_scroll is False
    assert editor.render_commands == {'html': 'w3m -dump -T text/html'}

    # Invalid values report errors without raising or clobbering prior valid settings.
    bad_errors: list[str] = []
    ok2 = apply_toml_config(
        editor,
        {
            'render': {
                'default_split': 'diagonal',
                'debounce_ms': -5,
                'sync_scroll': 'yes',
                'commands': {'md': ''},
            }
        },
        error_handler=bad_errors.append,
    )
    assert ok2 is False
    assert len(bad_errors) == 4
    assert editor.render_default_split == 'horizontal'
    assert editor.render_debounce_ms == 200
    assert editor.render_sync_scroll is False


def test_set_filetype_refreshes_active_render_preview(editor) -> None:
    """Changing `:set ft=...` on an untitled buffer refreshes its live preview immediately."""
    editor.load_initial_files([None])
    eb = editor.window_arrangement.active_editor_buffer
    eb.buffer.text = '{"hello": 123}'

    handle_command(editor, ':render-split-v')
    ctrl = eb._live_render_controllers[0]
    preview_eb = ctrl.preview_eb
    assert preview_eb is not None
    assert ctrl._last_format == 'json'

    handle_command(editor, ':set ft=markdown')
    assert ctrl._last_format == 'markdown'
    handle_command(editor, ':render!')


def test_render_position_sync_anchors_headings_and_follows_cursor_and_scroll(
    editor, tmp_path
) -> None:
    """Preview cursor & viewport scroll track source cursor moves via heading anchors and pause when preview is focused."""
    from vith.render import map_source_row_to_preview

    # Build a multi-section Markdown doc with an expanded table in section 1 so
    # rendered line ratios diverge from naive proportional ratios.
    sections = [
        '# First Heading',
        '',
        '| Col A | Col B |',
        '| --- | --- |',
        *[f'| row {i} | value {i} |' for i in range(12)],
        '',
        '## Second Heading',
        '',
        *[f'Body paragraph line {i} with extra text.' for i in range(10)],
        '',
        '## Third Heading',
        '',
        'Final section content.',
    ]
    md_text = '\n'.join(sections)
    md_file = tmp_path / 'long.md'
    md_file.write_text(md_text, encoding='utf-8')

    editor.load_initial_files([str(md_file)])
    source_win = editor.window_arrangement.active_tab.active_window
    source_eb = source_win.editor_buffer

    handle_command(editor, ':render-split-v')
    ctrl = source_eb._live_render_controllers[0]
    preview_win = ctrl.preview_window
    preview_eb = ctrl.preview_eb
    assert preview_win is not None and preview_eb is not None
    assert preview_win.pt_window is not None and source_win.pt_window is not None

    # Anchors captured all three headings monotonically.
    second_src_row = sections.index('## Second Heading')
    third_src_row = sections.index('## Third Heading')
    anchor_src_rows = [s for s, _r in ctrl._anchors]
    assert second_src_row in anchor_src_rows
    assert third_src_row in anchor_src_rows

    # Simulate a 15-row viewport height on the preview window.
    preview_win.pt_window.render_info = SimpleNamespace(window_width=60, window_height=15)

    # Moving cursor onto `## Second Heading` in the editor moves preview cursor
    # directly to the rendered `Second Heading` row and aligns vertical_scroll!
    source_win.pt_window.vertical_scroll = second_src_row - 3  # cursor 3 lines from top
    source_eb.buffer.cursor_position = source_eb.buffer.document.translate_row_col_to_index(
        second_src_row, 0
    )
    preview_row = preview_eb.buffer.document.cursor_position_row
    assert 'Second Heading' in preview_eb.buffer.document.lines[preview_row]
    expected_scroll = max(
        0,
        min(
            max(0, len(preview_eb.buffer.document.lines) - 15),
            preview_row - 3,
        ),
    )
    assert preview_win.pt_window.vertical_scroll == expected_scroll

    # Moving cursor onto `## Third Heading` jumps preview cursor directly to `Third Heading`.
    source_win.pt_window.vertical_scroll = third_src_row - 2
    source_eb.buffer.cursor_position = source_eb.buffer.document.translate_row_col_to_index(
        third_src_row, 0
    )
    preview_row_3 = preview_eb.buffer.document.cursor_position_row
    assert 'Third Heading' in preview_eb.buffer.document.lines[preview_row_3]

    # Scrolling the source viewport (`zt`/`zz` style) without moving cursor syncs via `on_after_render`.
    source_win.pt_window.vertical_scroll = third_src_row  # cursor now at top line (screen_y = 0)
    ctrl.on_after_render()
    max_scroll = max(0, len(preview_eb.buffer.document.lines) - 15)
    assert preview_win.pt_window.vertical_scroll == min(max_scroll, preview_row_3)

    # Focusing the preview pane suspends auto-sync so manual scrolling isn't fought.
    tab = editor.window_arrangement.active_tab
    tab.active_window = preview_win
    preview_win.pt_window.vertical_scroll = 0
    preview_eb.buffer.cursor_position = 0
    source_eb.buffer.cursor_position = 0
    assert preview_win.pt_window.vertical_scroll == 0

    # `map_source_row_to_preview` interpolates monotonically between anchors and handles boundaries.
    assert map_source_row_to_preview(0, 100, 200, ((0, 0), (50, 120), (99, 199))) == 0
    assert map_source_row_to_preview(50, 100, 200, ((0, 0), (50, 120), (99, 199))) == 120
    assert map_source_row_to_preview(25, 100, 200, ((0, 0), (50, 120), (99, 199))) == 60

    # Closing detaches the `on_cursor_position_changed` callback cleanly.
    tab.active_window = source_win
    handle_command(editor, ':render!')
    assert (
        ctrl._on_source_cursor_changed not in source_eb.buffer.on_cursor_position_changed._handlers
    )


def test_render_live_typing_preserves_lazy_document_cache_and_row_accuracy(
    editor, tmp_path
) -> None:
    """Live typing with :render active must not materialize Document._cache.line_indexes or drift across undo/redo."""
    from prompt_toolkit.document import Document

    from vith.render import open_or_update_live_render

    md_file = tmp_path / 'typing.md'
    initial = '# Title\n\n' + '\n'.join(f'Line {i} content' for i in range(400)) + '\n'
    md_file.write_text(initial, encoding='utf-8')
    editor.load_initial_files([str(md_file)])

    async def scenario() -> None:
        source_eb = editor.window_arrangement.active_editor_buffer
        assert source_eb is not None
        ctrl = open_or_update_live_render(editor, orientation='vertical')
        assert ctrl is not None
        for _ in range(100):
            if ctrl._has_good_render:
                break
            await asyncio.sleep(0.01)
        assert ctrl._has_good_render

        # Start with a fresh lazy Document mid-file and type characters on one line.
        source_eb.buffer.set_document(Document(initial, cursor_position=len(initial) // 2))
        ctrl._cached_src_row_info = None
        ctrl._cached_src_line_count = None

        for _ in range(25):
            source_eb.buffer.insert_text('x')

        assert source_eb.buffer.document._cache.lines is None
        assert source_eb.buffer.document._cache.line_indexes is None

        # Verify row & line_count stay exact across newlines and undo branching.
        expected_row = source_eb.buffer.text[: source_eb.buffer.cursor_position].count('\n')
        expected_lines = source_eb.buffer.text.count('\n') + 1
        assert ctrl._source_row_and_line_count(source_eb) == (expected_row, expected_lines)

        source_eb.buffer.save_to_undo_stack()
        source_eb.buffer.insert_text('\n\n')
        assert ctrl._source_row_and_line_count(source_eb) == (expected_row + 2, expected_lines + 2)

        source_eb.buffer.undo()
        assert ctrl._source_row_and_line_count(source_eb) == (expected_row, expected_lines)

        source_eb.buffer.insert_text('\n\n\n')
        assert ctrl._source_row_and_line_count(source_eb) == (expected_row + 3, expected_lines + 3)

        handle_command(editor, ':render!')

    asyncio.run(scenario())
