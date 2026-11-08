"""
Two-tier Thai/Lao syllable & word segmentation (libthai + TCC-lite
heuristic) and the word-span document walkers.

Split out of `vith/thai.py` (docs/bugs/075.md); `vith.thai` re-exports the
public names, so the import surface stays stable.
"""

import ctypes
import re
import threading
from functools import lru_cache
from typing import Any, Callable, Pattern

from prompt_toolkit.document import Document

from .thai_classify import (
    is_thai,
    is_thai_base,
    is_thai_following_vowel,
    is_thai_leading_vowel,
    is_thai_or_lao,
    is_thai_right_attaching,
)  # noqa: F401 (classification reuse)
from .thai_clusters import next_cell_offset, prev_cell_offset, snap_to_cell_start  # noqa: F401

# Shared by the Document/KeyProcessor patch wrappers in `vith/thai.py` and
# the ASCII fast-path delegates here (defined in this leaf module so both
# can import it without a cycle, docs/bugs/075.md).
_ORIGINAL_DOCUMENT_METHODS: dict[str, Callable[..., Any]] = {}
_ORIGINAL_KEY_PROCESSOR_METHODS: dict[str, Callable[..., Any]] = {}

# ---------------------------------------------------------------------------
# 3. Two-Tier Syllable & Word Segmentation (libthai + TCC-lite heuristic)
# ---------------------------------------------------------------------------

_LIBTHAI_PATHS: tuple[str, ...] = (
    "/opt/homebrew/lib/libthai.dylib",
    "/usr/local/lib/libthai.dylib",
    "/usr/lib/libthai.dylib",
    "/usr/local/lib64/libthai.so.0",
    "/usr/local/lib64/libthai.so",
    "/usr/lib64/libthai.so.0",
    "/usr/lib64/libthai.so",
    "/usr/lib/libthai.so.0",
    "/usr/lib/libthai.so",
    "/usr/lib/x86_64-linux-gnu/libthai.so.0",
    "/usr/lib/x86_64-linux-gnu/libthai.so",
    "/usr/lib/aarch64-linux-gnu/libthai.so.0",
    "/usr/lib/aarch64-linux-gnu/libthai.so",
    "libthai.so.0",
    "libthai.so",
    "libthai.dylib",
)


class _LibThaiWrapper:
    """
    Thread-safe wrapper around `libthai`'s wide-character word breaker
    (`th_brk_new`, `th_brk_wc_find_breaks`, `th_brk_delete`), ported from
    `szn/src/thai.zig:693-868`.
    """

    def __init__(self, lib: ctypes.CDLL, brk: int) -> None:
        self._lib = lib
        self._brk = ctypes.c_void_p(brk)
        self._call_lock = threading.Lock()

    def find_breaks(self, text: str) -> list[int]:
        n = len(text)
        if n <= 1:
            return []
        codepoints = (ctypes.c_uint32 * (n + 1))(*(ord(ch) for ch in text), 0)
        pos_buf = (ctypes.c_int * n)()
        with self._call_lock:
            num_breaks = int(self._lib.th_brk_wc_find_breaks(self._brk, codepoints, pos_buf, n))
        if num_breaks <= 0:
            return []

        result: list[int] = []
        for i in range(num_breaks):
            raw = int(pos_buf[i])
            if 0 < raw < n:
                snapped = snap_to_cell_start(text, raw)
                if 0 < snapped < n and (not result or result[-1] != snapped):
                    result.append(snapped)
        return result


_libthai_lock = threading.Lock()
_libthai_initialized = False
_libthai_instance: _LibThaiWrapper | None = None


def _get_libthai() -> _LibThaiWrapper | None:
    """Lazily initialize and return the singleton `_LibThaiWrapper`, or `None`."""
    global _libthai_initialized, _libthai_instance
    if _libthai_initialized:
        return _libthai_instance

    with _libthai_lock:
        if _libthai_initialized:
            return _libthai_instance

        for path in _LIBTHAI_PATHS:
            try:
                lib = ctypes.CDLL(path)
                th_brk_new = lib.th_brk_new
                th_brk_new.argtypes = [ctypes.c_char_p]
                th_brk_new.restype = ctypes.c_void_p

                th_brk_delete = lib.th_brk_delete
                th_brk_delete.argtypes = [ctypes.c_void_p]
                th_brk_delete.restype = None

                th_brk_wc_find_breaks = lib.th_brk_wc_find_breaks
                th_brk_wc_find_breaks.argtypes = [
                    ctypes.c_void_p,
                    ctypes.POINTER(ctypes.c_uint32),
                    ctypes.POINTER(ctypes.c_int),
                    ctypes.c_size_t,
                ]
                th_brk_wc_find_breaks.restype = ctypes.c_int

                brk = th_brk_new(None)
                if not brk:
                    continue

                _libthai_instance = _LibThaiWrapper(lib, int(brk))
                break
            except (OSError, AttributeError):
                continue

        _libthai_initialized = True
        return _libthai_instance


