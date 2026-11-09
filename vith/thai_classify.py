"""
Thai & Lao character classification predicates and codepoint tables.

Split out of `vith/thai.py` (docs/bugs/075.md); `vith.thai` re-exports the
public names, so the import surface stays stable.
"""

import unicodedata

from prompt_toolkit.utils import get_cwidth

# ---------------------------------------------------------------------------
# 1. Thai & Lao Character Classification Predicates
# ---------------------------------------------------------------------------

# Complete 34-codepoint set of Thai (16) and Lao (18) GC=Mn combining marks,
# matching `szn/src/thai.zig:28-70` (`isThaiCombining`) 1-for-1.
# Note: SARA AM (U+0E33 / U+0EB3) is GC=Lo (spacing following vowel, width 1), NOT Mn.
_THAI_COMBINING_CPS: frozenset[int] = frozenset(
    {
        # Thai combining marks (16 marks: U+0E31, U+0E34..U+0E3A, U+0E47..U+0E4E)
        0x0E31,  # MAI HAN-AKAT
        0x0E34,
        0x0E35,
        0x0E36,
        0x0E37,  # SARA I, II, UE, UEE
        0x0E38,
        0x0E39,
        0x0E3A,  # SARA U, UU, PHINTHU
        0x0E47,  # MAITAIKHU
        0x0E48,
        0x0E49,
        0x0E4A,
        0x0E4B,  # MAI EK, THO, TRI, CHATTAWA
        0x0E4C,
        0x0E4D,
        0x0E4E,  # THANTHAKHAT, NIKHAHIT, YAMAKKAN
        # Lao combining marks (18 marks: U+0EB1, U+0EB4..U+0EBC, U+0EC7..U+0ECE)
        0x0EB1,  # MAI KAN
        0x0EB4,
        0x0EB5,
        0x0EB6,
        0x0EB7,  # SARA I, II, Y, YY
        0x0EB8,
        0x0EB9,
        0x0EBA,
        0x0EBB,
        0x0EBC,  # SARA U, UU, PALI VIRAMA, MAI KON, LO
        0x0EC7,  # MAITAIKHU
        0x0EC8,
        0x0EC9,
        0x0ECA,
        0x0ECB,  # MAI EK, THO, TI, CATAWA
        0x0ECC,
        0x0ECD,
        0x0ECE,  # CANCELLATION MARK, NIKHAHIT, YAMAKKAN
    }
)

_FOLLOWING_VOWEL_CPS: frozenset[int] = frozenset(
    {
        0x0E30,
        0x0E31,
        0x0E32,
        0x0E33,
        0x0E45,  # Thai: ะ, ◌ั, า, ำ, ๅ
        0x0EB0,
        0x0EB1,
        0x0EB2,
        0x0EB3,  # Lao:  ະ, ◌ັ, າ, ຳ
    }
)

_RIGHT_ATTACHING_CPS: frozenset[int] = frozenset(
    {
        0x0E2F,
        0x0E46,  # Thai: ฯ (PAIYANNOI), ๆ (MAI YAMOK)
        0x0EAF,
        0x0EC6,  # Lao:  ຯ (ELLIPSIS),  ໆ (KO LA)
    }
)


def is_thai(cp: int) -> bool:
    """True if `cp` is in the Thai block (`U+0E00..U+0E7F`)."""
    return 0x0E00 <= cp <= 0x0E7F


def is_lao(cp: int) -> bool:
    """True if `cp` is in the Lao block (`U+0E80..U+0EFF`)."""
    return 0x0E80 <= cp <= 0x0EFF


def is_thai_or_lao(cp: int) -> bool:
    """True if `cp` is in the Thai (`U+0E00..U+0E7F`) or Lao (`U+0E80..U+0EFF`) blocks."""
    return 0x0E00 <= cp <= 0x0EFF


def is_thai_combining(cp: int) -> bool:
    """True if `cp` is one of the 34 Thai/Lao zero-width combining marks (`GC=Mn`)."""
    return cp in _THAI_COMBINING_CPS


