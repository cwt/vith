"""
Unit tests for Thai (`U+0E00..U+0E7F`) and Lao (`U+0E80..U+0EFF`) script support
(`vith/thai.py`, `vith/key_bindings.py`, `vith/editor.py`).
"""

from __future__ import annotations

import prompt_toolkit.renderer as ptk_renderer
import pytest
from prompt_toolkit.application.current import set_app
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.data_structures import Point, Size
from prompt_toolkit.document import Document
from prompt_toolkit.input import DummyInput
from prompt_toolkit.key_binding.key_processor import KeyPress
from prompt_toolkit.key_binding.vi_state import InputMode
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout.containers import Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.mouse_handlers import MouseHandlers
from prompt_toolkit.layout.screen import Char, Screen, WritePosition
from prompt_toolkit.output import ColorDepth, DummyOutput
from prompt_toolkit.styles import DummyStyle, DummyStyleTransformation

import vith.thai as thai
from vith.editor import Editor
from vith.thai import (
    _SARA_AM_POISON,
    _THAI_COMBINING_CPS,
    _sanitize_and_invalidate_sara_am,
    cell_starts,
    cell_stops,
    find_thai_cluster_end,
    find_thai_word_breaks,
    install_prompt_toolkit_thai_patches,
    is_combining_char,
    is_lao,
    is_sara_am,
    is_thai,
    is_thai_base,
    is_thai_combining,
    is_thai_following_vowel,
    is_thai_or_lao,
    next_cell_offset,
    prev_cell_offset,
    snap_to_cell_start,
)
from vith.thai import words as thai_words


def _feed_keys(editor: Editor, *keys: str | Keys) -> None:
    """Feed a sequence of keys through `editor.application.key_processor`."""
    kp = editor.application.key_processor
    with set_app(editor.application):
        for k in keys:
            if isinstance(k, Keys):
                kp.feed(KeyPress(k))
            else:
                kp.feed(KeyPress(k))
        kp.process_keys()


def _active_buffer(editor: Editor) -> Buffer:
    eb = editor.current_editor_buffer
    assert eb is not None
    return eb.buffer


def _make_editor_with_text(text: str, cursor_position: int = 0) -> Editor:
    editor = Editor(output=DummyOutput(), input=DummyInput())
    editor.load_initial_files([None])
    editor.sync_with_prompt_toolkit()
    editor.application.vi_state.input_mode = InputMode.NAVIGATION
    buff = _active_buffer(editor)
    buff.document = Document(text, cursor_position=cursor_position)
    return editor


def test_all_34_thai_lao_combining_marks() -> None:
    expected_thai = {
        0x0E31,
        0x0E34,
        0x0E35,
        0x0E36,
        0x0E37,
        0x0E38,
        0x0E39,
        0x0E3A,
        0x0E47,
        0x0E48,
        0x0E49,
        0x0E4A,
        0x0E4B,
        0x0E4C,
        0x0E4D,
        0x0E4E,
    }
    expected_lao = {
        0x0EB1,
        0x0EB4,
        0x0EB5,
        0x0EB6,
        0x0EB7,
        0x0EB8,
        0x0EB9,
        0x0EBA,
        0x0EBB,
        0x0EBC,
        0x0EC7,
        0x0EC8,
        0x0EC9,
        0x0ECA,
        0x0ECB,
        0x0ECC,
        0x0ECD,
        0x0ECE,
    }
    assert len(expected_thai) == 16
    assert len(expected_lao) == 18
    assert _THAI_COMBINING_CPS == (expected_thai | expected_lao)
    assert len(_THAI_COMBINING_CPS) == 34

    for cp in _THAI_COMBINING_CPS:
        assert is_thai_combining(cp), f"Expected U+{cp:04X} to be combining"
        assert is_combining_char(chr(cp)), f"Expected chr(0x{cp:04X}) to be combining"

    # Explicit check for Lao PALI VIRAMA, MAITAIKHU, and YAMAKKAN:
    for cp in (0x0EBA, 0x0EC7, 0x0ECE):
        assert is_thai_combining(cp)
        assert is_combining_char(chr(cp))

    # Latin combining acute accent fallback:
    assert is_combining_char("\u0301") is True
    assert is_combining_char("a") is False
    assert is_combining_char("") is False

    # SARA AM (U+0E33 / U+0EB3) and FONGMAN (U+0E4F) are spacing characters, NOT Mn:
    assert is_thai_combining(0x0E33) is False
    assert is_thai_combining(0x0EB3) is False
    assert is_thai_combining(0x0E4F) is False
    assert is_combining_char("\u0e33") is False
    assert is_combining_char("\u0eb3") is False

    # Block range checks:
    assert is_thai(0x0E01) is True
    assert is_lao(0x0E81) is True
    assert is_thai_or_lao(0x0E01) is True
    assert is_thai_or_lao(0x0E81) is True
    assert is_thai_or_lao(0x0041) is False


def test_cell_offsets_thi_din_and_lao() -> None:
    for word in ("ที่ดิน", "ທີ່ດິນ"):
        assert len(word) == 6
        assert cell_starts(word) == [0, 3, 5]
        assert cell_stops(word) == [0, 3, 5, 6]

        # Forward steps:
        assert next_cell_offset(word, 0, 1) == 3
        assert next_cell_offset(word, 3, 1) == 5
        assert next_cell_offset(word, 5, 1) == 6
        assert next_cell_offset(word, 0, 2) == 5
        assert next_cell_offset(word, 0, 3) == 6

        # Backward steps from cell boundaries:
        assert prev_cell_offset(word, 6, 1) == 5
        assert prev_cell_offset(word, 5, 1) == 3
        assert prev_cell_offset(word, 3, 1) == 0

    # Mid-cluster col=4 (◌ิ inside ดิ at [3:5]) steps back to 3 (ด), NOT 0:
    assert prev_cell_offset("ที่ดิน", 4, 1) == 3
    assert snap_to_cell_start("ที่ดิน", 1) == 0
    assert snap_to_cell_start("ที่ดิน", 2) == 0
    assert snap_to_cell_start("ที่ดิน", 4) == 3


def test_cell_offsets_phu_yai() -> None:
    word = "ผู้ใหญ่"
    assert len(word) == 7
    assert cell_starts(word) == [0, 3, 4, 5]
    assert cell_stops(word) == [0, 3, 4, 5, 7]

    # Reverse traversal from len(word) back to 0 matches cell_starts in reverse:
    rev: list[int] = []
    col = len(word)
    while col > 0:
        col = prev_cell_offset(word, col, 1)
        rev.append(col)
    assert rev[::-1] == [0, 3, 4, 5]


def test_cell_vs_syllable_ko_ro_han_mai_yamok() -> None:
    # "เกาะ": 4 visual cells [0, 1, 2, 3, 4], 1 orthographic syllable [0, 4]
    assert cell_stops("เกาะ") == [0, 1, 2, 3, 4]
    assert find_thai_cluster_end("เกาะ", 0) == 4
    assert find_thai_word_breaks("เกาะ", use_libthai=False) == [0, 4]
    assert find_thai_word_breaks("เกาะ", use_libthai=True) == [0, 4]

    # "ธรรม": 4 visual cells [0, 1, 2, 3, 4], Tier 2 syllables "ธรร" [0, 3] + "ม" [3, 4]
    assert cell_stops("ธรรม") == [0, 1, 2, 3, 4]
    assert find_thai_cluster_end("ธรรม", 0) == 3
    assert find_thai_cluster_end("ธรรม", 3) == 4
    assert find_thai_word_breaks("ธรรม", use_libthai=False) == [0, 3, 4]

    # "ดีๆ": 2 visual cells [0, 2, 3], 1 syllable cluster [0, 3] (trailing ๆ attaches to ดี)
    assert cell_stops("ดีๆ") == [0, 2, 3]
    assert find_thai_cluster_end("ดีๆ", 0) == 3
    assert find_thai_word_breaks("ดีๆ", use_libthai=False) == [0, 3]
    assert find_thai_word_breaks("ดีๆ", use_libthai=True) == [0, 3]

    # "ๆฯ": standalone right-attaching symbols at cluster start step 1 cell at a time
    assert cell_stops("ๆฯ") == [0, 1, 2]
    assert find_thai_cluster_end("ๆฯ", 0) == 1
    assert find_thai_cluster_end("ๆฯ", 1) == 2
    assert find_thai_word_breaks("ๆฯ", use_libthai=False) == [0, 1, 2]
    assert find_thai_word_breaks("ๆฯ", use_libthai=True) == [0, 1, 2]


