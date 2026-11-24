"""
Regression tests for vertical cursor movement, column jumps (`{count}|`),
visual-block selection (`<C-v>` highlight, `y`, `d`, `I`, `A`), and visual-block
paste (`p`/`P`) on Thai text containing upper/lower vowels, tone marks, and
tab characters (`\\t`).
"""

from __future__ import annotations

import pytest
from prompt_toolkit.application.current import set_app
from prompt_toolkit.document import Document
from prompt_toolkit.input import DummyInput
from prompt_toolkit.key_binding.key_processor import KeyPress
from prompt_toolkit.key_binding.vi_state import InputMode
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout.processors import (
    HighlightSelectionProcessor,
    TabsProcessor,
    TransformationInput,
)
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.utils import get_cwidth

from vith.editor import Editor
from vith.thai.clusters import (
    _cell_columns,
    _cell_ordinal,
    _line_visual_width,
    _start_of_cell_ordinal,
    next_cell_offset,
)


def _end_of_visual_col(line, visual_col, tabstop=8):
    """Test-local equivalent of the retired visual-column helper (bug 228)."""
    from bisect import bisect_right

    n = len(line)
    if not line or visual_col < 0:
        return 0
    if line.isascii() and "\t" not in line:
        return min(visual_col + 1, n)
    starts, columns, total_width = _cell_columns(line, tabstop)
    if not starts or visual_col >= total_width:
        return n
    index = max(0, bisect_right(columns, visual_col) - 1)
    return starts[index + 1] if index + 1 < len(starts) else n


PENTAX_LINES = (
    "  เพนแทกซ์คือแป้นแตก\tแม้ดูแปลกมีที่มา",
    "ยอดเลนส์ทาคูมาร์\t\tพัฒนาเป็นเมาท์เค",
    "กล้องฟิล์มสีสวยสด\t\tดังสีหยดสาดรดเท",
    "ดิจิตอลดีไซน์เท่\t\tแป้นแสนเก๋ตัวเล็กดี",
    "",
    "  บางภาพเราโอเวอร์\tบ้างอันเดอร์ไม่พอดี",
    "หื่นฮาเป็นพิธี\t\tฟุ้งวจีมิตรภาพอวล",
    "น้าป้าหลายหลากรุ่น\t\tทั้งวัยรุ่นและจอมกวน",
    "แตกต่างกันแต่ล้วน\t\tจุดมุ่งหมายถ่ายภาพเอย",
)
PENTAX_TEXT = "\n".join(PENTAX_LINES)


def _make_editor(text: str, cursor_position: int = 0, tabstop: int = 8) -> Editor:
    editor = Editor(output=DummyOutput(), input=DummyInput())
    editor.load_initial_files([None])
    editor.sync_with_prompt_toolkit()
    editor.application.vi_state.input_mode = InputMode.NAVIGATION
    eb = editor.current_editor_buffer
    assert eb is not None
    eb.tabstop = tabstop
    eb.buffer.document = Document(text, cursor_position=cursor_position)
    return editor


def _feed(editor: Editor, *keys: str | Keys) -> None:
    kp = editor.application.key_processor
    with set_app(editor.application):
        for key in keys:
            if isinstance(key, Keys):
                kp.feed(KeyPress(key))
            else:
                for ch in key:
                    kp.feed(KeyPress(ch))
        kp.process_keys()


def _cell_at_cursor(doc: Document) -> str:
    line = doc.current_line
    col = doc.cursor_position_col
    if col >= len(line):
        return ""
    return line[col : next_cell_offset(line, col, 1)]


def _identity_pos(pos: int) -> int:
    return pos


