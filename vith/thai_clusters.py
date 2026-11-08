"""
O(1) local cell-cluster boundary scanners for Thai/Lao text.

Split out of `vith/thai.py` (docs/bugs/075.md); `vith.thai` re-exports the
public names, so the import surface stays stable.
"""

from vith.thai_classify import is_combining_char

# ---------------------------------------------------------------------------
# 2. O(1) Local Cell-Cluster Boundary Scanners
# ---------------------------------------------------------------------------


def snap_to_cell_start(line: str, col: int) -> int:
    """
    If `col` is parked on a combining mark inside `line` (e.g. after vertical
    `j`/`k` motion), walk backward to the base character of the enclosing cell
    cluster.
    """
    n = len(line)
    if col <= 0 or n == 0 or line.isascii():
        return max(0, min(col, n))
    idx = min(col, n)
    while idx > 0 and idx < n and is_combining_char(line[idx]):
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


def cell_starts(line: str) -> list[int]:
    """
    Return valid Normal-mode cell start offsets in `line`
    (e.g. `"ที่ดิน"` -> `[0, 3, 5]`).

    Exported for the test suite and benchmarks only (docs/bugs/074.md);
    the editor itself walks via `snap_to_cell_start`/`next_cell_offset`.
    """
    if not line:
        return []
    return cell_stops(line)[:-1]