def test_sara_am_cell_and_syllable() -> None:
    assert is_sara_am(0x0E33) is True
    assert is_sara_am(0x0EB3) is True
    assert is_sara_am(0x0E32) is False
    assert is_thai_combining(0x0E33) is False
    assert is_thai_combining(0x0EB3) is False
    assert is_thai_following_vowel(0x0E33) is True
    assert is_thai_following_vowel(0x0EB3) is True
    assert is_thai_base(0x0E33) is False
    assert is_thai_base(0x0EB3) is False

    # "น้ำ" (น + ◌้ + ำ, len=3): 2 cells ("น้" [0:2], "ำ" [2:3]), 1 syllable [0, 3]
    assert cell_stops("น้ำ") == [0, 2, 3]
    assert find_thai_cluster_end("น้ำ", 0) == 3
    assert find_thai_word_breaks("น้ำ", use_libthai=False) == [0, 3]
    assert find_thai_word_breaks("น้ำ", use_libthai=True) == [0, 3]

    # "กำ" (ก + ำ, len=2): 2 cells ("ก" [0:1], "ำ" [1:2]), 1 syllable [0, 2]
    assert cell_stops("กำ") == [0, 1, 2]
    assert find_thai_cluster_end("กำ", 0) == 2
    assert find_thai_word_breaks("กำ", use_libthai=False) == [0, 2]
    assert find_thai_word_breaks("กำ", use_libthai=True) == [0, 2]

    # Lao "ນ້ຳ" (len=3) and "ກຳ" (len=2):
    assert cell_stops("ນ້ຳ") == [0, 2, 3]
    assert find_thai_cluster_end("ນ້ຳ", 0) == 3
    assert cell_stops("ກຳ") == [0, 1, 2]
    assert find_thai_cluster_end("ກຳ", 0) == 2


class _RecordingOutput(DummyOutput):
    def __init__(self) -> None:
        super().__init__()
        self.written: list[str] = []

    def write(self, data: str) -> None:
        self.written.append(data)


@pytest.fixture
def sticky_gate(monkeypatch):
    """Force the sticky Thai-text renderer gate off for a deterministic test."""
    saved = thai._THAI_GATE.value
    monkeypatch.setattr(thai._THAI_GATE, "value", False)
    yield thai._THAI_GATE
    thai.reset_thai_text_gate()
    thai._THAI_GATE.value = saved


def test_sara_am_renderer_neighbor_refresh() -> None:
    install_prompt_toolkit_thai_patches()
    editor = _make_editor_with_text("")
    # The wrapper only sanitizes once the session reported Thai-capable text;
    # this test renders กำ/ກຳ frames, so latch the sticky gate (after the
    # editor is created: its sync pass may decay an earlier latch once every
    # buffer is plain ASCII, docs/bugs/072.md).
    thai.note_document_text("\u0e33")
    app = editor.application
    size = Size(rows=24, columns=80)
    attrs_cache = ptk_renderer._StyleStringToAttrsCache(
        DummyStyle().get_attrs_for_style_str, DummyStyleTransformation()
    )
    has_style_cache = ptk_renderer._StyleStringHasStyleCache(attrs_cache)

    for base_ch, other_base_ch, sara_am_ch in (
        ("ก", "ข", "\u0e33"),  # Thai กำ -> ขำ
        ("ກ", "ຂ", "\u0eb3"),  # Lao  ກຳ -> ຂຳ
    ):
        # Frame 2a: Delete SARA AM at x=1 -> neighbor at x=0 must be poisoned and re-emitted
        prev_screen_a = Screen()
        prev_screen_a.height = 1
        prev_screen_a.data_buffer[0][0] = Char(base_ch, "")
        prev_screen_a.data_buffer[0][1] = Char(sara_am_ch, "")

        new_screen_a = Screen()
        new_screen_a.height = 1
        new_screen_a.data_buffer[0][0] = Char(base_ch, "")
        new_screen_a.data_buffer[0][1] = Char(" ", "")

        out_a = _RecordingOutput()
        ptk_renderer._output_screen_diff(
            app,
            out_a,
            new_screen_a,
            Point(0, 0),
            ColorDepth.DEPTH_8_BIT,
            prev_screen_a,
            None,
            False,
            full_screen=True,
            attrs_for_style_string=attrs_cache,
            style_string_has_style=has_style_cache,
            size=size,
            previous_width=size.columns,
        )
        assert prev_screen_a.data_buffer[0][0] == _SARA_AM_POISON
        assert base_ch in "".join(out_a.written)

        # Frame 2b: Modify preceding consonant at x=0 -> SARA AM at x=1 must be poisoned and re-emitted
        prev_screen_b = Screen()
        prev_screen_b.height = 1
        prev_screen_b.data_buffer[0][0] = Char(base_ch, "")
        prev_screen_b.data_buffer[0][1] = Char(sara_am_ch, "")

        new_screen_b = Screen()
        new_screen_b.height = 1
        new_screen_b.data_buffer[0][0] = Char(other_base_ch, "")
        new_screen_b.data_buffer[0][1] = Char(sara_am_ch, "")

        out_b = _RecordingOutput()
        ptk_renderer._output_screen_diff(
            app,
            out_b,
            new_screen_b,
            Point(0, 0),
            ColorDepth.DEPTH_8_BIT,
            prev_screen_b,
            None,
            False,
            full_screen=True,
            attrs_for_style_string=attrs_cache,
            style_string_has_style=has_style_cache,
            size=size,
            previous_width=size.columns,
        )
        assert prev_screen_b.data_buffer[0][1] == _SARA_AM_POISON
        written_b = "".join(out_b.written)
        assert other_base_ch in written_b
        assert sara_am_ch in written_b


def test_document_jk_vertical_snap() -> None:
    install_prompt_toolkit_thai_patches()
    # Row 0: "abcdef" (cursor at col=2 'c')
    # Row 1: "ที่ดิน" (codepoint index 2 is '่' combining mark inside Cell 0 "ที่")
    doc = Document("abcdef\nที่ดิน", cursor_position=2)
    assert doc.cursor_position_row == 0
    assert doc.cursor_position_col == 2

    down_delta = doc.get_cursor_down_position(1)
    doc_down = Document(doc.text, cursor_position=doc.cursor_position + down_delta)
    assert doc_down.cursor_position_row == 1
    # 078: j/k keep the VISUAL column (cell-cluster ordinal): row 0's 'c' is
    # visual column 2, row 1's visual column 2 is 'น' (never a mark).
    assert doc_down.cursor_position_col == 5
    assert doc_down.current_char == "น"

    # Also test upward motion from row 1 col=2 of "abcdef" onto row 0 "ที่ดิน":
    doc2 = Document("ที่ดิน\nabcdef", cursor_position=len("ที่ดิน\n") + 2)
    assert doc2.cursor_position_row == 1
    assert doc2.cursor_position_col == 2
    up_delta = doc2.get_cursor_up_position(1)
    doc_up = Document(doc2.text, cursor_position=doc2.cursor_position + up_delta)
    assert doc_up.cursor_position_row == 0
    # 078: visual column 2 of row 1 ('c' at x 2) maps to น on the Thai row:
    assert doc_up.cursor_position_col == 5
    assert doc_up.current_char == "น"


