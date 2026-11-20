"""
Read-only information and inspection Ex commands (`:comp`, `:ver`, `:mes`,
`:reg`, `:marks`, `:help`).
"""

from __future__ import annotations

from typing import Any

from prompt_toolkit.selection import SelectionType

from .common import show_info_lines
from .registry import cmd


@cmd('compression')
@cmd('comp')
@cmd('coldtext')
def cold_text_compression(editor: Any) -> None:
    """
    Show compressed undo/redo history size and ratio in a new window
    (like `:help`, close with `:q`).
    """
    editor.show_compression()


def version_lines(editor: Any) -> list[str]:
    """
    Build the `:version` pager lines: vith, Python, prompt_toolkit, Thai word
    segmentation backend, and cold-text compression configuration.
    """
    import platform
    import sys

    import prompt_toolkit

    from vith import __version__ as vith_version
    from vith.cold_text import make_cold_text_compressor
    from vith.thai.words import _get_libthai

    thai_backend = 'libthai' if _get_libthai() is not None else 'orthographic fallback (TCC-lite)'

    eb = editor.window_arrangement.active_editor_buffer
    if eb is not None and hasattr(eb.buffer, 'cold_compression_name'):
        comp_name = eb.buffer.cold_compression_name
        comp_level = eb.buffer.cold_compression_level
    else:
        _, comp_name, comp_level = make_cold_text_compressor(
            editor.cold_text_compression,
            editor.cold_text_compression_level,
        )
    comp_text = comp_name if comp_level <= 0 else f'{comp_name} (level {comp_level})'

    return [
        f'vith {vith_version}',
        f'Python {platform.python_version()} ({platform.python_implementation()}) on {sys.platform}',
        f'prompt_toolkit {prompt_toolkit.__version__}',
        f'Thai word segmentation: {thai_backend}',
        f'Cold-text compression: {comp_text}',
    ]


@cmd('ver')
@cmd('version')
def show_version(editor: Any) -> None:
    """
    Display vith and runtime environment version info in a split inside vith
    (`:ver` / `:version`).
    """
    editor.show_info('\n'.join(version_lines(editor)), title='[Version]')


def messages_lines(editor: Any) -> list[str]:
    """
    Return the recorded editor message history for `:messages`.
    """
    return list(getattr(editor, '_messages', ()))


@cmd('mes')
@cmd('messages')
def show_messages(editor: Any) -> None:
    """
    Display recorded editor messages in a split inside vith (`:mes` / `:messages`).
    """
    show_info_lines(
        editor,
        messages_lines(editor),
        title='[Messages]',
        empty_message='No messages',
    )


def _format_register_type(selection_type: Any) -> str:
    if selection_type == SelectionType.LINES:
        return 'l'
    if selection_type == SelectionType.BLOCK:
        return 'b'
    return 'c'


def _format_register_preview(text: str, max_len: int = 200) -> str:
    # Truncate before escaping so a multi-megabyte yank is not copied three
    # times just to display 200 characters (docs/bugs/149.md).
    if len(text) > max_len:
        text = text[: max_len - 3] + '...'
    return text.replace('\r', '^M').replace('\n', '^J').replace('\t', '^I')


def _register_sort_key(name: str) -> tuple[int, str]:
    if name.isdigit():
        return (0, name)
    if 'a' <= name <= 'z':
        return (1, name)
    return (2, name)


def registers_lines(editor: Any) -> list[str]:
    """
    Build Vim-style `:registers` lines from the unnamed clipboard (`""`) and
    non-empty named registers on `editor.application.vi_state.named_registers`.
    """
    rows: list[str] = []
    app = getattr(editor, 'application', None)
    if app is None:
        return []

    clipboard = getattr(app, 'clipboard', None)
    if clipboard is not None:
        unnamed = clipboard.get_data()
        if unnamed is not None and unnamed.text:
            rtype = _format_register_type(getattr(unnamed, 'type', SelectionType.CHARACTERS))
            rows.append(f'  {rtype}  ""   {_format_register_preview(unnamed.text)}')

    vi_state = getattr(app, 'vi_state', None)
    named_registers = getattr(vi_state, 'named_registers', None) or {}
    for name in sorted(named_registers.keys(), key=_register_sort_key):
        data = named_registers[name]
        if data is not None and data.text:
            rtype = _format_register_type(getattr(data, 'type', SelectionType.CHARACTERS))
            rows.append(f'  {rtype}  "{name}   {_format_register_preview(data.text)}')

    # Registers written by `:d {x}` / `:y {x}` (docs/bugs/147.md).
    for name in sorted(getattr(editor, 'registers', {}), key=_register_sort_key):
        text = editor.registers[name]
        if text:
            rows.append(
                f'  {_format_register_type(SelectionType.LINES)}  "{name}   {_format_register_preview(text)}'
            )

    if not rows:
        return []
    return ['Type Name Content', *rows]


@cmd('registers')
@cmd('reg')
def show_registers(editor: Any) -> None:
    """
    Display non-empty registers in a read-only split inside vith
    (`:reg` / `:registers`).
    """
    show_info_lines(
        editor,
        registers_lines(editor),
        title='[Registers]',
        empty_message='No registers',
    )


def marks_lines(editor: Any) -> list[str]:
    """
    Build Vim-style `:marks` lines from buffer-local marks and `'<` / `'>`
    visual line marks on the active buffer.
    """
    eb = editor.window_arrangement.active_editor_buffer
    if eb is None:
        return []

    doc = eb.buffer.document
    doc_lines = doc.lines
    line_count = len(doc_lines)
    collected: list[tuple[str, int, int]] = []

    raw_marks: dict[str, Any] = {}
    buf_marks = getattr(eb.buffer, 'marks', None)
    if isinstance(buf_marks, dict):
        raw_marks.update(buf_marks)
    eb_marks = getattr(eb, 'marks', None)
    if isinstance(eb_marks, dict):
        raw_marks.update(eb_marks)

    for name in sorted(raw_marks.keys()):
        pos = raw_marks[name]
        if isinstance(pos, tuple) and len(pos) == 2:
            row, col = int(pos[0]), int(pos[1])
        elif isinstance(pos, int):
            clamped = max(0, min(pos, len(doc.text)))
            row, col = doc.translate_index_to_position(clamped)
        else:
            continue
        collected.append((str(name), row, col))

    visual_marks = getattr(eb, 'visual_marks', None)
    if visual_marks is not None:
        start_row, end_row = visual_marks
        collected.append(('<', int(start_row), 0))
        collected.append(('>', int(end_row), 0))

    if not collected:
        return []

    rows = ['mark line  col file/text']
    for name, row, col in collected:
        line_text = doc_lines[row] if 0 <= row < line_count else ''
        rows.append(f' {name:<3} {row + 1:>4} {col:>4} {line_text}')
    return rows


@cmd('marks')
def show_marks(editor: Any) -> None:
    """
    Display marks recorded on the active buffer (including `'<` / `'>`) in a
    read-only split inside vith (`:marks`).
    """
    show_info_lines(
        editor,
        marks_lines(editor),
        title='[Marks]',
        empty_message='No marks',
    )


@cmd('h')
@cmd('help')
def help(editor: Any) -> None:
    """
    Show help.
    """
    editor.show_help()