def is_combining_char(ch: str) -> bool:
    """
    Fast-path check for Thai/Lao combining marks (`_THAI_COMBINING_CPS`),
    with general Unicode `Mn` / `get_cwidth(ch) == 0` fallback for other scripts.

    `ch` must be ONE codepoint (callers pass `line[idx]` etc.); a
    multi-codepoint string is classified by its first codepoint only and the
    rest is silently ignored (docs/bugs/071.md).

    NOTE: characters below U+0300 always classify as non-combining — in
    particular `\n`. The cell-cluster scanners (`snap_to_cell_start`,
    `prev_cell_offset`) rely on newline never behaving like a combining
    mark so that backward walks over multi-line `Document.text` stop at
    line boundaries; keep that ordering when generalizing this predicate
    (see tests/test_thai.py::test_prev_cell_offset_never_crosses_newline
    and docs/bugs/014.md of the findings bundle).
    """
    if not ch:
        return False
    cp = ord(ch[0])
    if 0x0E00 <= cp <= 0x0EFF:
        return cp in _THAI_COMBINING_CPS
    if cp < 0x0300:
        return False
    return unicodedata.category(ch[0]) == "Mn" or get_cwidth(ch[0]) == 0


def is_sara_am(cp: int) -> bool:
    """True if `cp` is Thai SARA AM (`U+0E33`) or Lao SARA AM (`U+0EB3`).

    SARA-AM over-printing remains the SARA-AM renderer's logic
    (`_cell_has_sara_am`) — this predicate has zero in-app callers today
    (tests + re-export only, akin to the 074 test-helper row)."""
    return cp == 0x0E33 or cp == 0x0EB3


def is_thai_leading_vowel(cp: int) -> bool:
    """True if `cp` is a visual-order leading vowel (`U+0E40..U+0E44`, `U+0EC0..U+0EC4`)."""
    return (0x0E40 <= cp <= 0x0E44) or (0x0EC0 <= cp <= 0x0EC4)


def is_thai_following_vowel(cp: int) -> bool:
    """True if `cp` is a Thai/Lao following vowel (`ะ`, `◌ั`, `า`, `ำ`, `ๅ` / `ະ`, `◌ັ`, `າ`, `ຳ`)."""
    return cp in _FOLLOWING_VOWEL_CPS


def is_thai_right_attaching(cp: int) -> bool:
    """True if `cp` is a right-attaching symbol (`ฯ` `U+0E2F`, `ๆ` `U+0E46`, `ຯ` `U+0EAF`, `ໆ` `U+0EC6`)."""
    return cp in _RIGHT_ATTACHING_CPS


_THAI_DIGIT_CPS = set(range(0x0E50, 0x0E5A)) | set(range(0x0ED0, 0x0EDA))
_THAI_SYMBOL_CPS = {0x0E3F, 0x0E4F, 0x0E5A, 0x0E5B}


def is_thai_base(cp: int) -> bool:
    """
    True if `cp` is a width-1 Thai/Lao codepoint that is not a combining mark,
    leading vowel, following vowel, right-attaching mark, digit, or symbol
    (adapted from `szn/src/thai.zig:126-133` `isThaiBase` with digits/symbols excluded;
    docs/bugs/054.md).

    The result is folded into a precomputed table at import (the predicate
    used to evaluate `get_cwidth(chr(cp))` per call from the word-motion
    helpers; docs/bugs/072.md).
    """
    return cp in _THAI_BASE_CPS


def _build_thai_base_table() -> frozenset[int]:
    """Precompute `is_thai_base` over the Thai/Lao blocks."""
    return frozenset(
        cp
        for cp in range(0x0E00, 0x0EFF + 1)
        if not is_thai_combining(cp)
        and not is_thai_leading_vowel(cp)
        and not is_thai_following_vowel(cp)
        and not is_thai_right_attaching(cp)
        and cp not in _THAI_DIGIT_CPS
        and cp not in _THAI_SYMBOL_CPS
        and get_cwidth(chr(cp)) == 1
    )


_THAI_BASE_CPS = _build_thai_base_table()