def test_vi_x_X_s_r_tilde_dollar_via_key_processor() -> None:
    # 1. 'x' at col=0 on "ที่ดิน" deletes entire cell cluster "ที่" -> "ดิน"
    editor = _make_editor_with_text("ที่ดิน", cursor_position=0)
    buff = _active_buffer(editor)
    _feed_keys(editor, "x")
    assert buff.text == "ดิน"
    assert buff.cursor_position == 0
    assert editor.application.clipboard.get_data().text == "ที่"

    # 2. 'X' at col=3 on "ที่ดิน" deletes cell cluster "ที่" before cursor -> "ดิน"
    editor = _make_editor_with_text("ที่ดิน", cursor_position=3)
    buff = _active_buffer(editor)
    _feed_keys(editor, "X")
    assert buff.text == "ดิน"
    assert buff.cursor_position == 0
    assert editor.application.clipboard.get_data().text == "ที่"

    # 3. 's' at col=0 on "ที่ดิน" deletes "ที่" and switches to INSERT mode
    editor = _make_editor_with_text("ที่ดิน", cursor_position=0)
    buff = _active_buffer(editor)
    _feed_keys(editor, "s")
    assert buff.text == "ดิน"
    assert editor.application.vi_state.input_mode == InputMode.INSERT
    assert editor.application.clipboard.get_data().text == "ที่"

    # 4. 'r' + 'ข' at col=0 on "ที่ดิน" replaces "ที่" with "ข" -> "ขดิน"
    editor = _make_editor_with_text("ที่ดิน", cursor_position=0)
    buff = _active_buffer(editor)
    _feed_keys(editor, "r", "ข")
    assert buff.text == "ขดิน"
    assert buff.cursor_position == 0
    assert editor.application.vi_state.input_mode == InputMode.NAVIGATION

    # 5. '~' at col=0 on "ที่ดิน" advances 1 cell cluster (0 -> 3), never parking on ◌ี
    editor = _make_editor_with_text("ที่ดิน", cursor_position=0)
    buff = _active_buffer(editor)
    _feed_keys(editor, "~")
    assert buff.text == "ที่ดิน"
    assert buff.cursor_position == 3

    # 6. '$' lands on start of last cell cluster (col=5 on "ที่ดิน", col=5 on "ผู้ใหญ่", col=0 on "ที่")
    editor = _make_editor_with_text("ที่ดิน", cursor_position=0)
    buff = _active_buffer(editor)
    _feed_keys(editor, "$")
    assert buff.cursor_position == 5

    editor = _make_editor_with_text("ผู้ใหญ่", cursor_position=0)
    buff = _active_buffer(editor)
    _feed_keys(editor, "$")
    assert buff.cursor_position == 5

    editor = _make_editor_with_text("ที่", cursor_position=0)
    buff = _active_buffer(editor)
    _feed_keys(editor, "$")
    assert buff.cursor_position == 0

    # Operator-pending 'd$' from col=0 on "ผู้ใหญ่" still deletes all 7 codepoints including ◌่:
    editor = _make_editor_with_text("ผู้ใหญ่", cursor_position=0)
    buff = _active_buffer(editor)
    _feed_keys(editor, "d", "$")
    assert buff.text == ""


def test_formatted_text_stray_combining_cleanup() -> None:
    ctrl = FormattedTextControl([("", "ที่")])
    win = Window(content=ctrl)
    screen = Screen()
    win.write_to_screen(
        screen,
        MouseHandlers(),
        WritePosition(xpos=0, ypos=0, width=20, height=1),
        "",
        erase_bg=False,
        z_index=None,
    )
    # Before cleanup, Window._copy_body leaves a width-0 combining mark in column 1:
    assert screen.data_buffer[0][0].char == "ที่"
    assert screen.data_buffer[0][1].width == 0

    _sanitize_and_invalidate_sara_am(screen, None)
    assert screen.data_buffer[0][0].char == "ที่"
    assert screen.data_buffer[0][1].char == " "
    assert screen.data_buffer[0][1].width == 1


def test_vi_hl_wbe_text_objects_on_thai() -> None:
    # 'l' and 'h' step by cell clusters across "ที่ดิน" (stops: 0 -> 3 -> 5):
    editor = _make_editor_with_text("ที่ดิน", cursor_position=0)
    buff = _active_buffer(editor)
    _feed_keys(editor, "l")
    assert buff.cursor_position == 3
    _feed_keys(editor, "l")
    assert buff.cursor_position == 5
    _feed_keys(editor, "h")
    assert buff.cursor_position == 3
    _feed_keys(editor, "h")
    assert buff.cursor_position == 0

    # 'w', 'b', 'e', 'diw' on "เกาะ น้ำ":
    editor = _make_editor_with_text("เกาะ น้ำ", cursor_position=0)
    buff = _active_buffer(editor)
    _feed_keys(editor, "w")
    assert buff.cursor_position == 5  # Start of "น้ำ"
    _feed_keys(editor, "b")
    assert buff.cursor_position == 0  # Back to start of "เกาะ"
    _feed_keys(editor, "e")
    assert buff.cursor_position == 3  # Last cell 'ะ' of "เกาะ"
    _feed_keys(editor, "d", "i", "w")
    assert buff.text == " น้ำ"


def test_vi_append_escape_and_operators_on_thai() -> None:
    # 'a' at col=0 on "ที่ดิน" moves past "ที่" (to col=3) and enters INSERT mode;
    # '<Esc>' steps back 1 cell cluster to col=0 (never landing on ◌่ at col=2):
    editor = _make_editor_with_text("ที่ดิน", cursor_position=0)
    buff = _active_buffer(editor)
    _feed_keys(editor, "a")
    assert editor.application.vi_state.input_mode == InputMode.INSERT
    assert buff.cursor_position == 3
    _feed_keys(editor, Keys.Escape)
    assert editor.application.vi_state.input_mode == InputMode.NAVIGATION
    assert buff.cursor_position == 0

    # 'dl' at col=0 on "ที่ดิน" deletes 1 cell cluster ("ที่"):
    editor = _make_editor_with_text("ที่ดิน", cursor_position=0)
    buff = _active_buffer(editor)
    _feed_keys(editor, "d", "l")
    assert buff.text == "ดิน"
    assert buff.cursor_position == 0

    # 'dh' at col=3 on "ที่ดิน" deletes 1 cell cluster before cursor ("ที่"):
    editor = _make_editor_with_text("ที่ดิน", cursor_position=3)
    buff = _active_buffer(editor)
    _feed_keys(editor, "d", "h")
    assert buff.text == "ดิน"
    assert buff.cursor_position == 0

    # 'e' vs 'de' on "ผู้ใหญ่ บ้าน" (where "ผู้ใหญ่" ends with combining mark ◌่ at idx 6):
    editor = _make_editor_with_text("ผู้ใหญ่ บ้าน", cursor_position=0)
    buff = _active_buffer(editor)
    _feed_keys(editor, "e")
    assert buff.cursor_position == 5  # Base consonant 'ญ' of last cell "ญ่"

    editor = _make_editor_with_text("ผู้ใหญ่ บ้าน", cursor_position=0)
    buff = _active_buffer(editor)
    _feed_keys(editor, "d", "e")
    assert buff.text == " บ้าน"

    # '*' and '#' word search on Thai words:
    editor = _make_editor_with_text("เกาะ น้ำ เกาะ", cursor_position=0)
    buff = _active_buffer(editor)
    assert buff.document.get_word_under_cursor() == "เกาะ"
    _feed_keys(editor, "*")
    assert buff.cursor_position == 9
    _feed_keys(editor, "#")
    assert buff.cursor_position == 0


def test_two_tier_word_breaks_fallback(monkeypatch) -> None:
    # Tier 2 fallback (when libthai is unavailable or disabled):
    monkeypatch.setattr(thai_words, "_get_libthai", lambda: None)
    assert find_thai_word_breaks("ที่ดิน") == [0, 3, 5, 6]
    assert find_thai_word_breaks("ผู้ใหญ่") == [0, 3, 5, 7]
    assert find_thai_word_breaks("ທີ່ດິນ") == [0, 3, 5, 6]
    doc = Document("ที่ดิน", cursor_position=0)
    assert doc.find_next_word_beginning(1) == 3
    assert doc.find_next_word_beginning(2) == 5


def test_version_gate_graceful_fallback(monkeypatch) -> None:
    # Simulate an incompatible Document method signature and verify graceful fallback:
    def _bad_sig(self):  # Missing `count` parameter
        return 0

    orig_left = Document.get_cursor_left_position
    monkeypatch.setattr(Document, "get_cursor_left_position", _bad_sig)
    assert thai.install_prompt_toolkit_thai_patches() is False
    assert Document.get_cursor_left_position is _bad_sig

    # Restore and verify re-installation succeeds:
    monkeypatch.setattr(Document, "get_cursor_left_position", orig_left)
    assert thai.install_prompt_toolkit_thai_patches() is True
    assert Document("ที่ดิน", cursor_position=0).get_cursor_right_position(1) == 3


