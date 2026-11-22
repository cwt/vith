"""
Real-time asynchronous document rendering for live preview splits
(`:render`, `:render-split-v`, `:render-split-h`).

Delegates parsing, wrapping, tables, and terminal styling to the optional
`rich` and `rich-rst` libraries (installed via `vith[render]`), converting
`rich.segment.Segment` runs directly into `prompt_toolkit` `StyleAndTextTuples`
so preview panes support bold, italic, underline, strike, dim, truecolor,
Vi navigation/search/yank, and Thai/Lao cluster rendering with zero impact on
editing latency.
"""

from __future__ import annotations

import asyncio
import csv
import importlib.util
import io
import json
import os
import re
import subprocess
import time
import weakref
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from prompt_toolkit.document import Document
from prompt_toolkit.filters import to_filter
from prompt_toolkit.formatted_text import ANSI, StyleAndTextTuples, to_formatted_text
from prompt_toolkit.formatted_text.utils import split_lines

from .lexer import sniff_filetype_from_text
from .thai import note_document_text

__all__ = (
    'LiveRenderController',
    'ParseIncompleteError',
    'RenderDependencyError',
    'RenderResult',
    'ansi_to_render_result',
    'close_live_render_for_buffer',
    'detect_render_format',
    'map_source_row_to_preview',
    'open_or_update_live_render',
    'render_document',
)

DEFAULT_RENDER_WIDTH = 80
MIN_RENDER_WIDTH = 20
_CSV_MAX_ROWS = 5000
_EXTERNAL_CMD_TIMEOUT = 5.0

_ANSI_SGR_RE = re.compile(r'\x1b\[[0-9;]*m')
_MD_HEADING_OR_FENCE_RE = re.compile(r'^(?:#{1,6}\s+\S|```|~~~)', re.MULTILINE)
_MD_ATX_HEADING_RE = re.compile(r'^#{1,6}\s+(.+?)\s*#*\s*$')
_MD_SETEXT_UNDERLINE_RE = re.compile(r'^(?:={3,}|-{3,})\s*$')
_MD_FENCE_RE = re.compile(r'^\s*(?:```|~~~)')
_MD_LINK_RE = re.compile(r'\[([^\]]+)\]\([^)]*\)')
_MD_INLINE_MARK_RE = re.compile(r'[*_`~]+')
_RST_DIRECTIVE_RE = re.compile(r'^\.\.\s+[A-Za-z][\w-]*::', re.MULTILINE)
_RST_UNDERLINE_RE = re.compile(r'^([=\-~^_*+#:."\'`])\1{2,}\s*$')
_JSON_TOP_KEY_RE = re.compile(r'^\s{0,2}"([^"]+)"\s*:')

_EXT_TO_FORMAT: dict[str, str] = {
    'md': 'markdown',
    'markdown': 'markdown',
    'mdown': 'markdown',
    'mkd': 'markdown',
    'rst': 'rst',
    'rest': 'rst',
    'json': 'json',
    'csv': 'csv',
    'tsv': 'tsv',
    'ansi': 'ansi',
}

_FT_TO_FORMAT: dict[str, str] = {
    'markdown': 'markdown',
    'md': 'markdown',
    'rst': 'rst',
    'restructuredtext': 'rst',
    'json': 'json',
    'csv': 'csv',
    'tsv': 'tsv',
    'ansi': 'ansi',
}

# Rich standard 16 ANSI color indices -> prompt_toolkit named ANSI styles.
_ANSI_16_NAMES: tuple[str, ...] = (
    'ansiblack',
    'ansired',
    'ansigreen',
    'ansiyellow',
    'ansiblue',
    'ansimagenta',
    'ansicyan',
    'ansigray',
    'ansibrightblack',
    'ansibrightred',
    'ansibrightgreen',
    'ansibrightyellow',
    'ansibrightblue',
    'ansibrightmagenta',
    'ansibrightcyan',
    'ansiwhite',
)


class RenderDependencyError(RuntimeError):
    """Raised when an optional rendering dependency (`rich` or `rich-rst`) is missing."""


class ParseIncompleteError(ValueError):
    """Raised when a strict format (e.g., JSON mid-edit) cannot parse yet."""


@dataclass(frozen=True)
class RenderResult:
    """Styled lines, plain text, and source->preview line anchors from a render pass."""

    plain_text: str
    styled_lines: list[StyleAndTextTuples]
    format_name: str
    is_stale: bool = False
    anchors: tuple[tuple[int, int], ...] = ()


def _has_package(name: str) -> bool:
    """Side-effect-free availability probe for optional render dependencies."""
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def detect_render_format(
    location: str | None,
    filetype: str | None,
    text: str,
    custom_commands: dict[str, str] | None = None,
) -> str:
    """
    Resolve the render format name for a buffer from `:set filetype`, file
    extension, custom `[render.commands]` mappings, or content heuristics.
    """
    commands = custom_commands or {}
    ft = (filetype or '').strip().lower()
    ext = ''
    if location:
        ext = os.path.splitext(str(location))[1].lstrip('.').lower()

    # 1. Explicit filetype match (custom command override takes precedence).
    if ft:
        if ft in commands:
            return ft
        mapped_ft = _FT_TO_FORMAT.get(ft)
        if mapped_ft is not None:
            return mapped_ft

    # 2. File extension match (custom command override takes precedence).
    if ext:
        if ext in commands:
            return ext
        mapped_ext = _EXT_TO_FORMAT.get(ext)
        if mapped_ext is not None:
            return mapped_ext

    # 3. Content heuristics for extensionless / unnamed buffers.
    sample = text if len(text) <= 4096 else text[:4096]
    if _ANSI_SGR_RE.search(sample):
        return 'ansi'

    sniffed = sniff_filetype_from_text(sample)
    if sniffed:
        if sniffed in commands:
            return sniffed
        mapped_sniff = _FT_TO_FORMAT.get(sniffed)
        if mapped_sniff is not None:
            return mapped_sniff

    if _RST_DIRECTIVE_RE.search(sample):
        return 'rst'
    if _MD_HEADING_OR_FENCE_RE.search(sample):
        return 'markdown'

    # 4. Known code/config filetype falls back to Rich Syntax; untitled prose to Markdown.
    if ft or sniffed:
        return 'syntax'
    return 'markdown'


