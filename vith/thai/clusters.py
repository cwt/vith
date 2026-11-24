"""
O(1) local cell-cluster boundary scanners for Thai/Lao text.

Split out of `vith/thai.py` (docs/bugs/075.md); `vith.thai` re-exports the
public names, so the import surface stays stable.
"""

from bisect import bisect_right
from functools import lru_cache
from typing import Any

from prompt_toolkit.utils import get_cwidth

try:
    from wcwidth.table_vs16 import VS16_NARROW_TO_WIDE as _WCWIDTH_VS16_NARROW_TO_WIDE
except Exception:  # pragma: no cover - wcwidth always present in supported envs
    _WCWIDTH_VS16_NARROW_TO_WIDE = {}

from .classify import is_combining_char

#: Base codepoints that `wcwidth` widens from 1 to 2 cells when followed by
#: U+FE0F (VS16).  Consulted only when the opt-in
#: ``variation_selector_always_wide`` policy is enabled.
_VS16_NARROW_BASES: frozenset[int] = frozenset(
    cp for (_lo, _hi) in _WCWIDTH_VS16_NARROW_TO_WIDE.get('9.0.0', ()) for cp in range(_lo, _hi + 1)
)

# Opt-in policy flag(s).  Several spellings are accepted so that direct
# callers, tests, and the `Editor` attribute can all toggle the behavior.
variation_selector_always_wide = False
VARIATION_SELECTOR_ALWAYS_WIDE = False


def _vsaw_enabled() -> bool:
    """Return True when the VS16-always-wide policy is active."""
    # Prefer the active Application's Editor so two Editor instances can hold
    # independent policies (docs/bugs/223.md).
    try:
        from prompt_toolkit.application.current import get_app_or_none

        app = get_app_or_none()
        editor = getattr(app, '_vith_editor', None) if app is not None else None
        if editor is not None:
            return bool(getattr(editor, '_variation_selector_always_wide', False))
    except Exception:
        pass
    if globals().get('variation_selector_always_wide') or globals().get(
        'VARIATION_SELECTOR_ALWAYS_WIDE'
    ):
        return True
    try:
        import vith.thai as _thai_mod

        if getattr(_thai_mod, 'variation_selector_always_wide', False) or getattr(
            _thai_mod, 'VARIATION_SELECTOR_ALWAYS_WIDE', False
        ):
            return True
    except Exception:
        pass
    return False


def current_line_and_col(doc: Any) -> tuple[str, int]:
    """
    Return `(line, col)` at `doc.cursor_position` in O(line length) time via
    `str.rfind`/`str.find`, avoiding `prompt_toolkit`'s `Document.current_line`
    (which copies 100% of `doc.text`) and `cursor_position_col` (which builds
    `_line_start_indexes` across the entire document; docs/bugs/157.md).
    """
    text: str = doc.text
    pos: int = doc.cursor_position
    line_start = text.rfind("\n", 0, pos) + 1
    line_end = text.find("\n", pos)
    if line_end == -1:
        line_end = len(text)
    return text[line_start:line_end], pos - line_start


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
    n = len(line)
    if line.isascii():
        return list(range(n + 1))
    stops = [0]
    col = 0
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


DEFAULT_TABSTOP = 8


def _compute_cell_columns(
    line: str, tabstop: int = DEFAULT_TABSTOP, _vsaw: bool | None = None
) -> tuple[tuple[int, ...], tuple[int, ...], int]:
    """
    Build `(cluster starts, visual columns, total width)` for `line`.

    A cluster's visual width is the sum of `get_cwidth` over its codepoints:
    Thai/Lao combining marks are zero-width, CJK/full-width/emoji bases occupy
    two cells (docs/bugs/141.md), and `\\t` advances to the next multiple of
    `tabstop` (matching `TabsProcessor` in `vith/thai/patches.py`).

    Computed in a single pass with an ASCII+tabs fast path and no per-cluster
    string slices or generator `sum()` allocations (docs/bugs/165.md).
    """
    if _vsaw is None:
        _vsaw = _vsaw_enabled()
    ts = max(1, int(tabstop))
    n = len(line)
    if n == 0:
        return (), (), 0

    if line.isascii():
        starts = tuple(range(n))
        columns: list[int] = [0] * n
        visual = 0
        for i, ch in enumerate(line):
            columns[i] = visual
            if ch == "\t":
                visual += ts - (visual % ts)
            elif " " <= ch <= "~":
                visual += 1
            else:
                visual += get_cwidth(ch)
        return starts, tuple(columns), visual

    starts_list: list[int] = []
    columns_list: list[int] = []
    append_start = starts_list.append
    append_col = columns_list.append
    visual = 0
    idx = 0
    while idx < n:
        append_start(idx)
        append_col(visual)
        ch = line[idx]
        idx += 1
        if ch == "\t":
            visual += ts - (visual % ts)
            while idx < n and is_combining_char(line[idx]):
                idx += 1
        else:
            w = 1 if " " <= ch <= "~" else get_cwidth(ch)
            _has_vs16 = False
            while idx < n and is_combining_char(line[idx]):
                if line[idx] == "\ufe0f":
                    _has_vs16 = True
                w += get_cwidth(line[idx])
                idx += 1
            # Opt-in: terminals that widen VS16-promoted narrow bases render
            # the base + U+FE0F pair as 2 cells (szn variation-selector-always-wide).
            if _vsaw and _has_vs16 and ord(ch) in _VS16_NARROW_BASES and w < 2:
                w += 1
            visual += w
    return tuple(starts_list), tuple(columns_list), visual


