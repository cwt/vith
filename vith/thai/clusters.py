"""
O(1) local cell-cluster boundary scanners for Thai/Lao text.

Split out of `vith/thai.py` (docs/bugs/075.md); `vith.thai` re-exports the
public names, so the import surface stays stable.
"""

from bisect import bisect_right
from functools import lru_cache

from .classify import is_combining_char

# ---------------------------------------------------------------------------
# 2. O(1) Local Cell-Cluster Boundary Scanners
# ---------------------------------------------------------------------------


def snap_to_cell_start(line: str, col: int) -> int:
    """
    If `col` is parked on a combining mark inside `line` (e.g. after vertical
    `j`/`k` motion), walk backward to the base character of the enclosing cell
    cluster. `col == len(line)` is the EOL insert position and is preserved as
    `len(line)` (use `prev_cell_offset(line, len(line), 1)` to step onto the
    last cell cluster).
    """
    n = len(line)
    if col <= 0 or col >= n or line.isascii():
        return max(0, min(col, n))
    idx = col
    while idx > 0 and is_combining_char(line[idx]):
        idx -= 1
    return idx


def next_cell_offset(line: str, col: int, count: int = 1) -> int:
    """
    Advance `count` visual cell clusters forward from `col` within `line`.
    Returns the new column index in `0..len(line)`.
    """
    n = len(line)
    if col >= n or count <= 0:
        return n if col >= n else max(0, col)
    if line.isascii():
        return min(n, col + count)

    idx = max(0, col)
    for _ in range(count):
        if idx >= n:
            break
        idx += 1
        while idx < n and is_combining_char(line[idx]):
            idx += 1
    return idx


def prev_cell_offset(line: str, col: int, count: int = 1) -> int:
    """
    Move `count` visual cell clusters backward from `col` within `line`.
    Returns the new column index in `0..len(line)`.
    """
    if col <= 0 or count <= 0:
        return 0 if col <= 0 else min(col, len(line))
    if line.isascii():
        return max(0, col - count)

    idx = min(col, len(line))
    for _ in range(count):
        if idx <= 0:
            break
        idx -= 1
        while idx > 0 and is_combining_char(line[idx]):
            idx -= 1
    return idx


def cell_stops(line: str) -> list[int]:
    """
    Return all cell-cluster boundary offsets in `line`, including `len(line)`
    at the end (e.g. `"ที่ดิน"` -> `[0, 3, 5, 6]`).
    """
    stops = [0]
    col = 0
    n = len(line)
    while col < n:
        col = next_cell_offset(line, col, 1)
        stops.append(col)
    return stops


_CELL_STARTS_CACHE_MAX_LINE = 65536


@lru_cache(maxsize=4096)
def _cell_starts_cached(line: str) -> list[int]:
    if not line:
        return []
    return cell_stops(line)[:-1]


def cell_starts(line: str) -> list[int]:
    """
    Return valid Normal-mode cell start offsets in `line`
    (e.g. `"ที่ดิน"` -> `[0, 3, 5]`).

    The per-line result is cached: `_cell_ordinal`/`_start_of_cell_ordinal`
    (077/079) and `-- vertical curswant` (078) consult this on every
    `j`/`k`/block command — uncached, a 2000-codepoint Thai row paid two
    O(line) scans per keypress (docs/bugs/090.md). Lines longer than
    `_CELL_STARTS_CACHE_MAX_LINE` bypass the LRU cache so megabyte single-line
    documents cannot pin gigabytes in memory (docs/bugs/114.md). TREAT THE
    RETURNED LIST AS READ-ONLY (it is shared).
    """
    if not line:
        return []
    if len(line) > _CELL_STARTS_CACHE_MAX_LINE:
        return cell_stops(line)[:-1]
    return _cell_starts_cached(line)


cell_starts.cache_clear = _cell_starts_cached.cache_clear  # type: ignore[attr-defined]
cell_starts.cache_info = _cell_starts_cached.cache_info  # type: ignore[attr-defined]


def _cell_ordinal(line: str, col: int) -> int:
    """
    Visual cell-cluster ordinal that contains codepoint column `col`
    (combining marks overprint their base, so every Thai/Lao cluster is
    exactly one terminal cell: its ordinal == its visual column).

    Empty and ASCII lines shortcut (columns == ordinals); `col` may sit
    mid-cluster — the ordinal of the cluster's base is returned
    (docs/bugs/077.md).
    """
    if not line or line.isascii():
        return col
    starts = cell_starts(line)
    if not starts:
        return 0
    if col >= len(line):
        # A codepoint parked at/past the line end is the empty slot *after*
        # the last cell (Vim curswant semantics), not the last cell itself.
        return len(starts)
    return max(0, bisect_right(starts, col) - 1)


def _start_of_cell_ordinal(line: str, ordinal: int) -> int:
    """
    Line-relative codepoint column of the start of visual cell `ordinal`
    (`len(line)` when the row has fewer cells). ASCII shortcut keeps the
    codepoint/visual identity.
    """
    n = len(line)
    if not line or line.isascii():
        return min(ordinal, n)
    starts = cell_starts(line)
    return starts[ordinal] if ordinal < len(starts) else n


def _map_column_across_lines(src_line: str, src_col: int, target_line: str) -> int:
    """
    Map codepoint column `src_col` on `src_line` to the start column of the
    same visual cell-cluster ordinal on `target_line`.
    """
    ordinal = _cell_ordinal(src_line, min(src_col, len(src_line)))
    return _start_of_cell_ordinal(target_line, ordinal)