def _required_package_for_format(
    fmt: str,
    custom_commands: dict[str, str] | None = None,
) -> str | None:
    """Return the optional package needed for `fmt`, or `None` if built-in/external."""
    if custom_commands and fmt in custom_commands:
        return None
    if fmt == 'ansi':
        return None
    if fmt == 'rst':
        if not _has_package('rich'):
            return 'rich'
        if not _has_package('rich_rst'):
            return 'rich-rst'
        return None
    if not _has_package('rich'):
        return 'rich'
    return None


def _rich_color_to_ptk(color: Any, *, is_bg: bool) -> str:
    """Convert a `rich.color.Color` to a `prompt_toolkit` color token."""
    if color is None:
        return ''
    color_type = getattr(color, 'type', None)
    if color_type is None:
        return ''
    type_name = getattr(color_type, 'name', '')
    if type_name == 'DEFAULT':
        return ''
    prefix = 'bg:' if is_bg else ''
    if type_name == 'STANDARD':
        num = getattr(color, 'number', None)
        if isinstance(num, int) and 0 <= num < len(_ANSI_16_NAMES):
            return prefix + _ANSI_16_NAMES[num]
    try:
        hex_color = color.get_truecolor().hex
    except Exception:
        return ''
    return prefix + hex_color


@lru_cache(maxsize=1024)
def _rich_style_to_ptk(style: Any) -> str:
    """Convert a hashable `rich.style.Style` into a `prompt_toolkit` style string."""
    if style is None:
        return ''
    parts: list[str] = []
    fg = _rich_color_to_ptk(getattr(style, 'color', None), is_bg=False)
    if fg:
        parts.append(fg)
    bg = _rich_color_to_ptk(getattr(style, 'bgcolor', None), is_bg=True)
    if bg:
        parts.append(bg)
    if getattr(style, 'bold', False):
        parts.append('bold')
    if getattr(style, 'italic', False):
        parts.append('italic')
    if getattr(style, 'underline', False):
        parts.append('underline')
    if getattr(style, 'strike', False):
        parts.append('strike')
    if getattr(style, 'dim', False):
        parts.append('dim')
    if getattr(style, 'reverse', False):
        parts.append('reverse')
    if getattr(style, 'blink', False):
        parts.append('blink')
    if getattr(style, 'conceal', False):
        parts.append('hidden')
    return ' '.join(parts)


def _coalesce_line_fragments(fragments: StyleAndTextTuples) -> StyleAndTextTuples:
    """Merge consecutive `(style, text)` fragments that share an identical style."""
    merged: StyleAndTextTuples = []
    cur_style: str | None = None
    cur_chunks: list[str] = []
    for frag in fragments:
        style = frag[0]
        text = frag[1]
        if not text:
            continue
        if style == cur_style:
            cur_chunks.append(text)
        else:
            if cur_style is not None and cur_chunks:
                merged.append((cur_style, ''.join(cur_chunks)))
            cur_style = style
            cur_chunks = [text]
    if cur_style is not None and cur_chunks:
        merged.append((cur_style, ''.join(cur_chunks)))
    return merged


def _build_render_result(
    styled_lines: list[StyleAndTextTuples],
    format_name: str,
    *,
    is_stale: bool = False,
) -> RenderResult:
    """Build a `RenderResult` preserving 1-to-1 line alignment between text and styles."""
    if not styled_lines:
        styled_lines = [[]]
    plain_lines = [''.join(frag[1] for frag in line) for line in styled_lines]
    return RenderResult(
        plain_text='\n'.join(plain_lines),
        styled_lines=styled_lines,
        format_name=format_name,
        is_stale=is_stale,
    )


def ansi_to_render_result(raw_ansi: str, format_name: str = 'ansi') -> RenderResult:
    """
    Parse an ANSI SGR string into coalesced `StyleAndTextTuples` lines + clean
    plain text using `prompt_toolkit.formatted_text.ANSI`.
    """
    normalized = (raw_ansi or '').replace('\r\n', '\n').replace('\r', '\n')
    if normalized.endswith('\n'):
        normalized = normalized[:-1]
    if not normalized:
        return _build_render_result([[]], format_name)

    parsed_lines = list(split_lines(to_formatted_text(ANSI(normalized))))
    styled_lines = [_coalesce_line_fragments(line) for line in parsed_lines]
    return _build_render_result(styled_lines, format_name)


def _render_rich_object(renderable: Any, width: int, format_name: str) -> RenderResult:
    """Render any Rich renderable directly via `Console.render_lines()`."""
    from rich.console import Console

    safe_width = max(MIN_RENDER_WIDTH, int(width))
    console = Console(
        file=io.StringIO(),
        width=safe_width,
        force_terminal=True,
        color_system='truecolor',
        legacy_windows=False,
    )
    options = console.options.update_width(safe_width)
    raw_lines = console.render_lines(renderable, options, pad=False, new_lines=False)

    styled_lines: list[StyleAndTextTuples] = []
    for segments in raw_lines:
        line_frags: StyleAndTextTuples = []
        for seg in segments:
            if seg.control is not None or not seg.text:
                continue
            seg_text = seg.text.replace('\r', '').replace('\n', '')
            if not seg_text:
                continue
            line_frags.append((_rich_style_to_ptk(seg.style), seg_text))
        styled_lines.append(_coalesce_line_fragments(line_frags))

    # Drop a single trailing blank line only when Rich pads an extra empty footer.
    return _build_render_result(styled_lines, format_name)