def find_thai_cluster_end(line: str, start: int) -> int:
    """
    Codepoint-indexed port of `szn/src/thai.zig:285-326` (`findThaiClusterEnd`).
    Each cell is `[base_cp + combining*]` advanced via `next_cell_offset(line, pos, 1)`:
      `[leading_vowel] + base (+ optional Ro Han รร) + [following_vowel*] + [right_attaching*]`
    """
    n = len(line)
    if start >= n:
        return start
    cp = ord(line[start])
    if not is_thai_or_lao(cp):
        return next_cell_offset(line, start, 1)

    first_cell_end = next_cell_offset(line, start, 1)
    pos = start

    if is_thai_leading_vowel(cp):
        pos = first_cell_end
        if pos >= n:
            return first_cell_end

    base_cp = ord(line[pos])
    if not is_thai_base(base_cp):
        return first_cell_end

    # Check for Thai Ro Han (รร, U+0E23 + U+0E23) following the base consonant.
    after_base = next_cell_offset(line, pos, 1)
    r1_end = next_cell_offset(line, after_base, 1) if after_base < n else n
    if (
        after_base < n
        and r1_end < n
        and ord(line[after_base]) == 0x0E23
        and ord(line[r1_end]) == 0x0E23
    ):
        pos = next_cell_offset(line, r1_end, 1)
    else:
        pos = after_base

    while pos < n and is_thai_following_vowel(ord(line[pos])):
        pos = next_cell_offset(line, pos, 1)

    while pos < n and is_thai_right_attaching(ord(line[pos])):
        pos = next_cell_offset(line, pos, 1)

    return pos


def _cluster_breaks_for_span(text: str, start: int, end: int) -> list[int]:
    """Step through `text[start:end]` using Tier 2 `find_thai_cluster_end`."""
    breaks: list[int] = []
    pos = start
    while pos < end:
        next_pos = find_thai_cluster_end(text, pos)
        if next_pos <= pos or next_pos > end:
            next_pos = min(end, next_cell_offset(text, pos, 1))
        pos = next_pos
        breaks.append(pos)
    return breaks


def find_thai_word_breaks(text: str, use_libthai: bool = True) -> list[int]:
    """
    Return the sorted boundary offsets `[0, ..., len(text)]` segmenting `text`
    into Thai dictionary words (Tier 1: `libthai`) or orthographic syllable
    clusters (Tier 2 fallback: `find_thai_cluster_end`).
    """
    n = len(text)
    if n == 0:
        return [0]

    libthai = _get_libthai() if use_libthai else None
    if libthai is not None:
        all_thai = True
        has_thai_base = False
        for ch in text:
            cp = ord(ch)
            if not is_thai(cp):
                all_thai = False
                break
            if not has_thai_base and is_thai_base(cp):
                has_thai_base = True
        if all_thai and has_thai_base:
            internal = libthai.find_breaks(text)
            return [0, *internal, n]

    return [0, *_cluster_breaks_for_span(text, 0, n)]


_WORD_CHUNK_RE = re.compile(r"([\u0e00-\u0eff]+|[a-zA-Z0-9_]+|[^a-zA-Z0-9_\s\u0e00-\u0eff]+)")
_BIG_WORD_CHUNK_RE = re.compile(r"([\u0e00-\u0eff]+|[^\s\u0e00-\u0eff]+)")


def _word_spans_for_line(line: str, WORD: bool = False) -> tuple[tuple[int, int], ...]:
    """
    Partition a single line into ``(start, end)`` word spans, splitting Thai
    and Lao runs via `find_thai_word_breaks`.

    Cached per line (keyed on the line text and ``WORD``): word-level Vi
    motions and text objects re-scan only the lines they actually touch
    instead of the whole document on every keypress (see ``docs/bugs/001.md``).
    """
    regex = _BIG_WORD_CHUNK_RE if WORD else _WORD_CHUNK_RE
    spans: list[tuple[int, int]] = []
    for match in regex.finditer(line):
        m_start = match.start(0)
        m_end = match.end(0)
        chunk = match.group(0)
        if chunk and is_thai_or_lao(ord(chunk[0])):
            breaks = find_thai_word_breaks(chunk)
            for i in range(len(breaks) - 1):
                b0 = m_start + breaks[i]
                b1 = m_start + breaks[i + 1]
                if b0 < b1:
                    spans.append((b0, b1))
        else:
            spans.append((m_start, m_end))
    return tuple(spans)