def test_cluster_helpers_expand_tabs_and_combining_marks() -> None:
    # L1: '  เพนแทกซ์คือแป้นแตก' has 17 visual cols -> \t expands to col 24.
    line1 = PENTAX_LINES[0]
    assert _cell_ordinal(line1, 20, tabstop=8) == 17  # '\t' starts at vcol 17
    assert _cell_ordinal(line1, 21, tabstop=8) == 24  # 'แ' after '\t' is at vcol 24
    assert _cell_ordinal(line1, 27, tabstop=8) == 28  # 'ป' is at 0-based vcol 28 (1-based 29)
    assert _start_of_cell_ordinal(line1, 28, tabstop=8) == 27
    assert _end_of_visual_col(line1, 28, tabstop=8) == 28

    # L8: 'รุ่' at vcol 28 is a 3-codepoint cluster ('ร' + 'ุ' + '่').
    line8 = PENTAX_LINES[7]
    assert _start_of_cell_ordinal(line8, 28, tabstop=8) == 27
    assert _end_of_visual_col(line8, 28, tabstop=8) == 30
    assert line8[27:30] == "รุ่"

    # Pure ASCII with tabs must NOT bypass tab expansion.
    ascii_tab = "a\tb\tc"
    assert _line_visual_width(ascii_tab, tabstop=8) == 17
    assert _cell_ordinal(ascii_tab, 2, tabstop=8) == 8  # 'b'
    assert _cell_ordinal(ascii_tab, 4, tabstop=8) == 16  # 'c'
    assert _start_of_cell_ordinal(ascii_tab, 16, tabstop=8) == 4
    assert _end_of_visual_col(ascii_tab, 8, tabstop=8) == 3

    # Width-2 CJK character covered on either column 0 or column 1.
    assert _end_of_visual_col("你好a", 0, tabstop=8) == 1
    assert _end_of_visual_col("你好a", 1, tabstop=8) == 1
    assert _end_of_visual_col("你好a", 2, tabstop=8) == 2


def test_pentax_vertical_jk_stays_on_visual_col_29_across_tabs_and_empty_line() -> None:
    editor = _make_editor(PENTAX_TEXT, cursor_position=27, tabstop=8)
    eb = editor.current_editor_buffer
    assert eb is not None

    expected_cells = ["ป", "เ", "ย", "ส", "", "น", "มิ", "รุ่", "ห"]
    expected_vcols = [29, 29, 29, 29, 1, 29, 29, 29, 29]

    # Walk down L1 -> L9.
    down_cells: list[str] = []
    down_vcols: list[int] = []
    for step in range(len(PENTAX_LINES)):
        doc = eb.buffer.document
        down_cells.append(_cell_at_cursor(doc))
        down_vcols.append(_cell_ordinal(doc.current_line, doc.cursor_position_col, tabstop=8) + 1)
        if step < len(PENTAX_LINES) - 1:
            _feed(editor, "j")

    assert down_cells == expected_cells
    assert down_vcols == expected_vcols

    # Walk back up L9 -> L1 (sticky visual column 29 survives empty line 5).
    up_cells: list[str] = []
    up_vcols: list[int] = []
    for step in range(len(PENTAX_LINES)):
        doc = eb.buffer.document
        up_cells.append(_cell_at_cursor(doc))
        up_vcols.append(_cell_ordinal(doc.current_line, doc.cursor_position_col, tabstop=8) + 1)
        if step < len(PENTAX_LINES) - 1:
            _feed(editor, "k")

    assert up_cells == list(reversed(expected_cells))
    assert up_vcols == list(reversed(expected_vcols))


def test_pentax_vertical_jk_stays_on_visual_col_4_before_tabs() -> None:
    editor = _make_editor(PENTAX_TEXT, cursor_position=3, tabstop=8)
    eb = editor.current_editor_buffer
    assert eb is not None

    expected_cells = ["พ", "เ", "ง", "อ", "", "า", "า", "า", "ต่"]
    expected_vcols = [4, 4, 4, 4, 1, 4, 4, 4, 4]

    cells: list[str] = []
    vcols: list[int] = []
    for step in range(len(PENTAX_LINES)):
        doc = eb.buffer.document
        cells.append(_cell_at_cursor(doc))
        vcols.append(_cell_ordinal(doc.current_line, doc.cursor_position_col, tabstop=8) + 1)
        if step < len(PENTAX_LINES) - 1:
            _feed(editor, "j")

    assert cells == expected_cells
    assert vcols == expected_vcols


def test_pentax_bar_motion_jumps_to_visual_column_29() -> None:
    editor = _make_editor(PENTAX_TEXT, cursor_position=0, tabstop=8)
    eb = editor.current_editor_buffer
    assert eb is not None

    expected_cells = ["ป", "เ", "ย", "ส", "", "น", "มิ", "รุ่", "ห"]
    for row, expected_cell in enumerate(expected_cells):
        eb.buffer.cursor_position = eb.buffer.document.translate_row_col_to_index(row, 0)
        _feed(editor, "29|")
        doc = eb.buffer.document
        assert _cell_at_cursor(doc) == expected_cell
        expected_vcol = 1 if expected_cell == "" else 29
        assert (
            _cell_ordinal(doc.current_line, doc.cursor_position_col, tabstop=8) + 1 == expected_vcol
        )


