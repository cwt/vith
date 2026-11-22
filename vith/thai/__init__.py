"""
Thai (`U+0E00..U+0E7F`) and Lao (`U+0E80..U+0EFF`) script support for `vith`.

Adapts the cell-cluster and differential renderer architecture from `szn`
(`src/thai.zig`, `src/mode_copy.zig`, `src/server/render.zig`):

1. Character-wise Vi motions and edits (`h`, `l`, `j`, `k`, `x`, `X`, `s`,
   `r`, `~`, `$`, `a`, `<Esc>`) step by terminal cell clusters (`base + comb*`)
   so the cursor never parks on a zero-width combining mark (`GC=Mn`) and edits
   never slice a cluster in half.
2. Word-wise Vi motions and text objects (`w`, `b`, `e`, `W`, `B`, `E`, `iw`,
   `aw`, `*`, `#`) segment unspaced Thai/Lao text using a two-tier strategy:
   Tier 1 (`libthai` dictionary breaker via `ctypes`, optional at runtime) with
   Tier 2 (`find_thai_cluster_end` orthographic syllable cluster heuristic) as
   an always-available fallback.
3. Differential terminal rendering (`prompt_toolkit.renderer._output_screen_diff`)
   cleans up stray zero-width combining marks and invalidates the neighbor cell
   whenever Thai/Lao `SARA AM` (`U+0E33` / `U+0EB3`) or its preceding consonant
   at `x - 1` changes.
"""

from __future__ import annotations

from typing import Any

from . import classify, clusters, patches, words
from .classify import (
    is_combining_char,
    is_lao,
    is_sara_am,
    is_thai,
    is_thai_base,
    is_thai_combining,
    is_thai_following_vowel,
    is_thai_leading_vowel,
    is_thai_or_lao,
    is_thai_right_attaching,
)
from .clusters import (
    cell_starts,
    cell_stops,
    current_line_and_col,
    next_cell_offset,
    prev_cell_offset,
    snap_to_cell_start,
)
from .patches import (
    install_prompt_toolkit_thai_patches,
    is_thai_gate_latched,
    note_document_text,
    reset_thai_text_gate,
    reset_thai_text_gate_if_all_buffers_ascii,
)
from .words import (
    find_boundaries_of_current_word,
    find_next_word_beginning,
    find_next_word_ending,
    find_previous_word_beginning,
    find_previous_word_ending,
    find_start_of_previous_word,
    find_thai_cluster_end,
    find_thai_word_breaks,
)

__all__ = (
    # Submodules
    "classify",
    "clusters",
    "patches",
    "words",
    # Character classification predicates & tables
    "is_thai",
    "is_lao",
    "is_thai_or_lao",
    "is_thai_combining",
    "is_combining_char",
    "is_sara_am",
    "is_thai_leading_vowel",
    "is_thai_following_vowel",
    "is_thai_right_attaching",
    "is_thai_base",
    # Cell-cluster boundary scanners
    "snap_to_cell_start",
    "next_cell_offset",
    "prev_cell_offset",
    "cell_starts",
    "cell_stops",
    "current_line_and_col",
    # Syllable & word segmentation / motions
    "find_thai_cluster_end",
    "find_thai_word_breaks",
    "find_next_word_beginning",
    "find_next_word_ending",
    "find_previous_word_beginning",
    "find_previous_word_ending",
    "find_start_of_previous_word",
    "find_boundaries_of_current_word",
    # prompt_toolkit patches & renderer integration
    "install_prompt_toolkit_thai_patches",
    "is_thai_gate_latched",
    "note_document_text",
    "reset_thai_text_gate",
    "reset_thai_text_gate_if_all_buffers_ascii",
)

# Compatibility lookup for internal/test helpers that lived on the flat
# `vith/thai.py` module before the package split (docs/bugs/075.md, 153.md).
# Resolved lazily via PEP 562 `__getattr__` and hidden from `dir(vith.thai)`.
_COMPAT_INTERNAL_SUBMODULES: dict[str, Any] = {
    "_THAI_COMBINING_CPS": classify,
    "_SARA_AM_POISON": patches,
    "_THAI_GATE": patches,
    "_install_renderer_patch": patches,
    "_install_window_copy_body_patch": patches,
    "_PtkTextObject": patches,
    "_PtkZeroWidthEscape": patches,
    "_sanitize_and_invalidate_sara_am": patches,
    "_thai_install_selftest": patches,
    "ptk_renderer": patches,
    "_ORIGINAL_DOCUMENT_METHODS": words,
    "_ORIGINAL_KEY_PROCESSOR_METHODS": words,
    "_iter_word_spans": words,
    "_word_spans_for_line": words,
}


def __getattr__(name: str) -> Any:
    submodule = _COMPAT_INTERNAL_SUBMODULES.get(name)
    if submodule is not None:
        return getattr(submodule, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(__all__)
