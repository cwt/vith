"""
Cold-text compression for vith's undo/redo history.

The active `prompt_toolkit` buffer stays an ordinary string. Only undo/redo
snapshots — text that is cold and not on the screen — are stored compressed
through `quez.CompressedDeque`.

This is intentionally the simple, single-level design: every snapshot is
compressed. If compression latency becomes a problem on large files, a future
version can keep a small hot window of recent snapshots uncompressed.

Default: Zstandard level 1. Configure in ``.vithrc``::

    editor.cold_text_compression = 'zstd'
    editor.cold_text_compression_level = 1

Supported algorithms: ``zstd``, ``zlib``, ``zlib-fast``, ``zlib-best``,
``lzo``, and ``none``.
"""

from __future__ import annotations

import logging
from collections import deque
from typing import Any, cast

from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from quez import CompressedDeque, NullCompressor, ZlibCompressor
from quez.compressors import Compressor

try:
    from quez import LzoCompressor as _LzoCompressor
except ImportError:  # pragma: no cover - optional dependency
    _LzoCompressor = None

try:
    from quez import ZstdCompressor as _ZstdCompressor
except ImportError:  # pragma: no cover - Python < 3.14 without zstandard
    _ZstdCompressor = None

__all__ = (
    'ColdTextBuffer',
    'ColdTextStack',
    'format_cold_text_stats',
    'make_cold_text_compressor',
)

logger = logging.getLogger(__name__)

DEFAULT_COLD_TEXT_COMPRESSION = 'zstd'
DEFAULT_COLD_TEXT_COMPRESSION_LEVEL = 1


def make_cold_text_compressor(
    name: str | None,
    level: int | None = None,
) -> tuple[Compressor, str, int]:
    """
    Resolve a configured compression algorithm.

    Returns ``(compressor, resolved_name, resolved_level)``. Unknown or
    unavailable algorithms fall back to zstd, then zlib-fast, so a bad
    configuration can never prevent the editor from starting.
    """
    normalized = (name or DEFAULT_COLD_TEXT_COMPRESSION).strip().lower()
    requested_level = DEFAULT_COLD_TEXT_COMPRESSION_LEVEL if level is None else level

    if normalized in ('zstd', 'zstandard'):
        if _ZstdCompressor is not None:
            zstd_level = max(1, min(22, requested_level))
            return _ZstdCompressor(level=zstd_level), 'zstd', zstd_level
        logger.warning(
            'zstd compression is unavailable; falling back to zlib-fast '
            '(install zstandard on Python < 3.14)'
        )
        return ZlibCompressor(level=1), 'zlib-fast', 1

    if normalized == 'zlib':
        zlib_level = max(0, min(9, requested_level))
        return ZlibCompressor(level=zlib_level), 'zlib', zlib_level

    if normalized in ('zlib-fast', 'zlib-1'):
        return ZlibCompressor(level=1), 'zlib-fast', 1

    if normalized in ('zlib-best', 'zlib-9'):
        return ZlibCompressor(level=9), 'zlib-best', 9

    if normalized == 'lzo':
        if _LzoCompressor is not None:
            lzo_level = max(1, min(9, requested_level))
            return _LzoCompressor(level=lzo_level), 'lzo', lzo_level
        logger.warning('python-lzo is unavailable; falling back to zlib-fast')
        return ZlibCompressor(level=1), 'zlib-fast', 1

    if normalized in ('none', 'null', 'off'):
        return NullCompressor(), 'none', 0

    logger.warning('Unknown cold-text compressor %r; using zstd', name)
    if _ZstdCompressor is not None:
        zstd_level = max(1, min(22, requested_level))
        return _ZstdCompressor(level=zstd_level), 'zstd', zstd_level
    return ZlibCompressor(level=1), 'zlib-fast', 1


class ColdTextStack:
    """
    Compressed LIFO stack of ``(text, cursor_position, text_version)`` items.

    The compressed data lives in ``quez.CompressedDeque``. A tiny parallel
    deque keeps only the cursor/version metadata, so the common
    "cursor moved but text did not change" path needs no decompression.
    """

    def __init__(self, compressor: Compressor) -> None:
        self._compressor = compressor
        self._items: CompressedDeque = CompressedDeque(maxsize=None, compressor=compressor)
        self._meta: deque[tuple[int, int]] = deque()

    def __len__(self) -> int:
        return len(self._meta)

    def __bool__(self) -> bool:
        return bool(self._meta)

    @property
    def top_version(self) -> int | None:
        """Text version of the top snapshot, or ``None`` when empty."""
        if not self._meta:
            return None
        return self._meta[-1][0]

    def push(self, text: str, cursor_position: int, text_version: int) -> None:
        """Compress and push a snapshot."""
        self._items.append((text, cursor_position, text_version))
        self._meta.append((text_version, cursor_position))

    def pop(self) -> tuple[str, int, int]:
        """Pop and decompress the newest snapshot."""
        item = cast(tuple[str, int, int], self._items.pop())
        self._meta.pop()
        return item

    def update_top_cursor(self, cursor_position: int) -> None:
        """Update the cursor of the top snapshot without recompressing it."""
        text_version, _old_cursor = self._meta[-1]
        self._meta[-1] = (text_version, cursor_position)

    def clear(self) -> None:
        """Drop all snapshots."""
        self._items = CompressedDeque(maxsize=None, compressor=self._compressor)
        self._meta.clear()

    @property
    def stats(self) -> dict[str, Any]:
        """Quez compression statistics for this stack."""
        return self._items.stats