def test_pentax_visual_block_highlight_aligns_after_tabs_in_display_columns() -> None:
    editor = _make_editor(PENTAX_TEXT, cursor_position=27, tabstop=8)
    eb = editor.current_editor_buffer
    assert eb is not None

    _feed(editor, Keys.ControlV, "3j3l")
    doc = eb.buffer.document
    tabs_proc = TabsProcessor(tabstop=8)
    hi_proc = HighlightSelectionProcessor()

    expected_slices = ["ปลกมี", "เป็นเ", "ยดสา", "สนเก๋"]
    with set_app(editor.application):
        for row, expected_slice in enumerate(expected_slices):
            line = doc.lines[row]
            rng = doc.selection_range_at_line(row)
            assert rng is not None
            assert line[rng[0] : rng[1]] == expected_slice

            # Verify full processor pipeline (TabsProcessor -> HighlightSelectionProcessor)
            # highlights exact display columns 28..31 (1-based 29..32).
            ti_tabs = TransformationInput(
                None,  # type: ignore[arg-type]
                doc,
                row,
                _identity_pos,
                [("", line)],
                80,
                24,
            )
            trans_tabs = tabs_proc.apply_transformation(ti_tabs)
            ti_hi = TransformationInput(
                None,  # type: ignore[arg-type]
                doc,
                row,
                trans_tabs.source_to_display,
                trans_tabs.fragments,
                80,
                24,
            )
            trans_hi = hi_proc.apply_transformation(ti_hi)

            vcol = 0
            highlighted_base_vcols: list[int] = []
            highlighted_chars: list[str] = []
            for fragment in trans_hi.fragments:
                style = fragment[0]
                text = fragment[1]
                for ch in text:
                    width = get_cwidth(ch)
                    if "selected" in style:
                        highlighted_chars.append(ch)
                        if width > 0:
                            highlighted_base_vcols.append(vcol)
                    vcol += width
            assert "".join(highlighted_chars) == expected_slice
            assert highlighted_base_vcols == [28, 29, 30, 31]


def test_pentax_visual_block_y_d_I_A_match_vim() -> None:
    # Yank
    ed_y = _make_editor(PENTAX_TEXT, cursor_position=27, tabstop=8)
    _feed(ed_y, Keys.ControlV, "3j3ly")
    assert ed_y.application.clipboard.get_data().text == "ปลกมี\nเป็นเ\nยดสา\nสนเก๋"

    # Delete
    ed_d = _make_editor(PENTAX_TEXT, cursor_position=27, tabstop=8)
    _feed(ed_d, Keys.ControlV, "3j3ld")
    eb_d = ed_d.current_editor_buffer
    assert eb_d is not None
    assert eb_d.buffer.text.splitlines()[:4] == [
        "  เพนแทกซ์คือแป้นแตก\tแม้ดูแที่มา",
        "ยอดเลนส์ทาคูมาร์\t\tพัฒนามาท์เค",
        "กล้องฟิล์มสีสวยสด\t\tดังสีหดรดเท",
        "ดิจิตอลดีไซน์เท่\t\tแป้นแตัวเล็กดี",
    ]

    # Block Insert (I)
    ed_i = _make_editor(PENTAX_TEXT, cursor_position=27, tabstop=8)
    _feed(ed_i, Keys.ControlV, "3j3lI[X]", Keys.Escape)
    eb_i = ed_i.current_editor_buffer
    assert eb_i is not None
    assert eb_i.buffer.text.splitlines()[:4] == [
        "  เพนแทกซ์คือแป้นแตก\tแม้ดูแ[X]ปลกมีที่มา",
        "ยอดเลนส์ทาคูมาร์\t\tพัฒนา[X]เป็นเมาท์เค",
        "กล้องฟิล์มสีสวยสด\t\tดังสีห[X]ยดสาดรดเท",
        "ดิจิตอลดีไซน์เท่\t\tแป้นแ[X]สนเก๋ตัวเล็กดี",
    ]

    # Block Append (A)
    ed_a = _make_editor(PENTAX_TEXT, cursor_position=27, tabstop=8)
    _feed(ed_a, Keys.ControlV, "3j3lA[X]", Keys.Escape)
    eb_a = ed_a.current_editor_buffer
    assert eb_a is not None
    assert eb_a.buffer.text.splitlines()[:4] == [
        "  เพนแทกซ์คือแป้นแตก\tแม้ดูแปลกมี[X]ที่มา",
        "ยอดเลนส์ทาคูมาร์\t\tพัฒนาเป็นเ[X]มาท์เค",
        "กล้องฟิล์มสีสวยสด\t\tดังสีหยดสา[X]ดรดเท",
        "ดิจิตอลดีไซน์เท่\t\tแป้นแสนเก๋[X]ตัวเล็กดี",
    ]