# The span cache itself: word motions are pure functions of the (unchanged)
# line, so an LRU cache gives O(1) re-scans for repeat presses on the same
# text while using only line-sized keys.
_word_spans_for_line = lru_cache(maxsize=2048)(_word_spans_for_line)


def _iter_word_spans(text: str, WORD: bool = False) -> list[tuple[int, int]]:
    """
    Full-document word spans as absolute ``(start, end)`` offsets.

    Composed from the cached per-line spans and kept as the reference
    implementation (used by the test suite to pin equivalence of the
    line-local motion walkers against the whole-text scan).
    """
    spans: list[tuple[int, int]] = []
    offset = 0
    for line in text.split("\n"):
        for start, end in _word_spans_for_line(line, WORD):
            spans.append((offset + start, offset + end))
        offset += len(line) + 1
    return spans


def _iter_word_span_rows(doc: Document, WORD: bool, direction: int):
    """
    Shared cursor-line-then-row walker for the word-motion family.

    Yields ``(row, line_start, start, end)`` per word span, walking the
    cursor line first and then adjacent lines in `direction` (+1 forward,
    -1 backward). Spans within each row follow Vi order (natural when
    walking forward, reversed when walking backward). The single shared
    generator replaces four hand-rolled loops that differed only by
    direction and predicate (docs/bugs/073.md).

    Only *cursor-line* rows need predicates; for any other row the spans
    are unconditionally before/after the cursor, so callers can predicate
    uniformly without changing counting semantics.
    """
    row = doc.cursor_position_row
    lines = doc.lines
    if direction > 0:
        rows: range = range(row, len(lines))
        reverse = False
    else:
        rows = range(row, -1, -1)
        reverse = True
    for r in rows:
        line_start = doc.translate_row_col_to_index(r, 0)
        spans: list[tuple[int, int]] = list(_word_spans_for_line(lines[r], WORD))
        if reverse:
            spans.reverse()
        for start, end in spans:
            yield r, line_start, start, end


def find_next_word_beginning(doc: Document, count: int = 1, WORD: bool = False) -> int | None:
    """
    Thai/Lao-aware replacement for `Document.find_next_word_beginning`.
    Returns an offset relative to `doc.cursor_position`, or `None`.
    """
    if count < 0:
        return find_previous_word_beginning(doc, count=-count, WORD=WORD)
    if doc.text.isascii():
        orig = _ORIGINAL_DOCUMENT_METHODS.get("find_next_word_beginning")
        if orig is not None:
            return orig(doc, count=count, WORD=WORD)

    pos = doc.cursor_position
    matched = 0
    # Only spans starting strictly after the cursor qualify; on the cursor
    # line this is the Vi rule, on later rows it holds automatically.
    for _row, line_start, start, _end in _iter_word_span_rows(doc, WORD, 1):
        if line_start + start > pos:
            matched += 1
            if matched == count:
                return line_start + start - pos
    return None


def find_next_word_ending(
    doc: Document,
    include_current_position: bool = False,
    count: int = 1,
    WORD: bool = False,
) -> int | None:
    """
    Thai/Lao-aware replacement for `Document.find_next_word_ending`.
    Returns an exclusive end offset relative to `doc.cursor_position`, or `None`.
    """
    if count < 0:
        return find_previous_word_ending(doc, count=-count, WORD=WORD)
    if doc.text.isascii():
        orig = _ORIGINAL_DOCUMENT_METHODS.get("find_next_word_ending")
        if orig is not None:
            return orig(
                doc,
                include_current_position=include_current_position,
                count=count,
                WORD=WORD,
            )

    pos = doc.cursor_position
    matched = 0
    # A span qualifies when its last cell cluster starts after the cursor
    # (the Vi `e` text object then subtracts one to rest on that cluster);
    # on later rows this holds automatically.
    for row, line_start, _start, end in _iter_word_span_rows(doc, WORD, 1):
        line = doc.lines[row]
        last_cell = line_start + prev_cell_offset(line, end, 1)
        if include_current_position:
            qualifies = line_start + end > pos
        else:
            qualifies = last_cell > pos
        if qualifies:
            matched += 1
            if matched == count:
                return line_start + end - pos
    return None


