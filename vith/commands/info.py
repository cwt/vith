"""
Read-only information and inspection Ex commands (`:comp`, `:ver`, `:mes`,
`:reg`, `:marks`, `:help`).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from typing import Any

from prompt_toolkit.selection import SelectionType

from .common import show_info_lines
from .registry import cmd, location_cmd


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


# ---------------------------------------------------------------------------
# `:render` -- read-only rendered preview of markup files (.html/.md/.rst/...)
# ---------------------------------------------------------------------------


def _detect_render_format(location: Any, filetype: str | None, text: str) -> str:
    """Pick a renderer from the location extension, filetype, or content."""
    if location:
        ext = os.path.splitext(str(location))[1].lower()
        by_ext = {
            '.html': 'html',
            '.htm': 'html',
            '.xhtml': 'html',
            '.md': 'markdown',
            '.markdown': 'markdown',
            '.mdown': 'markdown',
            '.rst': 'rst',
            '.rest': 'rst',
        }
        if ext in by_ext:
            return by_ext[ext]

    ft = (filetype or '').lower()
    if ft in ('html', 'xml', 'xhtml'):
        return 'html'
    if ft in ('markdown', 'md'):
        return 'markdown'
    if ft in ('rst', 'restructuredtext'):
        return 'rst'

    sample = (text or '').lstrip()[:256].lower()
    if sample.startswith(('<!doctype html', '<html', '<head', '<body')):
        return 'html'
    if sample.startswith('.. ') or sample.startswith('====') or sample.startswith('----'):
        return 'rst'
    # Markdown heuristics: ATX heading or fenced code block near the top.
    if re.search(r'^#{1,6}\s+\S', sample, re.MULTILINE):
        return 'markdown'
    if sample.startswith('```') or sample.startswith('~~~'):
        return 'markdown'
    return 'text'


_MD_INLINE_SUBS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Images first.
    (re.compile(r'!\[([^\]]*)\]\([^)]*\)'), r'[\1]'),
    # Links.
    (re.compile(r'\[([^\]]+)\]\(([^)]+)\)'), r'\1 (\2)'),
    # Reference-style links.
    (re.compile(r'\[([^\]]+)\]\[[^\]]*\]'), r'\1'),
    # Inline code.
    (re.compile(r'`([^`]*)`'), r'\1'),
    # Bold / italic.
    (re.compile(r'\*\*([^*]+)\*\*'), r'\1'),
    (re.compile(r'__([^_]+)__'), r'\1'),
    (re.compile(r'\*([^*]+)\*'), r'\1'),
    (re.compile(r'_([^_]+)_'), r'\1'),
    # Strikethrough.
    (re.compile(r'~~([^~]+)~~'), r'\1'),
)
_MD_SETEXT_EQ_RE = re.compile(r'^=+\s*$')
_MD_SETEXT_DASH_RE = re.compile(r'^-+\s*$')
_MD_BULLET_PREFIX_RE = re.compile(r'^\s*[-*+]\s')
_MD_ATX_RE = re.compile(r'^(#{1,6})\s+(.*)$')
_MD_HR_RE = re.compile(r'^(\*{3,}|-{3,}|_{3,})\s*$')
_MD_LIST_RE = re.compile(r'^(\s*)([-*+]|\d+\.)\s+(.*)$')
_ANSI_ESCAPE_RE = re.compile(r'\x1b\[[0-9;]*[A-Za-z]')

_RST_INLINE_SUBS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Roles like :role:`text`.
    (re.compile(r':[A-Za-z][A-Za-z0-9_-]*:`([^`]*)`'), r'\1'),
    # Links `text <url>`_.
    (re.compile(r'`([^<`]+?)\s*<[^>]+>`_'), r'\1'),
    # Inline literals / code.
    (re.compile(r'``([^`]*)``'), r'\1'),
    # Strong / emphasis.
    (re.compile(r'\*\*([^*]+)\*\*'), r'\1'),
    (re.compile(r'\*([^*]+)\*'), r'\1'),
    # Single backticks.
    (re.compile(r'`([^`]*)`'), r'\1'),
)
_RST_HEADING_CHARS = frozenset('=-~^_*+#:."\'')
_RST_DIRECTIVE_RE = re.compile(r'^\.\.\s+([A-Za-z][\w-]*)(::)?\s*(.*)$')

_HTML_BLOCK_START_TAGS = frozenset(
    (
        'p',
        'div',
        'section',
        'article',
        'header',
        'footer',
        'main',
        'aside',
        'nav',
        'h1',
        'h2',
        'h3',
        'h4',
        'h5',
        'h6',
        'li',
        'tr',
        'blockquote',
        'pre',
        'table',
        'ul',
        'ol',
        'title',
    )
)
_HTML_BLOCK_END_TAGS = _HTML_BLOCK_START_TAGS - {'ul', 'ol'}
_HTML_BLANK_LINES_RE = re.compile(r'\n{3,}')


def _strip_markdown_inline(text: str) -> str:
    """Remove inline Markdown markup, keeping the readable text."""
    for pattern, replacement in _MD_INLINE_SUBS:
        text = pattern.sub(replacement, text)
    return text


def _render_markdown(text: str) -> str:
    # Prefer a dedicated renderer when one is available: an external
    # `mdcat` (clean terminal output) or the pure-Python `markdown`
    # package (convert to HTML, then reuse the HTML renderer).
    if shutil.which('mdcat'):
        try:
            proc = subprocess.run(
                ['mdcat', '--no-colour', '--columns', '0', '-'],
                input=text or '',
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=30,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                return proc.stdout.rstrip('\n') + '\n'
        except (OSError, subprocess.SubprocessError):
            pass

    try:
        import markdown as _markdown  # type: ignore[import-not-found]

        html = _markdown.markdown(text or '', extensions=['extra', 'sane_lists'])
        rendered = _render_html(html)
        if rendered.strip():
            return rendered
    except Exception:
        pass

    # Fallback: glow (strips ANSI escapes from its output).
    if shutil.which('glow'):
        try:
            proc = subprocess.run(
                ['glow', '-s', 'dark', '-w', '0', '-'],
                input=text or '',
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=30,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                cleaned = _ANSI_ESCAPE_RE.sub('', proc.stdout)
                if cleaned.strip():
                    return cleaned.rstrip('\n') + '\n'
        except (OSError, subprocess.SubprocessError):
            pass

    lines = (text or '').splitlines()
    out: list[str] = []
    i = 0
    n = len(lines)
    in_code = False
    while i < n:
        line = lines[i]
        stripped = line.strip()

        # Fenced code blocks: keep content verbatim.
        if stripped.startswith('```') or stripped.startswith('~~~'):
            fence = stripped[:3]
            if not in_code:
                in_code = True
                lang = stripped[3:].strip()
                out.append(fence + ((' ' + lang) if lang else ''))
            else:
                in_code = False
                out.append(fence)
            i += 1
            continue
        if in_code:
            out.append(line)
            i += 1
            continue

        # Setext headings (title followed by === or ---).
        if i + 1 < n and stripped:
            next_stripped = lines[i + 1].strip()
            if _MD_SETEXT_EQ_RE.match(next_stripped):
                title = _strip_markdown_inline(stripped)
                out.append(title)
                out.append('=' * max(3, len(title)))
                i += 2
                continue
            if _MD_SETEXT_DASH_RE.match(next_stripped) and not _MD_BULLET_PREFIX_RE.match(line):
                title = _strip_markdown_inline(stripped)
                out.append(title)
                out.append('-' * max(3, len(title)))
                i += 2
                continue

        # ATX headings.
        m = _MD_ATX_RE.match(line)
        if m:
            level = len(m.group(1))
            title = _strip_markdown_inline(m.group(2).strip())
            if level <= 2:
                underline = '=' if level == 1 else '-'
                out.append(title)
                out.append(underline * max(3, len(title)))
            else:
                out.append('#' * level + ' ' + title)
            i += 1
            continue

        # Horizontal rules.
        if _MD_HR_RE.match(stripped):
            out.append('-' * 40)
            i += 1
            continue

        # Blockquotes.
        if stripped.startswith('>'):
            content = _strip_markdown_inline(stripped.lstrip('>').lstrip())
            out.append('> ' + content)
            i += 1
            continue

        # Lists (keep markers, strip inline markup).
        m = _MD_LIST_RE.match(line)
        if m:
            indent = m.group(1)
            marker = m.group(2)
            content = _strip_markdown_inline(m.group(3))
            out.append(f'{indent}{marker} {content}')
            i += 1
            continue

        out.append(_strip_markdown_inline(line))
        i += 1

    return '\n'.join(out)


def _strip_rst_inline(text: str) -> str:
    """Remove inline reStructuredText markup, keeping readable text."""
    for pattern, replacement in _RST_INLINE_SUBS:
        text = pattern.sub(replacement, text)
    return text


def _render_rst(text: str) -> str:
    # Optional well-known converters.
    try:
        from docutils.core import publish_string  # type: ignore[import-not-found]

        published = publish_string(text or '', writer_name='text')
        rendered = (
            published.decode('utf-8')
            if isinstance(published, (bytes, bytearray))
            else str(published)
        )
        if rendered.strip():
            return rendered
    except Exception:
        pass
    for pandoc_cmd in (['pandoc', '-f', 'rst', '-t', 'plain'],):
        if shutil.which(pandoc_cmd[0]):
            try:
                proc = subprocess.run(
                    pandoc_cmd,
                    input=text or '',
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    encoding='utf-8',
                    errors='replace',
                    timeout=30,
                )
                if proc.returncode == 0 and proc.stdout.strip():
                    return proc.stdout.rstrip('\n') + '\n'
            except (OSError, subprocess.SubprocessError):
                pass

    lines = (text or '').splitlines()
    out: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()
        # Section headings: title followed by a line of punctuation.
        if i + 1 < n and stripped:
            next_stripped = lines[i + 1].strip()
            if (
                len(next_stripped) >= 3
                and len(next_stripped) >= len(stripped) - 2
                and _RST_HEADING_CHARS.issuperset(next_stripped)
            ):
                out.append(stripped)
                out.append(next_stripped)
                i += 2
                continue

        # Directives: keep a readable label.
        m = _RST_DIRECTIVE_RE.match(line)
        if m:
            name = m.group(1)
            rest = m.group(3) or ''
            label = '.. ' + name + '::' + ((' ' + rest) if rest else '')
            out.append(label)
            i += 1
            continue

        out.append(_strip_rst_inline(line))
        i += 1

    return '\n'.join(out)


def _render_html(text: str) -> str:
    # Optional well-known converters.
    if shutil.which('lynx'):
        try:
            proc = subprocess.run(
                ['lynx', '-dump', '-stdin'],
                input=text or '',
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=30,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                return proc.stdout.rstrip('\n') + '\n'
        except (OSError, subprocess.SubprocessError):
            pass
    if shutil.which('w3m'):
        try:
            proc = subprocess.run(
                ['w3m', '-dump', '-T', 'text/html'],
                input=text or '',
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=30,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                return proc.stdout.rstrip('\n') + '\n'
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        import html2text  # type: ignore[import-not-found]

        converter = html2text.HTML2Text()
        converter.body_width = 0
        rendered = converter.handle(text or '')
        if rendered.strip():
            return rendered
    except Exception:
        pass

    import html as _html
    from html.parser import HTMLParser

    class _Renderer(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.parts: list[str] = []
            self._pre = False

        def _ensure_block(self) -> None:
            if not self.parts:
                return
            # Inspect at most the last 2 non-empty characters in O(1) amortized
            # time instead of re-joining all parts on every tag (docs/bugs/159.md).
            tail = ''
            for part in reversed(self.parts):
                if part:
                    tail = part + tail
                    if len(tail) >= 2:
                        break
            if not tail or tail.endswith('\n\n'):
                return
            if tail.endswith('\n'):
                self.parts.append('\n')
            else:
                self.parts.append('\n\n')

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            tag = tag.lower()
            if tag == 'br':
                self.parts.append('\n')
            elif tag == 'hr':
                self._ensure_block()
                self.parts.append('---\n')
            elif tag in _HTML_BLOCK_START_TAGS:
                self._ensure_block()
                if tag == 'pre':
                    self._pre = True
            elif tag in ('td', 'th'):
                if self.parts and not self.parts[-1].endswith(('\n', ' ', '\t')):
                    self.parts.append(' ')

        def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            tag = tag.lower()
            if tag == 'br':
                self.parts.append('\n')
            elif tag == 'hr':
                self._ensure_block()
                self.parts.append('---\n')

        def handle_endtag(self, tag: str) -> None:
            tag = tag.lower()
            if tag in _HTML_BLOCK_END_TAGS:
                self.parts.append('\n')
                if tag == 'pre':
                    self._pre = False
            elif tag in ('td', 'th'):
                self.parts.append(' ')

        def handle_data(self, data: str) -> None:
            if self._pre:
                self.parts.append(data)
                return
            collapsed = ' '.join(data.split())
            if not collapsed:
                return
            if self.parts and not self.parts[-1].endswith(('\n', ' ')):
                self.parts.append(' ')
            self.parts.append(collapsed)

        def handle_entityref(self, name: str) -> None:
            self.handle_data(_html.unescape('&%s;' % name))

        def handle_charref(self, name: str) -> None:
            self.handle_data(_html.unescape('&#%s;' % name))

    renderer = _Renderer()
    try:
        renderer.feed(text or '')
    except Exception:
        return text or ''
    result = ''.join(renderer.parts)
    # Collapse runs of blank lines.
    result = _HTML_BLANK_LINES_RE.sub('\n\n', result)
    return result.strip()


def render_text(text: str, fmt: str = 'text') -> str:
    """Render markup `text` of kind `fmt` to pretty terminal text."""
    fmt = (fmt or 'text').lower()
    if fmt == 'html':
        return _render_html(text)
    if fmt in ('markdown', 'md'):
        return _render_markdown(text)
    if fmt in ('rst', 'restructuredtext', 'rest'):
        return _render_rst(text)
    return text or ''


@location_cmd('render', accepts_force=False)
@location_cmd('rend', accepts_force=False)
def render(editor: Any, location: Any = None, force: bool = False) -> None:
    """
    Render the current (or named) file as pretty read-only text in a split
    (`:render` / `:render {file}`). The original buffer is never modified.
    """
    from .common import NO_FILE_NAME_TEXT
    from .files import _read_location_text

    eb = editor.window_arrangement.active_editor_buffer

    source_text = ''
    source_location = None
    if location is not None:
        read_text = _read_location_text(editor, str(location))
        if read_text is None:
            return
        source_text = read_text
        source_location = str(location)
    elif eb is not None:
        source_text = eb.buffer.text
        source_location = eb.location
    else:
        editor.show_message(NO_FILE_NAME_TEXT)
        return

    fmt = _detect_render_format(
        source_location, getattr(eb, 'filetype', None) if eb is not None else None, source_text
    )
    rendered = render_text(source_text, fmt)
    if not rendered.strip():
        rendered = source_text

    try:
        from vith.thai import note_document_text

        note_document_text(rendered)
        if source_location:
            note_document_text(str(source_location))
    except Exception:
        pass

    editor.show_info(rendered, title='[Render]')


# Alias for callers/tests that expect the `_command` spelling.
render_command = render