class ColdTextBuffer(Buffer):
    """
    `prompt_toolkit` buffer whose undo/redo stacks store compressed snapshots.

    The buffer text itself remains an ordinary string. Only the cold
    undo/redo history is compressed (single-level design).
    """

    def __init__(
        self,
        *args: Any,
        cold_compressor: Compressor,
        cold_compression_name: str,
        cold_compression_level: int,
        **kwargs: Any,
    ) -> None:
        self._cold_compressor = cold_compressor
        self.cold_compression_name = cold_compression_name
        self.cold_compression_level = cold_compression_level
        self._text_version = 0
        super().__init__(*args, **kwargs)

    def reset(
        self,
        document: Document | None = None,
        append_to_history: bool = False,
    ) -> None:
        super().reset(document=document, append_to_history=append_to_history)
        self._text_version = 0
        self._undo_stack = ColdTextStack(self._cold_compressor)  # type: ignore[assignment]
        self._redo_stack = ColdTextStack(self._cold_compressor)  # type: ignore[assignment]

    def _text_changed(self) -> None:
        # Covers text assignment, Document replacement and history-index
        # changes, so the undo stack can detect "cursor moved only" in O(1).
        self._text_version += 1
        super()._text_changed()

    def save_to_undo_stack(self, clear_redo_stack: bool = True) -> None:
        undo_stack = cast(ColdTextStack, self._undo_stack)
        if undo_stack and undo_stack.top_version == self._text_version:
            undo_stack.update_top_cursor(self.cursor_position)
        else:
            undo_stack.push(self.text, self.cursor_position, self._text_version)

        if clear_redo_stack:
            cast(ColdTextStack, self._redo_stack).clear()

    def undo(self) -> None:
        undo_stack = cast(ColdTextStack, self._undo_stack)
        redo_stack = cast(ColdTextStack, self._redo_stack)

        while undo_stack:
            text, cursor_position, _text_version = undo_stack.pop()
            if text != self.text:
                redo_stack.push(self.text, self.cursor_position, self._text_version)
                self.document = Document(text, cursor_position=cursor_position)
                break

    def redo(self) -> None:
        redo_stack = cast(ColdTextStack, self._redo_stack)
        if redo_stack:
            self.save_to_undo_stack(clear_redo_stack=False)
            text, cursor_position, _text_version = redo_stack.pop()
            self.document = Document(text, cursor_position=cursor_position)

    def cold_text_stats(self) -> dict[str, Any]:
        """Return undo, redo and total compressed-history statistics."""
        undo = cast(ColdTextStack, self._undo_stack).stats
        redo = cast(ColdTextStack, self._redo_stack).stats
        return {
            'compressor': self.cold_compression_name,
            'level': self.cold_compression_level,
            'undo': undo,
            'redo': redo,
            'total': _combine_stats(undo, redo),
        }


def _combine_stats(*stats: dict[str, Any]) -> dict[str, Any]:
    count = sum(int(item['count']) for item in stats)
    raw_size = sum(int(item['raw_size_bytes']) for item in stats)
    compressed_size = sum(int(item['compressed_size_bytes']) for item in stats)
    ratio = (1 - compressed_size / raw_size) * 100 if raw_size else None
    return {
        'count': count,
        'raw_size_bytes': raw_size,
        'compressed_size_bytes': compressed_size,
        'compression_ratio_pct': ratio,
    }


def _format_size(num_bytes: int) -> str:
    if num_bytes >= 1024 * 1024:
        return '%.2f MB' % (num_bytes / (1024 * 1024))
    if num_bytes >= 1024:
        return '%.1f KB' % (num_bytes / 1024)
    return '%i B' % num_bytes


def _format_stack(label: str, stats: dict[str, Any]) -> str:
    ratio = stats.get('compression_ratio_pct')
    ratio_text = 'n/a' if ratio is None else 'ratio %.1f%% of raw' % (100.0 - float(ratio))
    return '  %-5s %4i snapshots  raw %8s  compressed %8s  %s' % (
        label + ':',
        int(stats['count']),
        _format_size(int(stats['raw_size_bytes'])),
        _format_size(int(stats['compressed_size_bytes'])),
        ratio_text,
    )


def format_cold_text_stats(stats: dict[str, Any]) -> list[str]:
    """Format stats returned by `ColdTextBuffer.cold_text_stats()`."""
    compressor = stats['compressor']
    level = int(stats['level'])
    level_text = '' if level <= 0 else ' level %i' % level
    return [
        'Cold-text compression: %s%s' % (compressor, level_text),
        _format_stack('undo', stats['undo']),
        _format_stack('redo', stats['redo']),
        _format_stack('total', stats['total']),
        '  (raw sizes are pickled bytes; active buffer text is not included)',
    ]