def _render_csv_table(text: str, delimiter: str) -> Any:
    """Parse CSV/TSV text into a Rich `Table` (row-capped for responsiveness)."""
    from rich import box
    from rich.console import Group
    from rich.table import Table
    from rich.text import Text

    # csv.reader rejects a bare CR in an unquoted field, and file/paste paths
    # can leave one in the buffer (docs/bugs/182.md).
    normalized = text.replace('\r\n', '\n').replace('\r', '\n')
    reader = csv.reader(io.StringIO(normalized), delimiter=delimiter)
    rows: list[list[str]] = []
    truncated = False
    for idx, row in enumerate(reader):
        if idx > _CSV_MAX_ROWS:
            truncated = True
            break
        rows.append(row)

    if not rows:
        return Text('')

    num_cols = max(len(r) for r in rows)
    header = rows[0] + [''] * (num_cols - len(rows[0]))
    table = Table(show_header=True, header_style='bold cyan', box=box.ROUNDED, expand=False)
    # Rich interprets raw strings as markup; wrap every cell in Text so data
    # like '[/]' or '[bold]x' is displayed literally (docs/bugs/169.md).
    for col_name in header:
        table.add_column(Text(col_name))
    for row in rows[1:]:
        padded = row + [''] * (num_cols - len(row))
        table.add_row(*(Text(cell) for cell in padded))

    if truncated:
        note = Text(f'... truncated after {_CSV_MAX_ROWS} rows ...', style='dim italic')
        return Group(table, note)
    return table


