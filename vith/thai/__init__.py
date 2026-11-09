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

from . import classify, clusters, patches, words
from .classify import (  # noqa: F401
    _THAI_COMBINING_CPS,
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
from .clusters import (  # noqa: F401
    cell_starts,
    cell_stops,
    next_cell_offset,
    prev_cell_offset,
    snap_to_cell_start,
)
from .patches import (  # noqa: F401
    _SARA_AM_POISON,
    _THAI_GATE,
    _cell_ordinal,
    _install_renderer_patch,
    _install_window_copy_body_patch,
    _PtkTextObject,
    _PtkZeroWidthEscape,
    _sanitize_and_invalidate_sara_am,
    _start_of_cell_ordinal,
    _thai_install_selftest,
    _ThaiTextGate,
    install_prompt_toolkit_thai_patches,
    note_document_text,
    ptk_renderer,
    reset_thai_text_gate,
    reset_thai_text_gate_if_all_buffers_ascii,
)
from .words import (  # noqa: F401
    _ORIGINAL_DOCUMENT_METHODS,
    _ORIGINAL_KEY_PROCESSOR_METHODS,
    _iter_word_spans,
    _word_spans_for_line,
    find_boundaries_of_current_word,
    find_next_word_beginning,
    find_next_word_ending,
    find_previous_word_beginning,
    find_previous_word_ending,
    find_start_of_previous_word,
    find_thai_cluster_end,
    find_thai_word_breaks,
)

# Backward-compatibility aliases for former flat module names
thai_classify = classify
thai_clusters = clusters
thai_words = words
thai_patches = patches

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
    "note_document_text",
    "reset_thai_text_gate",
    "reset_thai_text_gate_if_all_buffers_ascii",
)