def test_version_gate_rollback_covers_key_processor_patches(monkeypatch) -> None:
    """Failure during the self-test must also undo the KeyProcessor patches."""
    from prompt_toolkit.key_binding.key_processor import KeyProcessor

    # Simulate a pre-installation prompt_toolkit: restore the original
    # Document and KeyProcessor methods recorded at import time.
    for method_name, original in thai._ORIGINAL_DOCUMENT_METHODS.items():
        monkeypatch.setattr(Document, method_name, original)
    for method_name, original in thai._ORIGINAL_KEY_PROCESSOR_METHODS.items():
        if original is not None:
            monkeypatch.setattr(KeyProcessor, method_name, original)

    assert getattr(KeyProcessor._fix_vi_cursor_position, "_vith_thai_patched", False) is False, (
        "precondition: KeyProcessor patches uninstalled"
    )

    # Corrupt the primitive used by the patched Document.right method so the
    # startup self-test fails *after* all patches are applied:
    def _broken_next_cell_offset(line: str, col: int, count: int = 1) -> int:
        return col + 99

    monkeypatch.setattr(thai.patches, "next_cell_offset", _broken_next_cell_offset)
    assert thai.install_prompt_toolkit_thai_patches() is False

    # Both KeyProcessor patches must be rolled back (docs/bugs/005.md):
    assert getattr(KeyProcessor._fix_vi_cursor_position, "_vith_thai_patched", False) is False
    assert getattr(KeyProcessor._start_timeout, "_vith_thai_patched", False) is False
    # And the Document methods are the unpatched prompt_toolkit originals
    # (cursor 0 -> first codepoint on "ที่ดิน", no cell-cluster snapping):
    assert Document("ที่ดิน", cursor_position=0).get_cursor_right_position(1) == 1

    # monkeypatch.undo() restores the pre-test (patched) state:
    monkeypatch.undo()
    assert Document("ที่ดิน", cursor_position=0).get_cursor_right_position(1) == 3
    assert getattr(KeyProcessor._fix_vi_cursor_position, "_vith_thai_patched", False) is True


def _build_sara_am_frames(base: str, sara_am: str) -> tuple[Screen, Screen]:
    """A previous frame with `base ำ` and a new frame where the ำ is deleted."""
    prev_screen = Screen()
    prev_screen.height = 1
    prev_screen.data_buffer[0][0] = Char(base, "")
    prev_screen.data_buffer[0][1] = Char(sara_am, "")

    new_screen = Screen()
    new_screen.height = 1
    new_screen.data_buffer[0][0] = Char(base, "")
    new_screen.data_buffer[0][1] = Char(" ", "")
    return prev_screen, new_screen


def test_renderer_gate_skips_ascii_frames(sticky_gate) -> None:
    """With no Thai seen yet, the wrapper must not mutate/pre-scan screens."""
    install_prompt_toolkit_thai_patches()
    editor = _make_editor_with_text("")
    app = editor.application
    size = Size(rows=24, columns=80)
    attrs_cache = ptk_renderer._StyleStringToAttrsCache(
        DummyStyle().get_attrs_for_style_str, DummyStyleTransformation()
    )
    has_style_cache = ptk_renderer._StyleStringHasStyleCache(attrs_cache)

    prev_screen, new_screen = _build_sara_am_frames("ก", "\u0e33")
    out = _RecordingOutput()
    ptk_renderer._output_screen_diff(
        app,
        out,
        new_screen,
        Point(0, 0),
        ColorDepth.DEPTH_8_BIT,
        prev_screen,
        None,
        False,
        full_screen=True,
        attrs_for_style_string=attrs_cache,
        style_string_has_style=has_style_cache,
        size=size,
        previous_width=size.columns,
    )
    assert sticky_gate.value is False
    # No poisoning happened: the previous screen stayed untouched.
    assert prev_screen.data_buffer[0][0].char == "ก"
    assert prev_screen.data_buffer[0][1].char == "\u0e33"


def test_buffer_text_latches_gate_and_sanitizer_resumes(sticky_gate) -> None:
    """Typing Thai into the buffer must latch the gate (O(1) for ASCII)."""
    install_prompt_toolkit_thai_patches()
    editor = _make_editor_with_text("")
    app = editor.application
    size = Size(rows=24, columns=80)
    attrs_cache = ptk_renderer._StyleStringToAttrsCache(
        DummyStyle().get_attrs_for_style_str, DummyStyleTransformation()
    )
    has_style_cache = ptk_renderer._StyleStringHasStyleCache(attrs_cache)

    buff = _active_buffer(editor)
    buff.text = "กำ"
    assert sticky_gate.value is True, "Thai buffer text must latch the gate"

    prev_screen, new_screen = _build_sara_am_frames("ก", "\u0e33")
    out = _RecordingOutput()
    ptk_renderer._output_screen_diff(
        app,
        out,
        new_screen,
        Point(0, 0),
        ColorDepth.DEPTH_8_BIT,
        prev_screen,
        None,
        False,
        full_screen=True,
        attrs_for_style_string=attrs_cache,
        style_string_has_style=has_style_cache,
        size=size,
        previous_width=size.columns,
    )
    # Once latched, the neighbor refresh is back: ก must be re-emitted.
    assert prev_screen.data_buffer[0][0] == _SARA_AM_POISON
    assert "ก" in "".join(out.written)


def test_sticky_gate_note_document_text(sticky_gate) -> None:
    # ASCII / empty strings are free and never latch.
    thai.note_document_text("plain ascii text")
    thai.note_document_text("")
    thai.note_document_text(None)
    thai.note_document_text("accented é but no Thai")
    assert sticky_gate.value is False
    # Thai text latches; Lao text too.
    thai.note_document_text("กำ")
    assert sticky_gate.value is True
    sticky_gate.value = False
    thai.note_document_text("ຳ")
    assert sticky_gate.value is True


def test_prev_cell_offset_never_crosses_newline(monkeypatch) -> None:
    """Cell-cluster walks over multi-line text must stop at `\n` (docs/bugs/014.md).

    ``is_combining_char('\n')`` is False because the cp<0x300 branch fires
    before the general width-0 fallback; the snap machinery of the `e`/`E`
    bindings depends on that invariant being kept.
    """
    assert thai.is_combining_char("\n") is False
    text = "x\nที่ดิน"  # newline at index 1, Thai line 2..7
    n = len(text)
    # Walking back from the end of the document must stop at the base
    # character of the last cluster (น, index 7) - never onto the newline
    # (1) or the line above (0):
    assert prev_cell_offset(text, n, 1) == 7
    # From every cluster boundary *inside* the Thai line (cols 3..8), the
    # walk stays within that line (>= line start 2). Stepping back from the
    # line start itself (col 2) legitimately lands on the previous line's
    # tail (1). The e/E bindings only snap from span ends, which are
    # strictly inside a line, so they never see the boundary case.
    assert min(prev_cell_offset(text, col, 1) for col in range(3, n + 1)) >= 2
    assert prev_cell_offset(text, 2, 1) == 1

    # Even a leading combining mark on a line cannot drag the walk across \n:
    # " mark preceded by \n " - walk stops just before the newline.
    text_leading_mark = "ab\n\u0e48cd"
    assert prev_cell_offset(text_leading_mark, len(text_leading_mark), 1) >= 3