@pytest.mark.parametrize(
    ("start_row", "start_col", "motion"),
    [
        (0, 27, "3j3l"),  # top-left -> bottom-right
        (3, 26, "3k3h"),  # bottom-right -> top-left
        (0, 31, "3j3h"),  # top-right -> bottom-left
        (3, 23, "3k3l"),  # bottom-left -> top-right
    ],
)
def test_pentax_visual_block_all_four_corner_directions(
    start_row: int, start_col: int, motion: str
) -> None:
    doc = Document(PENTAX_TEXT)
    start_pos = doc.translate_row_col_to_index(start_row, start_col)
    editor = _make_editor(PENTAX_TEXT, cursor_position=start_pos, tabstop=8)
    _feed(editor, Keys.ControlV, motion, "d")
    eb = editor.current_editor_buffer
    assert eb is not None
    assert eb.buffer.text.splitlines()[:4] == [
        "  เพนแทกซ์คือแป้นแตก\tแม้ดูแที่มา",
        "ยอดเลนส์ทาคูมาร์\t\tพัฒนามาท์เค",
        "กล้องฟิล์มสีสวยสด\t\tดังสีหดรดเท",
        "ดิจิตอลดีไซน์เท่\t\tแป้นแตัวเล็กดี",
    ]


def test_pentax_visual_block_across_empty_line_5() -> None:
    ed_d = _make_editor(PENTAX_TEXT, cursor_position=27, tabstop=8)
    _feed(ed_d, Keys.ControlV, "8j3ld")
    eb_d = ed_d.current_editor_buffer
    assert eb_d is not None
    assert eb_d.buffer.text.splitlines() == [
        "  เพนแทกซ์คือแป้นแตก\tแม้ดูแที่มา",
        "ยอดเลนส์ทาคูมาร์\t\tพัฒนามาท์เค",
        "กล้องฟิล์มสีสวยสด\t\tดังสีหดรดเท",
        "ดิจิตอลดีไซน์เท่\t\tแป้นแตัวเล็กดี",
        "",
        "  บางภาพเราโอเวอร์\tบ้างอัร์ไม่พอดี",
        "หื่นฮาเป็นพิธี\t\tฟุ้งวจีาพอวล",
        "น้าป้าหลายหลากรุ่น\t\tทั้งวัยะจอมกวน",
        "แตกต่างกันแต่ล้วน\t\tจุดมุ่งถ่ายภาพเอย",
    ]

    ed_i = _make_editor(PENTAX_TEXT, cursor_position=27, tabstop=8)
    _feed(ed_i, Keys.ControlV, "8j3lI[X]", Keys.Escape)
    eb_i = ed_i.current_editor_buffer
    assert eb_i is not None
    assert eb_i.buffer.text.splitlines() == [
        "  เพนแทกซ์คือแป้นแตก\tแม้ดูแ[X]ปลกมีที่มา",
        "ยอดเลนส์ทาคูมาร์\t\tพัฒนา[X]เป็นเมาท์เค",
        "กล้องฟิล์มสีสวยสด\t\tดังสีห[X]ยดสาดรดเท",
        "ดิจิตอลดีไซน์เท่\t\tแป้นแ[X]สนเก๋ตัวเล็กดี",
        "",
        "  บางภาพเราโอเวอร์\tบ้างอั[X]นเดอร์ไม่พอดี",
        "หื่นฮาเป็นพิธี\t\tฟุ้งวจี[X]มิตรภาพอวล",
        "น้าป้าหลายหลากรุ่น\t\tทั้งวัย[X]รุ่นและจอมกวน",
        "แตกต่างกันแต่ล้วน\t\tจุดมุ่ง[X]หมายถ่ายภาพเอย",
    ]