def find_previous_word_beginning(doc: Document, count: int = 1, WORD: bool = False) -> int | None:
    """
    Thai/Lao-aware replacement for `Document.find_previous_word_beginning`.
    Returns an offset relative to `doc.cursor_position`, or `None`.
    """
    if count < 0:
        return find_next_word_beginning(doc, count=-count, WORD=WORD)
    if doc.text.isascii():
        orig = _ORIGINAL_DOCUMENT_METHODS.get("find_previous_word_beginning")
        if orig is not None:
            return orig(doc, count=count, WORD=WORD)

    pos = doc.cursor_position
    matched = 0
    # Only spans starting strictly before the cursor qualify (on earlier
    # rows this holds automatically); walk the cursor line reversed first,
    # then backward one row at a time.
    for _row, line_start, start, _end in _iter_word_span_rows(doc, WORD, -1):
        if line_start + start < pos:
            matched += 1
            if matched == count:
                return line_start + start - pos
    return None


def find_start_of_previous_word(
    doc: Document,
    count: int = 1,
    WORD: bool = False,
    pattern: Pattern[str] | None = None,
) -> int | None:
    """
    Thai/Lao-aware replacement for `Document.find_start_of_previous_word`
    (used by Vi `b`/`B` and `get_word_before_cursor`).
    """
    if pattern is not None or doc.text.isascii():
        orig = _ORIGINAL_DOCUMENT_METHODS.get("find_start_of_previous_word")
        if orig is not None:
            return orig(doc, count=count, WORD=WORD, pattern=pattern)
    return find_previous_word_beginning(doc, count=count, WORD=WORD)


def find_previous_word_ending(doc: Document, count: int = 1, WORD: bool = False) -> int | None:
    """
    Thai/Lao-aware replacement for `Document.find_previous_word_ending`
    (used by Vi `ge`/`gE`).
    """
    if count < 0:
        # Delegate symmetrically like upstream prompt_toolkit
        # (document.py:637-638). No Vi handler can generate a negative count
        # here — `ge`/`gE` pass a positive digit arg — so this is
        # upstream-parity dead code kept for API parity (docs/bugs/071.md).
        return find_next_word_ending(doc, count=-count, WORD=WORD)
    if doc.text.isascii():
        orig = _ORIGINAL_DOCUMENT_METHODS.get("find_previous_word_ending")
        if orig is not None:
            return orig(doc, count=count, WORD=WORD)

    pos = doc.cursor_position
    matched = 0
    # A span qualifies when its end is at or before the cursor (on earlier
    # rows this holds automatically).
    for _row, line_start, _start, end in _iter_word_span_rows(doc, WORD, -1):
        if line_start + end <= pos:
            matched += 1
            if matched == count:
                return line_start + end - pos
    return None


def find_boundaries_of_current_word(
    doc: Document,
    WORD: bool = False,
    include_leading_whitespace: bool = False,
    include_trailing_whitespace: bool = False,
) -> tuple[int, int]:
    """
    Thai/Lao-aware replacement for `Document.find_boundaries_of_current_word`
    (used by `iw`, `aw`, `iW`, `aW`, `*`, `#`).
    """
    line = doc.current_line
    if line.isascii():
        orig = _ORIGINAL_DOCUMENT_METHODS.get("find_boundaries_of_current_word")
        if orig is not None:
            return orig(
                doc,
                WORD=WORD,
                include_leading_whitespace=include_leading_whitespace,
                include_trailing_whitespace=include_trailing_whitespace,
            )

    col = doc.cursor_position_col
    n = len(line)
    if col >= n or line[col].isspace():
        if not include_leading_whitespace and not include_trailing_whitespace:
            return (0, 0)
        w_start = col
        w_end = col
        if include_leading_whitespace:
            while w_start > 0 and line[w_start - 1].isspace():
                w_start -= 1
        if include_trailing_whitespace:
            while w_end < n and line[w_end].isspace():
                w_end += 1
        return (w_start - col, w_end - col)

    for w_start, w_end in _word_spans_for_line(line, WORD):
        if w_start <= col < w_end:
            if include_leading_whitespace:
                while w_start > 0 and line[w_start - 1].isspace():
                    w_start -= 1
            if include_trailing_whitespace:
                while w_end < n and line[w_end].isspace():
                    w_end += 1
            return (w_start - col, w_end - col)
    return (0, 0)