def test_right_margin_combining_marks_wrapped_and_continuation_rows() -> None:
    """Upper/lower vowels and tone marks at the last column of a Window are
    preserved on both the initial row and soft-wrapped continuation rows
    with a line prefix (e.g. ``...``), without scanning off-screen lines."""
    from prompt_toolkit.application import Application
    from prompt_toolkit.application.current import set_app
    from prompt_toolkit.buffer import Buffer
    from prompt_toolkit.layout import Layout
    from prompt_toolkit.layout.containers import Window
    from prompt_toolkit.layout.controls import BufferControl
    from prompt_toolkit.layout.mouse_handlers import MouseHandlers
    from prompt_toolkit.layout.screen import Screen, WritePosition

    # Width = 10:
    # Row 0: cols 0..7 = "12345678", col 8 = "กี้", col 9 (last col) = "ที่"
    # Row 1: cols 0..2 = "..." (prefix), cols 3..8 = "abcdef", col 9 (last col) = "กู้"
    # Row 2: cols 0..2 = "..." (prefix), cols 3..5 = "xyz"
    text = "12345678กี้ที่abcdefกู้xyz"
    buf = Buffer(document=Document(text, cursor_position=11))
    buf._load_history_task = True  # type: ignore[assignment]
    ctrl = BufferControl(buffer=buf)
    win = Window(
        content=ctrl,
        wrap_lines=True,
        cursorline=True,
        cursorcolumn=True,
        get_line_prefix=lambda lineno, wrap_count: (
            [("class:line-prefix", "...")] if wrap_count > 0 else []
        ),
    )
    app: Application[object] = Application(layout=Layout(win))
    screen = Screen()
    with set_app(app):
        win._write_to_screen_at_index(screen, MouseHandlers(), WritePosition(0, 0, 10, 5), "", True)

    assert [screen.data_buffer[0][x].char for x in range(10)] == [
        "1",
        "2",
        "3",
        "4",
        "5",
        "6",
        "7",
        "8",
        "กี้",
        "ที่",
    ]
    assert [screen.data_buffer[1][x].char for x in range(10)] == [
        ".",
        ".",
        ".",
        "a",
        "b",
        "c",
        "d",
        "e",
        "f",
        "กู้",
    ]
    # Cursorline and cursorcolumn styles are preserved on the right-edge cluster.
    assert "class:cursor-line" in screen.data_buffer[0][9].style
    assert "class:cursor-column" in screen.data_buffer[0][9].style

    # Reverse mouse-click lookup maps cell (0, 9) to the base character 'ท' (col 11).
    assert win.render_info is not None
    rowcol_to_yx = win.render_info._rowcol_to_yx
    yx_to_rowcol = {v: k for k, v in rowcol_to_yx.items()}
    assert yx_to_rowcol[0, 9] == (0, 11)
    assert rowcol_to_yx[0, 11] == (0, 9)
    assert rowcol_to_yx[0, 12] == (0, 9)
    assert rowcol_to_yx[0, 13] == (0, 9)


def test_right_margin_combining_marks_nowrap_and_visible_only_scan() -> None:
    """Right-margin combining marks work with ``wrap_lines=False``
    (``horizontal_scroll > 0``) and never call ``get_line`` on off-screen
    buffer lines."""
    from prompt_toolkit.application import Application
    from prompt_toolkit.application.current import set_app
    from prompt_toolkit.buffer import Buffer
    from prompt_toolkit.layout import Layout
    from prompt_toolkit.layout.containers import Window
    from prompt_toolkit.layout.controls import BufferControl
    from prompt_toolkit.layout.mouse_handlers import MouseHandlers
    from prompt_toolkit.layout.screen import Screen, WritePosition

    lines = ["0123456789abcที่def"] + [f"line {i} ที่" for i in range(1000)]
    buf = Buffer(document=Document("\n".join(lines), cursor_position=13))
    buf._load_history_task = True  # type: ignore[assignment]
    ctrl = BufferControl(buffer=buf)
    win = Window(content=ctrl, wrap_lines=False)
    app: Application[object] = Application(layout=Layout(win))

    accessed_lines: set[int] = set()
    orig_create_content = ctrl.create_content

    def counting_create_content(width: int, height: int, preview_search: bool = False):
        content = orig_create_content(width, height, preview_search)
        orig_get_line = content.get_line

        def counting_get_line(i: int):
            accessed_lines.add(i)
            return orig_get_line(i)

        content.get_line = counting_get_line  # type: ignore[method-assign]
        return content

    ctrl.create_content = counting_create_content  # type: ignore[method-assign]
    screen = Screen()
    with set_app(app):
        win._write_to_screen_at_index(screen, MouseHandlers(), WritePosition(0, 0, 10, 5), "", True)

    assert win.horizontal_scroll == 4
    assert [screen.data_buffer[0][x].char for x in range(10)] == [
        "4",
        "5",
        "6",
        "7",
        "8",
        "9",
        "a",
        "b",
        "c",
        "ที่",
    ]
    # Only visible rows (0..4) are queried, never the full 1001-line document.
    assert len(accessed_lines) <= 6


def test_output_screen_diff_last_column_autowrap_guard(sticky_gate) -> None:
    """When a cell at the rightmost terminal column (`width - 1`) contains
    combining marks (`len(cell.char) > 1`), `_output_screen_diff` wraps its
    emission in `\\x1b[?7h` ... `\\x1b[?7l\\x1b[{width}G` so VT100 terminals
    and multiplexers (`szn`) attach combining marks to `width - 1` instead
    of `width - 2`."""
    import io

    from prompt_toolkit.application import Application
    from prompt_toolkit.data_structures import Point, Size
    from prompt_toolkit.output.color_depth import ColorDepth
    from prompt_toolkit.output.vt100 import Vt100_Output
    from prompt_toolkit.renderer import (
        _StyleStringHasStyleCache,
        _StyleStringToAttrsCache,
    )
    from prompt_toolkit.styles import DummyStyleTransformation, default_ui_style

    thai.note_document_text("ที่")
    screen = Screen()
    for x, ch in enumerate("123456789"):
        screen.data_buffer[0][x] = Char(char=ch, style="")
    screen.data_buffer[0][9] = Char(char="ที่", style="")
    screen.height = 1

    buf = io.StringIO()
    size = Size(rows=4, columns=10)
    vt_out = Vt100_Output(buf, lambda: size)
    style = default_ui_style()
    attrs_cache = _StyleStringToAttrsCache(
        style.get_attrs_for_style_str, DummyStyleTransformation()
    )
    has_style_cache = _StyleStringHasStyleCache(attrs_cache)
    app: Application[object] = Application()

    ptk_renderer._output_screen_diff(
        app,
        vt_out,
        screen,
        Point(x=0, y=0),
        ColorDepth.DEPTH_8_BIT,
        None,
        None,
        False,
        full_screen=True,
        attrs_for_style_string=attrs_cache,
        style_string_has_style=has_style_cache,
        size=size,
        previous_width=0,
    )
    vt_out.flush()
    rendered = buf.getvalue()
    assert "\x1b[?7hที่\x1b[?7l\x1b[10G" in rendered
    assert 9 not in screen.zero_width_escapes[0]


def test_reentrant_output_screen_diff_restores_escapes(sticky_gate) -> None:
    """098: re-entrant diff (_vith_write_swap_active=True) must not leak
    _AUTOWRAP_ARM_TAG into screen.zero_width_escapes."""
    from unittest.mock import MagicMock

    import prompt_toolkit.renderer as ptk_renderer
    from prompt_toolkit.data_structures import Point, Size
    from prompt_toolkit.layout.screen import Char, Screen

    screen = Screen()
    screen.data_buffer[0][79] = Char("ก้", "")
    output = MagicMock()
    output.write = MagicMock()
    output.write_raw = MagicMock()
    output._vith_write_swap_active = True

    size = Size(rows=1, columns=80)
    ptk_renderer._output_screen_diff(
        MagicMock(),
        output,
        screen,
        Point(0, 0),
        MagicMock(),
        Screen(),
        None,
        False,
        full_screen=True,
        attrs_for_style_string=MagicMock(),
        style_string_has_style=MagicMock(),
        size=size,
        previous_width=80,
    )
    assert 79 not in screen.zero_width_escapes[0]


def test_rollback_covers_text_object_patch(monkeypatch) -> None:
    """064A: a failure after TextObject.operator_range is installed must roll
    the TextObject patch back together with everything in `applied`."""

    class FakeTextObject:
        def operator_range(self, document):
            return (0, 1)

    fake_orig = FakeTextObject.operator_range

    monkeypatch.setattr(thai.patches, "_PtkTextObject", FakeTextObject)

    def _fail_selftest() -> None:
        raise RuntimeError("simulated self-test failure")

    monkeypatch.setattr(thai.patches, "_thai_install_selftest", _fail_selftest)

    assert thai.install_prompt_toolkit_thai_patches() is False

    # The TextObject patch must be rolled back, not left half-installed:
    assert getattr(FakeTextObject.operator_range, "_vith_thai_patched", False) is False
    assert FakeTextObject.operator_range is fake_orig