def test_pentax_visual_block_paste_P_and_p_align_on_visual_columns() -> None:
    # Block yank L1..L4 vcol 29..32, then P at L1 col 29.
    ed_before = _make_editor(PENTAX_TEXT, cursor_position=27, tabstop=8)
    _feed(ed_before, Keys.ControlV, "3j3ly", "gg29|P")
    eb_before = ed_before.current_editor_buffer
    assert eb_before is not None
    assert eb_before.buffer.text.splitlines()[:4] == [
        "  เพนแทกซ์คือแป้นแตก\tแม้ดูแปลกมีปลกมีที่มา",
        "ยอดเลนส์ทาคูมาร์\t\tพัฒนาเป็นเเป็นเมาท์เค",
        "กล้องฟิล์มสีสวยสด\t\tดังสีหยดสายดสาดรดเท",
        "ดิจิตอลดีไซน์เท่\t\tแป้นแสนเก๋สนเก๋ตัวเล็กดี",
    ]

    # Block yank L1..L4 vcol 29..32, then p on L7 col 29 ('มิ', 2 codepoints):
    # must paste AFTER 'มิ' (not split 'ม' and 'ิ') and pad the new row 10.
    ed_after = _make_editor(PENTAX_TEXT, cursor_position=27, tabstop=8)
    _feed(ed_after, Keys.ControlV, "3j3ly", "7gg29|p")
    eb_after = ed_after.current_editor_buffer
    assert eb_after is not None
    assert eb_after.buffer.text.splitlines()[6:] == [
        "หื่นฮาเป็นพิธี\t\tฟุ้งวจีมิปลกมีตรภาพอวล",
        "น้าป้าหลายหลากรุ่น\t\tทั้งวัยรุ่เป็นเนและจอมกวน",
        "แตกต่างกันแต่ล้วน\t\tจุดมุ่งหยดสามายถ่ายภาพเอย",
        "                             สนเก๋",
    ]

    # Charwise `p` on a Thai combining cluster pastes after the whole cluster.
    ed_char = _make_editor("มิตร", cursor_position=0, tabstop=8)
    _feed(ed_char, "yl", "0", "p")
    eb_char = ed_char.current_editor_buffer
    assert eb_char is not None
    assert eb_char.buffer.text == "มิมิตร"


def test_shorter_tabbed_line_inside_visual_block_is_not_dropped() -> None:
    # Row 1 has only 9 codepoints (< row 0's anchor codepoint 27), yet its 3
    # tabs expand to visual col 24 so 'XYZ' sits at visual cols 27..29.
    text = "  เพนแทกซ์คือแป้นแตก\tแม้ดูแปลกมีที่มา\n\t\t\tABCXYZ\nดิจิตอลดีไซน์เท่\t\tแป้นแสนเก๋ตัวเล็กดี"
    ed_y = _make_editor(text, cursor_position=27, tabstop=8)
    _feed(ed_y, Keys.ControlV, "2j2ly")
    assert ed_y.application.clipboard.get_data().text == "ปลก\nYZ\nสนเ"

    ed_d = _make_editor(text, cursor_position=27, tabstop=8)
    _feed(ed_d, Keys.ControlV, "2j2ld")
    eb_d = ed_d.current_editor_buffer
    assert eb_d is not None
    assert eb_d.buffer.text.splitlines() == [
        "  เพนแทกซ์คือแป้นแตก\tแม้ดูแมีที่มา",
        "\t\t\tABCX",
        "ดิจิตอลดีไซน์เท่\t\tแป้นแก๋ตัวเล็กดี",
    ]


def test_ascii_tabs_vertical_motion_and_visual_block() -> None:
    text = "a\tb\tcdefgh\n123456789012345678901234\n\t\tABCDEFGH"
    # L1 'c' is at cp=4, visual col 17 (0-based 16).
    ed_jk = _make_editor(text, cursor_position=4, tabstop=8)
    eb_jk = ed_jk.current_editor_buffer
    assert eb_jk is not None
    _feed(ed_jk, "j")
    assert _cell_at_cursor(eb_jk.buffer.document) == "7"
    _feed(ed_jk, "j")
    assert _cell_at_cursor(eb_jk.buffer.document) == "A"
    _feed(ed_jk, "k")
    assert _cell_at_cursor(eb_jk.buffer.document) == "7"
    _feed(ed_jk, "k")
    assert _cell_at_cursor(eb_jk.buffer.document) == "c"

    ed_d = _make_editor(text, cursor_position=4, tabstop=8)
    _feed(ed_d, Keys.ControlV, "2j2ld")
    eb_d = ed_d.current_editor_buffer
    assert eb_d is not None
    assert eb_d.buffer.text.splitlines() == [
        "a\tb\tfgh",
        "123456789012345601234",
        "\t\tDEFGH",
    ]