@lru_cache(maxsize=4096)
def _cell_columns_cached(
    line: str, tabstop: int = DEFAULT_TABSTOP, _vsaw: bool = False
) -> tuple[tuple[int, ...], tuple[int, ...], int]:
    return _compute_cell_columns(line, tabstop, _vsaw)


def _cell_columns(
    line: str, tabstop: int = DEFAULT_TABSTOP
) -> tuple[tuple[int, ...], tuple[int, ...], int]:
    _vsaw = _vsaw_enabled()
    if len(line) > _CELL_STARTS_CACHE_MAX_LINE:
        return _compute_cell_columns(line, tabstop, _vsaw)
    return _cell_columns_cached(line, tabstop, _vsaw)


_cell_columns.cache_clear = _cell_columns_cached.cache_clear  # type: ignore[attr-defined]
_cell_columns.cache_info = _cell_columns_cached.cache_info  # type: ignore[attr-defined]


def _line_visual_width(line: str, tabstop: int = DEFAULT_TABSTOP) -> int:
    """Total terminal display width of `line`, expanding tabs to `tabstop`."""
    if not line:
        return 0
    if line.isascii() and "\t" not in line:
        return len(line)
    _starts, _columns, total_width = _cell_columns(line, tabstop)
    return total_width


def _cell_ordinal(line: str, col: int, tabstop: int = DEFAULT_TABSTOP) -> int:
    """
    Visual *column* containing codepoint column `col` (docs/bugs/141.md).

    Thai/Lao combining marks are zero-width, wide (CJK/emoji) clusters consume
    two columns, and `\\t` expands to the next multiple of `tabstop`, so
    `j`/`k` and visual blocks stay in the same display column across mixed
    lines. `col` may sit mid-cluster — the containing cell's visual column is
    returned.
    """
    if not line or (line.isascii() and "\t" not in line):
        return col
    starts, columns, total_width = _cell_columns(line, tabstop)
    if not starts:
        return 0
    if col >= len(line):
        # A codepoint parked at/past the line end is the empty slot *after*
        # the last cell (Vim curswant semantics), not the last cell itself.
        return total_width
    index = bisect_right(starts, col) - 1
    return columns[max(0, index)]


def _start_of_cell_ordinal(line: str, ordinal: int, tabstop: int = DEFAULT_TABSTOP) -> int:
    """
    Line-relative codepoint column of the start of visual column `ordinal`
    (`len(line)` when the row ends before `ordinal`; docs/bugs/141.md).
    """
    n = len(line)
    if not line or (line.isascii() and "\t" not in line):
        return min(ordinal, n)
    starts, columns, total_width = _cell_columns(line, tabstop)
    if ordinal >= total_width:
        return n
    index = bisect_right(columns, ordinal) - 1
    return starts[max(0, index)]


def _map_column_across_lines(
    src_line: str,
    src_col: int,
    target_line: str,
    tabstop: int = DEFAULT_TABSTOP,
) -> int:
    """
    Map codepoint column `src_col` on `src_line` to the start column of the
    same visual display column on `target_line`.
    """
    ordinal = _cell_ordinal(src_line, min(src_col, len(src_line)), tabstop=tabstop)
    return _start_of_cell_ordinal(target_line, ordinal, tabstop=tabstop)