def test_window_copy_body_patch_skips_on_signature_drift(monkeypatch) -> None:
    """064B: signature drift in Window._copy_body must skip the patch (fail
    loudly) instead of mis-binding arguments positionally."""
    from prompt_toolkit.layout.containers import Window

    def drifted(self, ui_content, new_screen):  # incomplete signature
        return {}, {}

    monkeypatch.setattr(Window, "_copy_body", drifted)
    assert thai._install_window_copy_body_patch() is False
    # Nothing was installed on drift:
    assert Window._copy_body is drifted


def test_renderer_patch_skips_on_signature_drift(monkeypatch) -> None:
    """064B: signature drift in _output_screen_diff must skip the patch."""

    def drifted(app, output):  # incomplete signature
        return None

    monkeypatch.setattr(thai.ptk_renderer, "_output_screen_diff", drifted)
    assert thai._install_renderer_patch() is False
    assert thai.ptk_renderer._output_screen_diff is drifted


def test_zero_width_escape_style_comes_from_prompt_toolkit_token() -> None:
    """064B: vith imports the shared token instead of hard-coding a magic
    style substring."""
    from prompt_toolkit.token import ZeroWidthEscape

    assert thai._PtkZeroWidthEscape == ZeroWidthEscape == "[ZeroWidthEscape]"


def test_thai_gate_decays_when_all_buffers_ascii(editor):
    # 072 row 7: the sticky latch decays when every open buffer is ASCII
    # again (called from sync_with_prompt_toolkit).
    editor.load_initial_files([None])
    wa = editor.window_arrangement
    eb = wa.active_editor_buffer
    assert eb.buffer.text == ""

    thai.note_document_text("\u0e01\u0e33")
    assert thai._THAI_GATE.value is True
    editor.sync_with_prompt_toolkit()
    assert thai._THAI_GATE.value is False

    # A buffer holding Thai text keeps the session latched:
    thai.note_document_text("\u0e01")
    eb.buffer.text = "\u0e01\u0e33"
    editor.sync_with_prompt_toolkit()
    assert thai._THAI_GATE.value is True


def test_selection_range_at_line_is_line_relative(editor):
    # 076: prompt_toolkit's `Document.selection_range_at_line` returns
    # LINE-RELATIVE columns; the Thai cluster extension used to interpret
    # them as absolute indices — correct only on row 0 (highlight right),
    # crashing `HighlightSelectionProcessor` with a KeyError on any later
    # row (visual highlight + `j`).
    from prompt_toolkit.application.current import set_app
    from prompt_toolkit.selection import SelectionState, SelectionType

    editor.load_initial_files([None])
    text = "ที่ดิน\nปั้\nกำแพง\n"  # row 1: ป + ั + ้ = one cluster
    with set_app(editor.application):
        eb = editor.window_arrangement.active_editor_buffer
        eb.buffer.text = text
        # Cursor on the ั mark of row 1's cluster [ปั้] (absolute index 8;
        # row 0 is 6 codepoints + newline). Document.cursor_position is
        # read-only, so it is set via the constructor.
        doc = Document(
            text, cursor_position=8, selection=SelectionState(0, SelectionType.CHARACTERS)
        )

        orig = thai._ORIGINAL_DOCUMENT_METHODS["selection_range_at_line"](doc, 1)
        assert orig == (0, 2)  # exclusive end: ั included, ้ not

        patched = doc.selection_range_at_line(1)
        assert patched == (0, 3)  # extended over ้, in LINE-relative coords

        # And the line-relative contract holds for every covered row:
        for row in range(len(doc.lines)):
            result = doc.selection_range_at_line(row)
            if result is not None:
                start, end = result
                assert 0 <= start <= end
                assert end <= len(doc.lines[row]) + 1  # vi EOL inclusive +1

        # EOL selection (cursor at the newline after row 1, absolute 10):
        # ptk's own (0, len+1) is passed through unchanged — no absolute
        # mangling, no crash:
        doc_eol = Document(
            text, cursor_position=10, selection=SelectionState(0, SelectionType.CHARACTERS)
        )
        result = doc_eol.selection_range_at_line(1)
        assert result == thai._ORIGINAL_DOCUMENT_METHODS["selection_range_at_line"](doc_eol, 1)


def test_block_selection_rectangles_align_on_cells(editor):
    # 077: visual-block (C-v) rects must be defined on VISUAL columns —
    # cell-cluster ordinals — not codepoint columns that count combining
    # marks. Whole cells light up on every row; no mid-cluster mark slices.
    from prompt_toolkit.application.current import set_app
    from prompt_toolkit.selection import SelectionState, SelectionType

    editor.load_initial_files([None])
    text = "ที่ดิน\nปั้\nกำแพง\n"
    # clusters: row0 'ที่ดิน' [0,3,5]; row1 'ปั้' [0]; row2 'กำแพง' [0,1,2,3,4]

    with set_app(editor.application):
        # Corners: codepoint 5 (row 0, cluster ordinal 2 = น) and 13
        # (row 2, cluster ordinal 2 = แ). The rect is visual columns 2..2.
        doc = Document(text, cursor_position=13, selection=SelectionState(5, SelectionType.BLOCK))

        row0 = doc.selection_range_at_line(0)
        row1 = doc.selection_range_at_line(1)
        row2 = doc.selection_range_at_line(2)
        assert row0 == (5, 6)  # cell ordinal 2 on row 0 = น (was (2, 6) vanilla: mid-cluster)
        assert row2 == (2, 3)  # cell ordinal 2 on row 2 = แ (1 cell wide, docs/bugs/092.md)
        assert row1 == (3, 4)  # row 1 has no cell at ordinal 2: only the EOL space stays

        # LINES selection still spans its lines untouched:
        doc_lines = Document(
            text, cursor_position=13, selection=SelectionState(0, SelectionType.LINES)
        )
        assert doc_lines.selection_range_at_line(1) == (0, len(doc_lines.lines[1]) + 1)


def test_block_selection_ascii_rectangle_matches_vanilla(editor):
    # 077: on ASCII documents the block rect is unchanged compared with
    # prompt_toolkit's own columns (cluster ordinal == codepoint column).
    from prompt_toolkit.application.current import set_app
    from prompt_toolkit.selection import SelectionState, SelectionType

    editor.load_initial_files([None])
    text = "012345\nabcdef\n"
    with set_app(editor.application):
        doc = Document(text, cursor_position=8, selection=SelectionState(5, SelectionType.BLOCK))
        for row in range(len(doc.lines)):
            assert doc.selection_range_at_line(row) == thai._ORIGINAL_DOCUMENT_METHODS[
                "selection_range_at_line"
            ](doc, row)


def test_block_selection_with_sara_am(editor):
    # 092: visual-block selection across lines with SARA AM (GC=Lo, spacing
    # vowel, width 1) previously bled prompt_toolkit's codepoint column into
    # subsequent cells because max(end, cell_end) preserved col2+1 from corner
    # rows with more combining marks (e.g. 'สวัสดี', 'ฉัน' -> col 3).
    from prompt_toolkit.application.current import set_app
    from prompt_toolkit.selection import SelectionState, SelectionType

    editor.load_initial_files([None])
    text = "สวัสดีครับ\nทำอะไรอยู่\nปีใหม่นี้ดีไหม\nฉันจะฝันถึงเธอ\n"
    with set_app(editor.application):
        line4_start = text.index("ฉัน")
        # Visual column 0..1 block: from (row 0, col 0) to (row 3, col 2 = 'น')
        doc = Document(
            text, cursor_position=line4_start + 2, selection=SelectionState(0, SelectionType.BLOCK)
        )

        # Check selection_range_at_line (highlight)
        assert doc.selection_range_at_line(0) == (0, 3)  # 'สวั' (2 visual cells)
        assert doc.selection_range_at_line(1) == (0, 2)  # 'ทำ' (2 visual cells, no 'อ')
        assert doc.selection_range_at_line(2) == (0, 3)  # 'ปีใ' (2 visual cells)
        assert doc.selection_range_at_line(3) == (0, 3)  # 'ฉัน' (2 visual cells)

        # Check selection_ranges (yank/cut)
        ranges = list(doc.selection_ranges())
        slices = [doc.text[f:t] for f, t in ranges]
        assert slices == ["สวั", "ทำ", "ปีใ", "ฉัน"]