def test_custom_tabstop_4_applies_to_motion_and_visual_block() -> None:
    ed_d = _make_editor(PENTAX_TEXT, cursor_position=27, tabstop=4)
    _feed(ed_d, Keys.ControlV, "3j3ld")
    eb_d = ed_d.current_editor_buffer
    assert eb_d is not None
    assert eb_d.buffer.text.splitlines()[:4] == [
        "  เพนแทกซ์คือแป้นแตก\tแม้ดูแที่มา",
        "ยอดเลนส์ทาคูมาร์\t\tพัฒนามาท์เค",
        "กล้องฟิล์มสีสวยสด\t\tดังสีหดรดเท",
        "ดิจิตอลดีไซน์เท่\t\tแป้นแสนเก๋กดี",
    ]

    # Mid-run tabstop change re-anchors cleanly without crashing or using stale width.
    ed_dyn = _make_editor(PENTAX_TEXT, cursor_position=27, tabstop=8)
    eb_dyn = ed_dyn.current_editor_buffer
    assert eb_dyn is not None
    _feed(ed_dyn, "j")  # lands on L2 'เ' (cp 23, vcol 29 at ts=8)
    assert _cell_at_cursor(eb_dyn.buffer.document) == "เ"
    eb_dyn.tabstop = 4
    # At tabstop=4, L2 cp 23 ('เ') is at vcol 25 (0-based 24); L3 vcol 25 is 'ด' (cp 26).
    _feed(ed_dyn, "j")
    doc = eb_dyn.buffer.document
    assert _cell_ordinal(doc.current_line, doc.cursor_position_col, tabstop=4) == _cell_ordinal(
        PENTAX_LINES[1], 23, tabstop=4
    )


@pytest.mark.parametrize("ts", [2, 3, 4, 6, 8, 12])
def test_ex_set_tabstop_and_setlocal_ts_drive_motion_and_blocks(ts: int) -> None:
    from vith.commands.handler import handle_command

    editor = _make_editor(PENTAX_TEXT, cursor_position=27, tabstop=8)
    eb = editor.current_editor_buffer
    assert eb is not None
    handle_command(editor, f"setlocal ts={ts}")
    assert eb.tabstop == ts

    expected_vcol = _cell_ordinal(PENTAX_LINES[0], 27, tabstop=ts)
    _feed(editor, "j")
    doc = eb.buffer.document
    col = doc.cursor_position_col
    cell_start_vcol = _cell_ordinal(doc.current_line, col, tabstop=ts)
    cell_end_vcol = _cell_ordinal(
        doc.current_line, next_cell_offset(doc.current_line, col, 1), tabstop=ts
    )
    # The landed cell (Thai cluster or a `\t` spanning `expected_vcol`) covers `expected_vcol`.
    assert cell_start_vcol <= expected_vcol < cell_end_vcol

    # Moving back up preserves sticky `expected_vcol` even after landing inside a `\t`.
    _feed(editor, "k")
    assert eb.buffer.cursor_position == 27

    # `{count}|` respects `:set tabstop=N` too.
    handle_command(editor, f"set tabstop={ts}")
    eb.buffer.cursor_position = 0
    _feed(editor, f"{expected_vcol + 1}|")
    assert eb.buffer.cursor_position == 27


def test_split_buffers_with_same_text_use_their_own_tabstop() -> None:
    from vith.thai.patches import _document_tabstop
    from vith.window_arrangement import EditorBuffer

    editor = _make_editor(PENTAX_TEXT, cursor_position=0, tabstop=8)
    eb1 = editor.current_editor_buffer
    assert eb1 is not None
    eb2 = EditorBuffer(editor)
    eb2.tabstop = 2
    eb2.buffer.document = Document(PENTAX_TEXT, cursor_position=0)
    editor.window_arrangement.editor_buffers.append(eb2)

    with set_app(editor.application):
        assert _document_tabstop(eb1.buffer.document) == 8
        assert _document_tabstop(eb2.buffer.document) == 2