def _render_external_command(command: str, text: str, width: int, format_name: str) -> RenderResult:
    """Pipe buffer text through a user-configured external CLI renderer."""
    env = os.environ.copy()
    env['COLUMNS'] = str(max(MIN_RENDER_WIDTH, int(width)))
    try:
        proc = subprocess.run(
            command,
            shell=True,
            input=text,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=_EXTERNAL_CMD_TIMEOUT,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise ParseIncompleteError(f'Renderer command timed out: {command}') from exc
    except OSError as exc:
        raise ParseIncompleteError(f'Cannot run renderer command {command!r}: {exc}') from exc

    if proc.returncode != 0 and not proc.stdout.strip():
        err_msg = (proc.stderr or '').strip() or f'exit code {proc.returncode}'
        raise ParseIncompleteError(f'Renderer command failed ({err_msg})')
    return ansi_to_render_result(proc.stdout, format_name=format_name)


def _normalize_landmark(text: str) -> str:
    return ' '.join(text.split()).lower()


def _extract_source_landmarks(source_text: str, fmt: str) -> list[tuple[int, str, bool]]:
    """
    Extract monotonic `(src_row, normalized_text, prefer_styled)` structural
    landmarks (headings, top-level JSON keys, sampled CSV rows) for accurate
    piecewise-linear scroll synchronization.
    """
    src_lines = source_text.split('\n')
    n = len(src_lines)
    landmarks: list[tuple[int, str, bool]] = []

    if fmt == 'markdown':
        in_fence = False
        for i, line in enumerate(src_lines):
            if _MD_FENCE_RE.match(line):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            m = _MD_ATX_HEADING_RE.match(line)
            if m is not None:
                cleaned = _MD_INLINE_MARK_RE.sub('', _MD_LINK_RE.sub(r'\1', m.group(1)))
                norm = _normalize_landmark(cleaned)
                if norm:
                    landmarks.append((i, norm, True))
                continue
            stripped = line.strip()
            if (
                stripped
                and '|' not in stripped
                and not stripped.startswith(('-', '*', '+', '>'))
                and i + 1 < n
                and _MD_SETEXT_UNDERLINE_RE.match(src_lines[i + 1])
            ):
                cleaned = _MD_INLINE_MARK_RE.sub('', _MD_LINK_RE.sub(r'\1', stripped))
                norm = _normalize_landmark(cleaned)
                if norm:
                    landmarks.append((i, norm, True))
        return landmarks

    if fmt == 'rst':
        for i in range(n - 1):
            stripped = src_lines[i].strip()
            if (
                stripped
                and not stripped.startswith('.. ')
                and _RST_UNDERLINE_RE.match(src_lines[i + 1])
            ):
                if len(src_lines[i + 1].strip()) >= min(3, len(stripped)):
                    norm = _normalize_landmark(stripped)
                    if norm:
                        landmarks.append((i, norm, True))
        return landmarks

    if fmt == 'json':
        for i, line in enumerate(src_lines):
            m = _JSON_TOP_KEY_RE.match(line)
            if m is not None:
                landmarks.append((i, f'"{m.group(1).lower()}"', False))
        return landmarks

    if fmt in ('csv', 'tsv'):
        delim = '\t' if fmt == 'tsv' else ','
        step = max(1, n // 64)
        for i in range(0, min(n, _CSV_MAX_ROWS), step):
            raw = src_lines[i].strip()
            if not raw:
                continue
            first_cell = raw.split(delim, 1)[0].strip().strip('"').strip("'")
            norm = _normalize_landmark(first_cell)
            if len(norm) >= 2:
                landmarks.append((i, norm, False))
        return landmarks

    return landmarks


def _build_line_anchors(
    source_text: str,
    plain_text: str,
    styled_lines: list[StyleAndTextTuples],
    fmt: str,
) -> tuple[tuple[int, int], ...]:
    """
    Build a sorted monotonic table of `(source_row, rendered_row)` anchor pairs
    for fast O(log A) piecewise-linear scroll sync.
    """
    src_last = max(0, source_text.count('\n'))
    ren_lines = plain_text.split('\n') if plain_text else ['']
    ren_last = max(0, len(ren_lines) - 1)
    if src_last == 0 or ren_last == 0:
        return ((0, 0), (src_last, ren_last))

    landmarks = _extract_source_landmarks(source_text, fmt)
    anchors: list[tuple[int, int]] = [(0, 0)]
    if landmarks:
        ren_norm = [_normalize_landmark(ln) for ln in ren_lines]
        ren_is_heading = [
            any('bold' in frag[0] or 'underline' in frag[0] for frag in line)
            for line in styled_lines
        ]
        if len(ren_is_heading) < len(ren_norm):
            ren_is_heading.extend([False] * (len(ren_norm) - len(ren_is_heading)))

        # Index exact normalized lines and styled heading rows once in O(R) so
        # unmatched landmarks never trigger an O(H * R) full-document scan.
        exact_styled: dict[str, list[int]] = {}
        exact_any: dict[str, list[int]] = {}
        styled_rows: list[int] = []
        for r, norm_ln in enumerate(ren_norm):
            if not norm_ln:
                continue
            exact_any.setdefault(norm_ln, []).append(r)
            if ren_is_heading[r]:
                exact_styled.setdefault(norm_ln, []).append(r)
                styled_rows.append(r)

        search_start = 0
        ren_count = len(ren_norm)
        max_fallback_scan = 256
        for src_row, norm_text, prefer_styled in landmarks:
            if src_row <= anchors[-1][0]:
                continue
            matched_row: int | None = None
            if prefer_styled:
                # 1. Exact styled-line hit in O(log R).
                rows_exact = exact_styled.get(norm_text)
                if rows_exact:
                    pos = bisect_left(rows_exact, search_start)
                    if pos < len(rows_exact):
                        matched_row = rows_exact[pos]
                # 2. Bounded substring check across styled heading rows only.
                if matched_row is None and styled_rows:
                    h_pos = bisect_left(styled_rows, search_start)
                    h_end = min(len(styled_rows), h_pos + 64)
                    for idx in range(h_pos, h_end):
                        r = styled_rows[idx]
                        if norm_text in ren_norm[r]:
                            matched_row = r
                            break
            else:
                rows_exact = exact_any.get(norm_text)
                if rows_exact:
                    pos = bisect_left(rows_exact, search_start)
                    if pos < len(rows_exact):
                        matched_row = rows_exact[pos]

            if matched_row is None and (not prefer_styled or len(norm_text) >= 3):
                scan_end = min(ren_count, search_start + max_fallback_scan)
                for r in range(search_start, scan_end):
                    if norm_text in ren_norm[r]:
                        matched_row = r
                        break
            if matched_row is not None and matched_row >= anchors[-1][1]:
                anchors.append((src_row, matched_row))
                search_start = matched_row + 1

    if anchors[-1][0] < src_last:
        anchors.append((src_last, ren_last))
    elif anchors[-1][1] < ren_last:
        anchors[-1] = (src_last, ren_last)
    return tuple(anchors)


def map_source_row_to_preview(
    src_row: int,
    src_line_count: int,
    preview_line_count: int,
    anchors: tuple[tuple[int, int], ...] = (),
) -> int:
    """
    Map 0-based `src_row` to the corresponding 0-based preview row using
    heading/landmark piecewise-linear interpolation in O(log A), falling back
    to proportional mapping when no internal anchors exist.
    """
    if preview_line_count <= 1 or src_line_count <= 1:
        return 0
    clamped = max(0, min(src_row, src_line_count - 1))
    if len(anchors) >= 2:
        idx = bisect_right(anchors, (clamped, preview_line_count + 1))
        if idx <= 0:
            return max(0, min(preview_line_count - 1, anchors[0][1]))
        if idx >= len(anchors):
            return max(0, min(preview_line_count - 1, anchors[-1][1]))
        s0, r0 = anchors[idx - 1]
        s1, r1 = anchors[idx]
        if clamped <= s0 or s1 <= s0:
            return max(0, min(preview_line_count - 1, r0))
        frac = (clamped - s0) / (s1 - s0)
        return max(0, min(preview_line_count - 1, r0 + int(round(frac * (r1 - r0)))))
    frac = clamped / (src_line_count - 1)
    return max(0, min(preview_line_count - 1, int(round(frac * (preview_line_count - 1)))))


def _attach_anchors(source_text: str, result: RenderResult) -> RenderResult:
    anchors = _build_line_anchors(
        source_text, result.plain_text, result.styled_lines, result.format_name
    )
    return RenderResult(
        plain_text=result.plain_text,
        styled_lines=result.styled_lines,
        format_name=result.format_name,
        is_stale=result.is_stale,
        anchors=anchors,
    )


def render_document(
    text: str,
    *,
    location: str | None = None,
    filetype: str | None = None,
    width: int = DEFAULT_RENDER_WIDTH,
    custom_commands: dict[str, str] | None = None,
    code_theme: str = 'monokai',
) -> RenderResult:
    """
    Pure, thread-safe entry point rendering `text` to styled `prompt_toolkit`
    lines (with source->preview landmark anchors) for the detected format.

    Raises:
        RenderDependencyError: when `rich` or `rich-rst` is required but absent.
        ParseIncompleteError: when a strict format (e.g. JSON mid-edit) fails
            to parse so callers can preserve the last valid render.
    """
    fmt = detect_render_format(location, filetype, text, custom_commands=custom_commands)
    safe_width = max(MIN_RENDER_WIDTH, int(width))

    if custom_commands and fmt in custom_commands:
        return _attach_anchors(
            text, _render_external_command(custom_commands[fmt], text, safe_width, fmt)
        )

    if fmt == 'ansi':
        return _attach_anchors(text, ansi_to_render_result(text, format_name='ansi'))

    missing = _required_package_for_format(fmt, custom_commands=custom_commands)
    if missing is not None:
        raise RenderDependencyError(
            f"Optional package '{missing}' is required for :render ({fmt}). "
            "Install with: pip install 'vith[render]'"
        )

    if fmt == 'markdown':
        from rich.markdown import Markdown

        renderable: Any = Markdown(text, code_theme=code_theme, hyperlinks=False)
        return _attach_anchors(text, _render_rich_object(renderable, safe_width, 'markdown'))

    if fmt == 'rst':
        from rich_rst import RestructuredText

        try:
            renderable = RestructuredText(
                text,
                code_theme=code_theme,
                show_errors=False,
                guess_lexer=False,
                allow_file_access=False,
            )
            return _attach_anchors(text, _render_rich_object(renderable, safe_width, 'rst'))
        except Exception as exc:
            raise ParseIncompleteError(f'Incomplete reStructuredText: {exc}') from exc

    if fmt == 'json':
        from rich.json import JSON

        if not text.strip():
            return _attach_anchors(text, _build_render_result([[]], 'json'))
        try:
            json.loads(text)
        except ValueError as exc:
            raise ParseIncompleteError(f'Invalid JSON: {exc}') from exc
        return _attach_anchors(text, _render_rich_object(JSON(text), safe_width, 'json'))

    if fmt in ('csv', 'tsv'):
        delimiter = '\t' if fmt == 'tsv' else ','
        return _attach_anchors(
            text, _render_rich_object(_render_csv_table(text, delimiter), safe_width, fmt)
        )

    # Fallback: Rich Syntax highlighting for known code/config filetypes.
    from rich.syntax import Syntax

    lexer_name = (filetype or '').strip().lower() or sniff_filetype_from_text(text) or 'text'
    syntax = Syntax(text, lexer_name, theme=code_theme, word_wrap=True)
    return _attach_anchors(text, _render_rich_object(syntax, safe_width, lexer_name))


class LiveRenderController:
    """
    Coordinates debounced asynchronous re-rendering from a source `EditorBuffer`
    into a read-only scratch preview `EditorBuffer` split.
    """

    def __init__(
        self,
        editor: Any,
        source_eb: Any,
        preview_eb: Any,
        preview_window: Any,
        orientation: str,
    ) -> None:
        self._editor_ref = weakref.ref(editor)
        self._source_ref = weakref.ref(source_eb)
        self._preview_ref = weakref.ref(preview_eb)
        self._preview_window_ref = weakref.ref(preview_window)
        self.orientation = orientation

        self._closed = False
        self._in_flight = False
        self._pending = False
        self._pending_immediate = False
        self._timer_handle: Any = None
        self._first_dirty_at: float | None = None
        self._last_width: int = self._compute_width()
        self._last_format: str = ''
        self._has_good_render = False
        self._anchors: tuple[tuple[int, int], ...] = ()
        self._last_sync_state: tuple[int, int, int] | None = None
        self._preview_line_starts: list[int] = [0]
        self._cached_src_row_info: tuple[int, int, int, int] | None = None
        self._cached_src_line_count: tuple[int, int, int, int] | None = None

        source_eb._live_render_controllers.append(self)
        source_eb.buffer.on_cursor_position_changed += self._on_source_cursor_changed
        preview_eb._owned_render_controller = self
        editor._live_render_controllers.append(self)

    @property
    def source_eb(self) -> Any | None:
        return self._source_ref()

    @property
    def preview_eb(self) -> Any | None:
        return self._preview_ref()

    @property
    def preview_window(self) -> Any | None:
        return self._preview_window_ref()

    @property
    def is_closed(self) -> bool:
        return self._closed

    def _compute_width(self) -> int:
        """Measure the preview window's current inner column width."""
        preview_win = self._preview_window_ref()
        pt_win = getattr(preview_win, 'pt_window', None) if preview_win is not None else None
        render_info = getattr(pt_win, 'render_info', None) if pt_win is not None else None
        if render_info is not None:
            win_width = getattr(render_info, 'window_width', 0)
            if isinstance(win_width, int) and win_width >= MIN_RENDER_WIDTH:
                return win_width

        editor = self._editor_ref()
        if editor is not None:
            try:
                cols = editor.application.output.get_size().columns
                if isinstance(cols, int) and cols > 0:
                    if self.orientation == 'vertical':
                        return max(MIN_RENDER_WIDTH, (cols - 1) // 2)
                    return max(MIN_RENDER_WIDTH, cols)
            except Exception:
                pass
        return DEFAULT_RENDER_WIDTH

    def _is_preview_alive(self) -> bool:
        """Whether both source and preview buffers are still mounted in the editor."""
        if self._closed:
            return False
        editor = self._editor_ref()
        source_eb = self._source_ref()
        preview_eb = self._preview_ref()
        if editor is None or source_eb is None or preview_eb is None:
            return False
        wa = editor.window_arrangement
        return any(preview_eb in tab.visible_editor_buffers() for tab in wa.tab_pages) and (
            source_eb in wa.editor_buffers
        )

    def _on_source_cursor_changed(self, _buff: Any) -> None:
        """Keep the preview aligned immediately when moving the cursor in the editor."""
        if self._closed:
            return
        if self.sync_scroll():
            self._invalidate()

    def on_after_render(self) -> None:
        """
        Post-render hook: react to pane width changes (terminal/split resize)
        and source-pane scroll changes (`zt`/`zz`/`zb`/`Ctrl-E`/`Ctrl-Y`/mouse).
        """
        if self._closed:
            return
        if not self._is_preview_alive():
            self.close()
            return
        new_width = self._compute_width()
        if new_width != self._last_width:
            self._last_width = new_width
            self.schedule()
        elif self.sync_scroll():
            self._invalidate()

    def check_width(self) -> None:
        """Schedule a re-render if the preview split's width changed after a resize."""
        self.on_after_render()

    def schedule(self, *, immediate: bool = False) -> None:
        """
        Schedule a debounced background render (with max-wait ceiling).
        Falls back to synchronous rendering when no asyncio loop is running.
        """
        if self._closed:
            return
        if not self._is_preview_alive():
            self.close()
            return

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running event loop (e.g. synchronous unit test): render directly.
            self.render_sync()
            return

        if self._in_flight:
            self._pending = True
            # Remember an urgent request so the coalesced follow-up does not
            # incur the full debounce delay (docs/bugs/183.md).
            self._pending_immediate = self._pending_immediate or immediate
            return

        if self._timer_handle is not None:
            self._timer_handle.cancel()
            self._timer_handle = None

        editor = self._editor_ref()
        debounce_s = max(0.0, float(getattr(editor, 'render_debounce_ms', 150)) / 1000.0)
        max_wait_s = max(debounce_s * 3.0, 0.50)

        now = time.monotonic()
        if self._first_dirty_at is None:
            self._first_dirty_at = now

        if immediate or debounce_s <= 0.0 or (now - self._first_dirty_at) >= max_wait_s:
            delay = 0.0
        else:
            delay = min(debounce_s, max_wait_s - (now - self._first_dirty_at))

        self._timer_handle = loop.call_later(delay, self._dispatch)

    def _snapshot(self) -> tuple[int, str, str | None, str, int, dict[str, str]] | None:
        """Capture immutable source-buffer inputs on the main thread."""
        editor = self._editor_ref()
        source_eb = self._source_ref()
        if editor is None or source_eb is None:
            return None
        version = getattr(source_eb.buffer, '_text_version', 0)
        text = source_eb.buffer.text
        location = source_eb.location
        filetype = source_eb.filetype_override or source_eb.filetype
        width = self._compute_width()
        self._last_width = width
        custom_commands = dict(getattr(editor, 'render_commands', None) or {})
        return version, text, location, filetype, width, custom_commands

    def render_sync(self) -> None:
        """Execute a single render synchronously (used outside a running event loop)."""
        if self._closed or not self._is_preview_alive():
            self.close()
            return
        snap = self._snapshot()
        if snap is None:
            return
        version, text, location, filetype, width, custom_commands = snap
        try:
            result = render_document(
                text,
                location=location,
                filetype=filetype,
                width=width,
                custom_commands=custom_commands,
            )
        except ParseIncompleteError as exc:
            self._apply_error(version, str(exc), incomplete=True)
            return
        except RenderDependencyError as exc:
            self._apply_error(version, str(exc), incomplete=False)
            return
        except Exception as exc:
            # Only ParseIncompleteError means "mid-edit": a renderer-internal
            # failure is permanent and must show a diagnostic (docs/bugs/171.md).
            self._apply_error(version, f'Render error: {exc}', incomplete=False)
            return
        self._apply_result(version, result)

    def _dispatch(self) -> None:
        """Launch one background render job in the default executor."""
        self._timer_handle = None
        self._first_dirty_at = None
        if self._closed or not self._is_preview_alive():
            self.close()
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self.render_sync()
            return

        snap = self._snapshot()
        if snap is None:
            return
        version, text, location, filetype, width, custom_commands = snap
        self._in_flight = True
        self._pending = False

        def in_executor() -> None:
            result: RenderResult | None = None
            err_msg: str | None = None
            incomplete = False
            try:
                result = render_document(
                    text,
                    location=location,
                    filetype=filetype,
                    width=width,
                    custom_commands=custom_commands,
                )
            except ParseIncompleteError as exc:
                err_msg = str(exc)
                incomplete = True
            except RenderDependencyError as exc:
                err_msg = str(exc)
                incomplete = False
            except Exception as exc:
                # A generic renderer failure is not a transient mid-edit
                # condition; show it rather than silently freezing the preview
                # on the last good render (docs/bugs/171.md).
                err_msg = f'Render error: {exc}'
                incomplete = False

            def finish() -> None:
                self._in_flight = False
                if self._closed or not self._is_preview_alive():
                    self.close()
                    return
                if result is not None:
                    self._apply_result(version, result)
                elif err_msg is not None:
                    self._apply_error(version, err_msg, incomplete=incomplete)

                # Coalesce any edits that arrived while this render was in flight.
                source_eb = self._source_ref()
                cur_version = (
                    getattr(source_eb.buffer, '_text_version', 0) if source_eb else version
                )
                if self._pending or cur_version != version:
                    self._pending = False
                    immediate = self._pending_immediate
                    self._pending_immediate = False
                    self.schedule(immediate=immediate)

            loop.call_soon_threadsafe(finish)

        loop.run_in_executor(None, in_executor)

    def _format_title(self, fmt: str, *, stale: bool = False) -> str:
        source_eb = self._source_ref()
        name = source_eb.get_display_name(short=True) if source_eb is not None else 'preview'
        suffix = f'{fmt} • stale' if stale else fmt
        return f'[Render: {name} ({suffix})]'

    def _update_preview_document(
        self,
        preview_eb: Any,
        plain_text: str,
        styled_lines: list[StyleAndTextTuples],
        title: str,
    ) -> None:
        """Atomically update preview buffer text, styled lines, scroll, and Thai gate."""
        preview_eb._rendered_lines = styled_lines
        preview_eb._rendered_version = getattr(preview_eb, '_rendered_version', 0) + 1
        preview_eb.scratch_title = title

        # Precompute O(1) line start offsets for `sync_scroll` so cursor moves
        # never rescan `preview_doc.text` or rebuild line indexes.
        line_starts: list[int] = [0]
        offset = 0
        for line_frags in styled_lines[:-1]:
            offset += sum(len(frag[1]) for frag in line_frags) + 1
            line_starts.append(offset)
        self._preview_line_starts = line_starts

        old_pos = preview_eb.buffer.cursor_position
        clamped_pos = min(old_pos, len(plain_text))

        preview_eb._suppress_text_changed += 1
        try:
            preview_eb.buffer.set_document(
                Document(plain_text, cursor_position=clamped_pos),
                bypass_readonly=True,
            )
            preview_eb._file_content = plain_text
            preview_eb._has_unsaved_changes = False
        finally:
            preview_eb._suppress_text_changed = max(0, preview_eb._suppress_text_changed - 1)

        note_document_text(plain_text)
        self.sync_scroll(force=True)
        self._invalidate()

    def _source_row_and_line_count(self, source_eb: Any) -> tuple[int, int]:
        """
        Return `(cursor_row, line_count)` for `source_eb` in amortized O(1)
        without forcing `Document._line_start_indexes` to materialize on every
        keystroke during live typing.
        """
        buff = source_eb.buffer
        doc = buff.document
        cache = getattr(doc, '_cache', None)
        line_indexes = getattr(cache, 'line_indexes', None) if cache is not None else None
        if line_indexes is not None:
            # Document index is already built (e.g., cursor motions): O(log N) row, O(1) count.
            return doc.cursor_position_row, max(1, len(line_indexes))

        text = doc.text
        pos = doc.cursor_position
        line_start = text.rfind('\n', 0, pos) + 1
        nc_ver = getattr(buff, '_non_cursor_version', -1)
        text_ver = getattr(buff, '_text_version', -1)

        # Fast path: consecutive single-line edits or horizontal moves on the
        # same line leave both `src_row` and `line_count` unchanged in O(1).
        cached = self._cached_src_row_info
        if cached is not None and nc_ver >= 0 and cached[0] == nc_ver and cached[1] == line_start:
            return cached[2], cached[3]

        # Compute row via pure-C `str.count` (no Python list/int allocations and
        # no `Document._cache.line_indexes` materialization).
        src_row = text.count('\n', 0, pos)
        text_len = len(text)
        cached_count = self._cached_src_line_count
        if (
            cached_count is not None
            and nc_ver >= 0
            and text_ver >= 0
            and cached_count[0] == nc_ver
            and cached_count[1] == text_ver
            and cached_count[2] == text_len
        ):
            src_lines = cached_count[3]
        else:
            src_lines = src_row + text.count('\n', pos) + 1
            if nc_ver >= 0 and text_ver >= 0:
                self._cached_src_line_count = (nc_ver, text_ver, text_len, src_lines)

        if nc_ver >= 0:
            self._cached_src_row_info = (nc_ver, line_start, src_row, src_lines)
        return src_row, src_lines

    def sync_scroll(self, *, force: bool = False) -> bool:
        """
        Synchronize the preview cursor row and viewport scroll with the source
        editor's cursor row and screen offset using heading/landmark anchors.

        Moves `preview_eb.buffer.cursor_position` to the mapped target row in
        addition to setting `vertical_scroll`, because `prompt_toolkit`'s
        `Window._scroll` always clamps `vertical_scroll` to keep the buffer
        cursor visible. Returns `True` if preview cursor or scroll changed.
        """
        if self._closed:
            return False
        editor = self._editor_ref()
        source_eb = self._source_ref()
        preview_eb = self._preview_ref()
        preview_win = self._preview_window_ref()
        if editor is None or source_eb is None or preview_eb is None or preview_win is None:
            return False
        if not getattr(editor, 'render_sync_scroll', True):
            return False
        active_tab = editor.window_arrangement.active_tab
        if active_tab is None or active_tab.active_window is preview_win:
            # Never fight the user while they are manually scrolling the preview pane.
            return False
        active_win = active_tab.active_window
        if active_win is None or active_win.editor_buffer is not source_eb:
            return False
        preview_pt_win = getattr(preview_win, 'pt_window', None)
        if preview_pt_win is None:
            return False

        src_row, src_lines = self._source_row_and_line_count(source_eb)
        src_pt_win = getattr(active_win, 'pt_window', None)
        src_vscroll = getattr(src_pt_win, 'vertical_scroll', 0) if src_pt_win is not None else 0
        preview_version = getattr(preview_eb, '_rendered_version', 0)

        state = (src_row, src_vscroll, preview_version)
        if not force and state == self._last_sync_state:
            return False
        self._last_sync_state = state

        line_starts = self._preview_line_starts
        preview_lines = max(1, len(line_starts))

        target_row = map_source_row_to_preview(src_row, src_lines, preview_lines, self._anchors)
        clamped_row = max(0, min(target_row, len(line_starts) - 1))
        target_pos = min(line_starts[clamped_row], len(preview_eb.buffer.text))

        render_info = getattr(preview_pt_win, 'render_info', None)
        if render_info is not None:
            win_height = max(1, int(getattr(render_info, 'window_height', 1)))
        else:
            win_height = 1
        max_scroll = max(0, preview_lines - win_height)

        if src_pt_win is not None:
            src_screen_y = max(0, src_row - src_vscroll)
            if render_info is not None:
                src_screen_y = min(src_screen_y, win_height - 1)
            desired_scroll = max(0, min(max_scroll, clamped_row - src_screen_y))
        else:
            desired_scroll = max(0, min(max_scroll, clamped_row))

        old_cursor = preview_eb.buffer.cursor_position
        old_scroll = getattr(preview_pt_win, 'vertical_scroll', 0)
        if target_pos != old_cursor:
            preview_eb.buffer.cursor_position = target_pos
        if desired_scroll != old_scroll:
            preview_pt_win.vertical_scroll = desired_scroll
        return target_pos != old_cursor or desired_scroll != old_scroll

    def _invalidate(self) -> None:
        editor = self._editor_ref()
        if editor is not None:
            try:
                editor.application.invalidate()
            except Exception:
                pass

    def _apply_result(self, _version: int, result: RenderResult) -> None:
        preview_eb = self._preview_ref()
        if preview_eb is None:
            return
        self._last_format = result.format_name
        self._has_good_render = True
        self._anchors = result.anchors
        title = self._format_title(result.format_name, stale=result.is_stale)
        self._update_preview_document(preview_eb, result.plain_text, result.styled_lines, title)

    def _apply_error(self, _version: int, message: str, *, incomplete: bool) -> None:
        preview_eb = self._preview_ref()
        if preview_eb is None:
            return
        if incomplete and self._has_good_render:
            # Keep the last valid render visible while the user finishes editing.
            fmt = self._last_format or 'text'
            preview_eb.scratch_title = self._format_title(fmt, stale=True)
            self._invalidate()
            return
        # The preview now shows a diagnostic, not a previously valid render:
        # clear the flag so a later mid-edit error is classified correctly
        # (docs/bugs/172.md).
        self._has_good_render = False
        # Keep one styled line per plain-text line so the lexer and
        # _preview_line_starts stay aligned (docs/bugs/173.md).
        styled: list[StyleAndTextTuples] = [
            [('ansired italic', line)] for line in message.split('\n')
        ]
        # A diagnostic is never a "stale" render — only the retention branch
        # and a genuinely stale RenderResult set that badge (docs/bugs/172.md).
        title = self._format_title(self._last_format or 'error', stale=False)
        self._update_preview_document(preview_eb, message, styled, title)

    def close(self) -> None:
        """Cancel pending timers and detach from source, preview, and editor."""
        if self._closed:
            return
        self._closed = True
        if self._timer_handle is not None:
            self._timer_handle.cancel()
            self._timer_handle = None
        source_eb = self._source_ref()
        if source_eb is not None:
            try:
                source_eb.buffer.on_cursor_position_changed -= self._on_source_cursor_changed
            except Exception:
                pass
            try:
                source_eb._live_render_controllers.remove(self)
            except ValueError:
                pass
        preview_eb = self._preview_ref()
        if preview_eb is not None and getattr(preview_eb, '_owned_render_controller', None) is self:
            preview_eb._owned_render_controller = None
        editor = self._editor_ref()
        if editor is not None:
            try:
                editor._live_render_controllers.remove(self)
            except ValueError:
                pass


def _find_active_controller(editor: Any, source_eb: Any) -> LiveRenderController | None:
    """Return the live render controller for `source_eb` in the active tab, if any."""
    active_tab = editor.window_arrangement.active_tab
    if active_tab is None:
        return None
    tab_windows = set(active_tab.windows())
    for ctrl in list(getattr(source_eb, '_live_render_controllers', ())):
        win = ctrl.preview_window
        if not ctrl.is_closed and win is not None and win in tab_windows:
            return ctrl
    return None


def close_live_render_for_buffer(editor: Any, source_eb: Any) -> bool:
    """Close any active live preview window attached to `source_eb` in the active tab."""
    ctrl = _find_active_controller(editor, source_eb)
    if ctrl is None:
        return False
    preview_win = ctrl.preview_window
    ctrl.close()
    active_tab = editor.window_arrangement.active_tab
    if active_tab is not None and preview_win is not None and preview_win in active_tab.windows():
        if active_tab.window_count() > 1:
            active_tab._close_window(preview_win)
            editor.window_arrangement._auto_close_new_empty_buffers()
    editor.sync_with_prompt_toolkit()
    return True


def open_or_update_live_render(editor: Any, orientation: str) -> LiveRenderController | None:
    """
    Open, refresh, or re-orient a live async render split (`'vertical'` or
    `'horizontal'`) for the active buffer while keeping focus on the editor.
    """
    wa = editor.window_arrangement
    active_eb = wa.active_editor_buffer
    if active_eb is None or wa.active_tab is None:
        return None

    # If invoked from inside a preview pane, resolve back to its source buffer.
    owned_ctrl = getattr(active_eb, '_owned_render_controller', None)
    if owned_ctrl is not None:
        source_eb = owned_ctrl.source_eb
        if source_eb is None:
            editor.show_message('No source buffer for render preview')
            return None
    else:
        source_eb = active_eb

    custom_commands = getattr(editor, 'render_commands', None) or {}
    fmt = detect_render_format(
        source_eb.location,
        source_eb.filetype_override or source_eb.filetype,
        source_eb.buffer.text,
        custom_commands=custom_commands,
    )
    missing = _required_package_for_format(fmt, custom_commands=custom_commands)
    if missing is not None:
        editor.show_message(
            f"Optional package '{missing}' is required for :render ({fmt}). "
            "Install with: pip install 'vith[render]'"
        )
        return None

    existing = _find_active_controller(editor, source_eb)
    if existing is not None:
        if existing.orientation == orientation:
            existing.schedule(immediate=True)
            return existing
        close_live_render_for_buffer(editor, source_eb)

    # Create the read-only scratch preview buffer on the right (vertical) or
    # bottom (horizontal) while keeping cursor focus in the source editor pane.
    if orientation == 'horizontal':
        preview_window = wa.hsplit(text='', after=True, focus_new=False)
    else:
        preview_window = wa.vsplit(text='', after=True, focus_new=False)

    if preview_window is None:
        return None

    preview_eb = preview_window.editor_buffer
    preview_eb.is_scratch = True
    preview_eb.is_render_preview = True
    preview_eb.scratch_title = f'[Render: {source_eb.get_display_name(short=True)} ({fmt})]'
    preview_eb.buffer.read_only = to_filter(True)
    preview_eb._rendered_lines = [[]]
    preview_eb._rendered_version = 1

    # Sync layout first so `preview_window.pt_window` is bound before scheduling.
    editor.sync_with_prompt_toolkit()

    controller = LiveRenderController(editor, source_eb, preview_eb, preview_window, orientation)
    controller.schedule(immediate=True)
    return controller