def test_block_selection_ranges_align_on_cells(editor):
    # 079: 'C-v I'/'A' multi-cursor positions come from selection_ranges();
    # they must sit on visual cell boundaries, never on a combining mark
    # (insertions on a mark split Thai clusters).
    from prompt_toolkit.application.current import set_app
    from prompt_toolkit.selection import SelectionState, SelectionType

    editor.load_initial_files([None])
    text = "ที่ดิน\nปั้\nกำแพง\n"
    with set_app(editor.application):
        doc = Document(text, cursor_position=8, selection=SelectionState(1, SelectionType.BLOCK))
        ranges = list(doc.selection_ranges())
        assert ranges == [(0, 3), (7, 10)]  # whole [ที่] and [ปั้] cells


def test_block_selection_ranges_ascii_matches_vanilla(editor):
    # 079: on ASCII documents block ranges are prompt_toolkit's own.
    from prompt_toolkit.application.current import set_app
    from prompt_toolkit.selection import SelectionState, SelectionType

    editor.load_initial_files([None])
    text = "0123456\nabcdef\n"
    with set_app(editor.application):
        doc = Document(text, cursor_position=8, selection=SelectionState(1, SelectionType.BLOCK))
        assert list(doc.selection_ranges()) == list(
            thai._ORIGINAL_DOCUMENT_METHODS["selection_ranges"](doc)
        )


def test_block_d_aligned_with_highlight():
    # 084: block `d` cuts exactly the highlighted cells — operators park the
    # cursor one codepoint back (a combining mark), which previously skewed
    # the cut rectangle one cell up/left vs the on-screen highlight.
    editor = _make_editor_with_text("ที่ดิน\nสีส\nxyz\n")
    # Single cell right (3l would compose a count-3 move): cursor on ด (cell
    # ordinal 1), then C-v, j (cursor keeps ordinal 1 on row 1: ส), then d.
    _feed_keys(editor, "l", Keys.ControlV, "j", "d")
    # cut exactly the highlighted cells [ดิ] / [ส]:
    assert editor.application.clipboard.get_data().text == "ดิ\nส"
    assert _active_buffer(editor).text == "ที่น\nสี\nxyz\n"


def test_gate_decay_respects_thai_statusbar_and_names(editor):
    # 082: gate decay previously looked only at buffer texts; Thai file
    # names (status bar/overlay) and Thai `editor.message` banners also
    # render Thai cells and must keep the sanitizer armed.
    editor.load_initial_files([None])
    eb = editor.window_arrangement.active_editor_buffer

    thai.note_document_text("ดำ")
    assert thai._THAI_GATE.value is True
    eb.location = "ดำ.py"
    editor.sync_with_prompt_toolkit()
    assert thai._THAI_GATE.value is True  # Thai location

    eb.location = "plain.py"
    editor.sync_with_prompt_toolkit()
    assert thai._THAI_GATE.value is False

    thai.note_document_text("ดำ")
    editor.message = "E94: ขออภัย"
    editor.sync_with_prompt_toolkit()
    assert thai._THAI_GATE.value is True  # Thai message banner

    editor.message = None
    editor.sync_with_prompt_toolkit()
    assert thai._THAI_GATE.value is False


def test_charwise_highlight_matches_yank_cluster_boundaries(editor):
    # 085: charwise highlight extends over the last selected cell's cluster
    # — previously the highlight showed ที่ด while yank/cut took ที่ดิ.
    from prompt_toolkit.application.current import set_app
    from prompt_toolkit.selection import SelectionState, SelectionType

    with set_app(editor.application):
        doc = Document(
            "ที่ดิน", cursor_position=3, selection=SelectionState(0, SelectionType.CHARACTERS)
        )
        highlighted = doc.selection_range_at_line(0)
        yanked = list(doc.selection_ranges())[0]
        assert highlighted == yanked == (0, 5)


def test_word_spans_never_slice_clusters():
    # 086: spans must land on cluster boundaries in mixed-script lines
    # (latin base + Thai/Lao/generic Mn marks used to be sliced in half).
    from vith.thai import is_combining_char

    for line in ("x abั de", "ab́c", "ที่ดิน", "กังวล abc"):
        for start, end in thai._word_spans_for_line(line):
            if 0 <= start < len(line):
                assert not is_combining_char(line[start]), (line, start)
            assert end == len(line) or not is_combining_char(line[end]), (line, end)


def test_snap_to_cell_start_preserves_eol_and_snaps_combining_marks():
    # 111 item 12: `snap_to_cell_start` preserves `col == len(line)` (the EOL
    # insert position) even when the line ends with a combining mark, while
    # snapping any in-line combining mark index back to its base character.
    line = "ที่"  # 'ท' (0), 'ี' (1), '่' (2), len == 3
    assert snap_to_cell_start(line, 0) == 0
    assert snap_to_cell_start(line, 1) == 0
    assert snap_to_cell_start(line, 2) == 0
    assert snap_to_cell_start(line, 3) == 3
    assert snap_to_cell_start(line, 99) == 3


def test_cell_starts_size_guarded_cache_bypasses_megabyte_lines():
    # 114 item 2: `cell_starts` caches normal lines in `_cell_starts_cached`
    # and bypasses the LRU cache for lines exceeding `_CELL_STARTS_CACHE_MAX_LINE`.
    from vith.thai.clusters import _CELL_STARTS_CACHE_MAX_LINE, cell_starts

    cell_starts.cache_clear()
    short_line = "ที่ดิน"
    assert cell_starts(short_line) == [0, 3, 5]
    assert cell_starts.cache_info().currsize == 1

    huge_line = "ก" * (_CELL_STARTS_CACHE_MAX_LINE + 10)
    result = cell_starts(huge_line)
    assert len(result) == _CELL_STARTS_CACHE_MAX_LINE + 10
    # Cache size did not grow for the oversized line:
    assert cell_starts.cache_info().currsize == 1


def test_window_and_renderer_patches_tolerate_inserted_parameters(monkeypatch):
    # 111 item 13 & 113 item 3: `_install_window_copy_body_patch` and
    # `_install_renderer_patch` succeed even when upstream inserts an extra
    # parameter before `has_focus` or `size`, resolving inspected arguments by name.
    from prompt_toolkit.layout.containers import Window

    def copy_body_with_inserted_param(
        self,
        ui_content,
        new_screen,
        write_position,
        move_x,
        width,
        extra_upstream_flag=False,
        has_focus=False,
    ):
        return {}, {}

    monkeypatch.setattr(Window, "_copy_body", copy_body_with_inserted_param)
    assert thai._install_window_copy_body_patch() is True
    assert getattr(Window._copy_body, "_vith_thai_patched", False) is True

    def diff_with_inserted_param(
        app,
        output,
        screen,
        extra_upstream_param,
        previous_screen=None,
        is_done=False,
        size=None,
    ):
        return "diff-ok"

    monkeypatch.setattr(thai.ptk_renderer, "_output_screen_diff", diff_with_inserted_param)
    assert thai._install_renderer_patch() is True
    assert getattr(thai.ptk_renderer._output_screen_diff, "_vith_thai_patched", False) is True


def test_autowrap_escape_row_tolerates_mapping_without_setdefault(sticky_gate) -> None:
    # 110 item 12 / 111 item 14 follow-up: exotic `zero_width_escapes`
    # mapping without `setdefault` must not raise KeyError on a missing row.
    import io

    from prompt_toolkit.application import Application
    from prompt_toolkit.data_structures import Point, Size
    from prompt_toolkit.output.color_depth import ColorDepth
    from prompt_toolkit.output.vt100 import Vt100_Output
    from prompt_toolkit.renderer import (
        _StyleStringHasStyleCache,
        _StyleStringToAttrsCache,
    )
    from prompt_toolkit.styles import DummyStyleTransformation, default_ui_style

    thai.note_document_text("ที่")
    screen = Screen()
    for x, ch in enumerate("123456789"):
        screen.data_buffer[0][x] = Char(char=ch, style="")
    screen.data_buffer[0][9] = Char(char="ที่", style="")
    screen.height = 1

    class PlainMapping(dict):
        # `dict` subclass without `setdefault` to force the fallback path.
        setdefault = None  # type: ignore[assignment]

        def __getattribute__(self, name):
            if name == "setdefault":
                raise AttributeError("no setdefault")
            return super().__getattribute__(name)

    plain = PlainMapping()
    screen.zero_width_escapes = plain  # type: ignore[assignment]

    buf = io.StringIO()
    size = Size(rows=4, columns=10)
    vt_out = Vt100_Output(buf, lambda: size)
    style = default_ui_style()
    attrs_cache = _StyleStringToAttrsCache(
        style.get_attrs_for_style_str, DummyStyleTransformation()
    )
    has_style_cache = _StyleStringHasStyleCache(attrs_cache)
    app: Application[object] = Application()

    ptk_renderer._output_screen_diff(
        app,
        vt_out,
        screen,
        Point(x=0, y=0),
        ColorDepth.DEPTH_8_BIT,
        None,
        None,
        False,
        full_screen=True,
        attrs_for_style_string=attrs_cache,
        style_string_has_style=has_style_cache,
        size=size,
        previous_width=0,
    )
    vt_out.flush()
    assert "\x1b[?7h" in buf.getvalue()
    assert 9 not in screen.zero_width_escapes[0]


def test_vith_package_compat_shims_are_lazy():
    # 112 item 4: pre-reorg `vith.thai_*` names remain available as lazy
    # compat shims (no import cost unless accessed).
    import vith
    from vith.thai import classify, clusters, patches, words

    assert vith.thai_words is words
    assert vith.thai_classify is classify
    assert vith.thai_clusters is clusters
    assert vith.thai_patches is patches


def test_tabs_processor_thai_combining_chars_visual_alignment():
    # 127: TabsProcessor in prompt_toolkit previously counted codepoints
    # (pos += 1) rather than visual display width (get_cwidth), causing
    # tab expansions after Thai combining marks to misalign horizontally.
    from prompt_toolkit.layout.processors import TabsProcessor, TransformationInput
    from prompt_toolkit.utils import get_cwidth

    def identity_stod(pos: int) -> int:
        return pos

    lines = [
        "  เพนแทกซ์คือแป้นแตก\tแม้ดูแปลกมีที่มา",
        "ยอดเลนส์ทาคูมาร์\t\tพัฒนาเป็นเมาท์เค",
        "กล้องฟิล์มสีสวยสด\t\tดังสีหยดสาดรดเท",
        "ดิจิตอลดีไซน์เท่\t\tแป้นแสนเก๋ตัวเล็กดี",
        "  บางภาพเราโอเวอร์\tบ้างอันเดอร์ไม่พอดี",
        "หื่นฮาเป็นพิธี\t\tฟุ้งวจีมิตรภาพอวล",
        "น้าป้าหลายหลากรุ่น\t\tทั้งวัยรุ่นและจอมกวน",
        "แตกต่างกันแต่ล้วน\t\tจุดมุ่งหมายถ่ายภาพเอย",
    ]

    # Test with tabstop = 8 (Vim default in pentax.txt screenshot)
    proc8 = TabsProcessor(tabstop=8, char1="|", char2="-")
    for line in lines:
        doc = Document(line, 0)
        ti = TransformationInput(
            buffer_control=None,
            document=doc,
            lineno=0,
            source_to_display=identity_stod,
            fragments=[("", line)],
            width=80,
            height=1,
        )
        tf = proc8.apply_transformation(ti)
        rendered = "".join(text for _, text in tf.fragments)
        parts = line.split("\t")
        second_text = parts[-1]
        idx_second = rendered.index(second_text)
        prefix = rendered[:idx_second]
        visual_col = sum(get_cwidth(c) for c in prefix)
        assert visual_col == 24, f"Line {line!r} aligned at visual col {visual_col} instead of 24"

    # Test with tabstop = 4
    proc4 = TabsProcessor(tabstop=4, char1="|", char2="-")
    for line in lines:
        doc = Document(line, 0)
        ti = TransformationInput(
            buffer_control=None,
            document=doc,
            lineno=0,
            source_to_display=identity_stod,
            fragments=[("", line)],
            width=80,
            height=1,
        )
        tf = proc4.apply_transformation(ti)
        rendered = "".join(text for _, text in tf.fragments)
        parts = line.split("\t")
        second_text = parts[-1]
        idx_second = rendered.index(second_text)
        prefix = rendered[:idx_second]
        visual_col = sum(get_cwidth(c) for c in prefix)
        assert visual_col % 4 == 0, f"Line {line!r} not on tabstop=4: col {visual_col}"


def test_tabs_processor_cursor_position_and_display_mapping():
    # Test source_to_display and display_to_source with Thai combining characters.
    from prompt_toolkit.layout.processors import TabsProcessor, TransformationInput

    def identity_stod(pos: int) -> int:
        return pos

    proc = TabsProcessor(tabstop=8, char1="|", char2="-")
    line = "  เพนแทกซ์คือแป้นแตก\tแม้ดูแปลกมีที่มา"
    doc = Document(line, 0)
    ti = TransformationInput(
        buffer_control=None,
        document=doc,
        lineno=0,
        source_to_display=identity_stod,
        fragments=[("", line)],
        width=80,
        height=1,
    )
    tf = proc.apply_transformation(ti)

    # Source index of the tab is 20
    tab_src_idx = line.index("\t")
    assert tab_src_idx == 20
    # Tab in display starts at fragment index 20
    assert tf.source_to_display(tab_src_idx) == 20

    # Source index of 'แม้' (after tab) is 21
    after_tab_src_idx = line.index("แม้")
    assert after_tab_src_idx == 21
    # Tab expands by 7 display cells: 20 + 7 = 27
    assert tf.source_to_display(after_tab_src_idx) == 27

    # Clicking anywhere inside the tab (indices 20..26) maps back to tab character (20)
    for disp_idx in range(20, 27):
        assert tf.display_to_source(disp_idx) == 20

    # Clicking on 'แม้' (index 27) maps to 21
    assert tf.display_to_source(27) == 21


def test_tabs_processor_cjk_double_width_tab_alignment():
    # Tabs following double-width (CJK) characters advance by visual width (127).
    from prompt_toolkit.layout.processors import TabsProcessor, TransformationInput
    from prompt_toolkit.utils import get_cwidth

    def identity_stod(pos: int) -> int:
        return pos

    proc = TabsProcessor(tabstop=4, char1="|", char2="-")
    # '中' is width 2, so tab at visual col 2 expands to width 2 (col 4).
    line = "中\tA"
    doc = Document(line, 0)
    ti = TransformationInput(
        buffer_control=None,
        document=doc,
        lineno=0,
        source_to_display=identity_stod,
        fragments=[("", line)],
        width=80,
        height=1,
    )
    tf = proc.apply_transformation(ti)
    rendered = "".join(text for _, text in tf.fragments)
    assert rendered == "中|-A"
    idx_a = rendered.index("A")
    prefix = rendered[:idx_a]
    assert sum(get_cwidth(c) for c in prefix) == 4


def test_version_gate_rollback_covers_tabs_processor_patch(monkeypatch):
    # Failure during the self-test must roll back the TabsProcessor patch.
    from prompt_toolkit.layout.processors import TabsProcessor

    # Ensure clean unpatched state for test
    orig = getattr(TabsProcessor.apply_transformation, "_vith_original", None)
    if orig is not None:
        monkeypatch.setattr(TabsProcessor, "apply_transformation", orig)

    def _broken_next_cell_offset(line: str, col: int, count: int = 1) -> int:
        return col + 99

    monkeypatch.setattr(thai.patches, "next_cell_offset", _broken_next_cell_offset)
    assert thai.install_prompt_toolkit_thai_patches() is False

    # TabsProcessor patch must be rolled back
    assert getattr(TabsProcessor.apply_transformation, "_vith_thai_patched", False) is False

    # Restoring allows patch to install cleanly
    monkeypatch.undo()
    assert thai.install_prompt_toolkit_thai_patches() is True
    assert getattr(TabsProcessor.apply_transformation, "_vith_thai_patched", False) is True

