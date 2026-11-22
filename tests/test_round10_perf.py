"""Regression tests for Round 10 performance and algorithmic fixes (docs/bugs/155-166)."""

from __future__ import annotations

import subprocess
import sys

from vith.editor import DEFAULT_FILETYPE_TABSTOPS, FiletypeTabstops, _canonical_filetype


def test_155_filetype_tabstops_avoids_eager_pygments_lexer_imports() -> None:
    """155: Initializing FiletypeTabstops(DEFAULT_FILETYPE_TABSTOPS) must not import lexer modules."""
    code = (
        "import sys\n"
        "from vith.editor import DEFAULT_FILETYPE_TABSTOPS, FiletypeTabstops\n"
        "before = {m for m in sys.modules if m.startswith('pygments.lexers.')}\n"
        "ft = FiletypeTabstops(DEFAULT_FILETYPE_TABSTOPS)\n"
        "after = {m for m in sys.modules if m.startswith('pygments.lexers.')}\n"
        "loaded = sorted(m for m in (after - before) if m != 'pygments.lexers._mapping')\n"
        "assert not loaded, f'Unexpected lexer modules imported: {loaded}'\n"
        "assert ft['py'] == 4 and ft['js'] == 2 and ft['c++'] == 4 and ft['yml'] == 2\n"
        "assert ft['vue'] == 2 and ft['gitcommit'] == 8\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr or proc.stdout


def test_155_canonical_filetype_matches_aliases() -> None:
    """155: Alias resolution via _mapping.LEXERS matches canonical Pygments primary aliases."""
    ft = FiletypeTabstops(DEFAULT_FILETYPE_TABSTOPS)
    assert _canonical_filetype('py') == 'python'
    assert _canonical_filetype('python3') == 'python'
    assert _canonical_filetype('js') == 'javascript'
    assert _canonical_filetype('ts') == 'typescript'
    assert _canonical_filetype('c++') == 'cpp'
    assert _canonical_filetype('yml') == 'yaml'
    assert _canonical_filetype('sh') == 'bash'
    assert ft['py'] == 4
    assert ft['sh'] == 2
    assert ft['vue'] == 2


def test_156_tabs_processor_fast_paths_tab_free_lines() -> None:
    """156: Tab-free lines bypass fragment explosion and tabstop/char callbacks."""
    from prompt_toolkit.document import Document
    from prompt_toolkit.formatted_text import StyleAndTextTuples
    from prompt_toolkit.layout.processors import TabsProcessor, TransformationInput

    from vith.thai import install_prompt_toolkit_thai_patches

    install_prompt_toolkit_thai_patches()
    calls = {'tabstop': 0, 'char2': 0}

    def counted_tabstop() -> int:
        calls['tabstop'] += 1
        return 4

    def counted_char2() -> str:
        calls['char2'] += 1
        return '-'

    proc = TabsProcessor(tabstop=counted_tabstop, char1='|', char2=counted_char2)
    frags: StyleAndTextTuples = [('class:kw', 'def'), ('', ' foo(x): return x + 1')]
    ti = TransformationInput(
        buffer_control=None,  # type: ignore[arg-type]
        document=Document('def foo(x): return x + 1', 0),
        lineno=0,
        source_to_display=lambda p: p,
        fragments=frags,
        width=80,
        height=1,
    )
    tf = proc.apply_transformation(ti)
    assert tf.fragments is frags
    assert calls == {'tabstop': 0, 'char2': 0}
    assert tf.source_to_display(7) == 7
    assert tf.display_to_source(7) == 7

    # Tabbed lines still expand visually and map cursor positions via bisect:
    tabbed_line = 'a\tb'
    ti_tab = TransformationInput(
        buffer_control=None,  # type: ignore[arg-type]
        document=Document(tabbed_line, 0),
        lineno=0,
        source_to_display=lambda p: p,
        fragments=[('', tabbed_line)],
        width=80,
        height=1,
    )
    tf_tab = proc.apply_transformation(ti_tab)
    assert ''.join(frag[1] for frag in tf_tab.fragments) == 'a|--b'
    assert calls == {'tabstop': 1, 'char2': 1}
    assert tf_tab.source_to_display(0) == 0
    assert tf_tab.source_to_display(1) == 1
    assert tf_tab.source_to_display(2) == 4
    assert tf_tab.display_to_source(2) == 1
    assert tf_tab.display_to_source(4) == 2
    assert tf_tab.display_to_source(999) == 4
    assert tf_tab.display_to_source(-1) == 0


def test_156_try_char_cached() -> None:
    """156: `_try_char` caches encoding probes per (char, backup, encoding)."""
    from vith.layout import _try_char, _try_char_for_encoding

    _try_char_for_encoding.cache_clear()
    assert _try_char('\u2508', '.', 'utf-8') == '\u2508'
    assert _try_char('\u2508', '.', 'ascii') == '.'
    assert _try_char('\u2508', '.', 'utf-8') == '\u2508'
    info = _try_char_for_encoding.cache_info()
    assert info.hits >= 1


def test_157_current_line_and_col_and_cursor_motions_avoid_line_index_build() -> None:
    """157: O(line_len) cursor motions do not materialize Document._cache.lines."""
    from prompt_toolkit.document import Document

    from vith.thai import current_line_and_col, install_prompt_toolkit_thai_patches

    install_prompt_toolkit_thai_patches()
    for text, pos, expected in [
        ('', 0, ('', 0)),
        ('abc\ndef', 0, ('abc', 0)),
        ('abc\ndef', 3, ('abc', 3)),
        ('abc\ndef', 4, ('def', 0)),
        ('abc\n', 4, ('', 0)),
        ('ที่ดิน\nสวัสดี', 3, ('ที่ดิน', 3)),
    ]:
        doc = Document(text, cursor_position=pos)
        assert current_line_and_col(doc) == expected

    # Use a unique non-interned text so prompt_toolkit's module-level
    # `_text_to_document_cache` starts cold on every xdist worker.
    unique_text = 'unique_157_line0\nunique_157_line1_middle\nunique_157_line2'
    ascii_doc = Document(unique_text, cursor_position=24)
    assert current_line_and_col(ascii_doc) == ('unique_157_line1_middle', 7)
    assert ascii_doc.get_cursor_left_position(2) == -2
    assert ascii_doc.get_cursor_right_position(3) == 3
    assert ascii_doc._cache.lines is None
    assert ascii_doc._cache.line_indexes is None


def test_157_on_text_changed_skips_document_lines_on_ascii_and_latches_thai(editor) -> None:
    """157: `_on_text_changed` does not split `document.lines` on ASCII edits but still latches Thai."""
    from vith.editor_buffer import EditorBuffer
    from vith.thai import is_thai_gate_latched, reset_thai_text_gate

    reset_thai_text_gate()
    eb = EditorBuffer(editor)
    eb.buffer.text = 'line0\nline1\nline2'
    eb.buffer.cursor_position = 2
    eb.buffer.insert_text('x')
    assert eb.buffer.document._cache.lines is None
    assert not is_thai_gate_latched()

    # Non-ASCII Thai edit still latches the gate immediately:
    eb.buffer.insert_text('ก')
    assert is_thai_gate_latched()


def test_158_join_linear_and_preserves_spacing_semantics(editor) -> None:
    """158: `:join` preserves exact spacing semantics and handles large ranges in O(N)."""
    from vith.commands.handler import handle_command

    editor.load_initial_files([])
    eb = editor.window_arrangement.active_editor_buffer

    # Edge cases: empty first line, trailing spaces, blank interior lines.
    eb.buffer.text = '\n   alpha \n   \n   beta\n   gamma'
    handle_command(editor, ':%j')
    assert eb.buffer.text == 'alpha beta gamma'

    # Large range (10,000 lines) executes linearly without quadratic blowup.
    n = 10_000
    eb.buffer.text = '\n'.join(f'  w{i}' for i in range(n))
    handle_command(editor, ':%j')
    assert eb.buffer.text.startswith('  w0 w1 w2 ')
    assert eb.buffer.text.endswith(f' w{n - 1}')
    assert '\n' not in eb.buffer.text


def test_160_substitute_uses_c_template_for_non_case_replacements() -> None:
    """160: Literal & backref `:s` compile to C template strings; case ops remain exact."""
    import re

    from vith.commands.substitution import (
        _has_overlapping_alternatives,
        _vim_replacement_function,
    )

    # Literal, backslash, &, ~, and backreferences compile to str (C re.subn fast path).
    lit = _vim_replacement_function(r'foo\\bar\&~', 'PREV', 0)
    assert isinstance(lit, str)
    assert re.sub('x', lit, 'x') == r'foo\bar&PREV'

    backref = _vim_replacement_function(r'[\1]-[&]-[\2]', '', 2)
    assert isinstance(backref, str)
    assert re.sub(r'(a)?(b)', backref, 'b') == '[]-[b]-[b]'
    assert re.sub(r'(a)?(b)', backref, 'ab') == '[a]-[ab]-[b]'

    # Case operators still return a callable and preserve Vim case semantics.
    case_fn = _vim_replacement_function(r'\u\1 \U\2\E \l\3', '', 3)
    assert callable(case_fn)
    assert re.sub(r'(\w+) (\w+) (\w+)', case_fn, 'hello world TEST') == 'Hello WORLD tEST'

    # O(A log A) overlapping-alternatives check preserves exact results.
    assert _has_overlapping_alternatives('a|aa|b') is True
    assert _has_overlapping_alternatives('foo|bar|baz') is False
    assert _has_overlapping_alternatives('ab|cd|a') is True


def test_161_normal_caches_key_processor_suppresses_intermediate_hooks_and_stays_linear(
    editor,
) -> None:
    """161: `:norm` caches KeyProcessor, batches `_on_text_changed`, and avoids O(R*N) index rebuilds."""
    from vith.commands.handler import handle_command
    from vith.commands.normal import _build_normal_key_processor

    editor.load_initial_files([None])
    eb = editor.window_arrangement.active_editor_buffer
    editor.sync_with_prompt_toolkit()

    # KeyProcessor is cached across calls and invalidated when a user binding is added.
    kp1 = _build_normal_key_processor(editor, ignore_user_mappings=False)
    kp2 = _build_normal_key_processor(editor, ignore_user_mappings=False)
    kp_bang = _build_normal_key_processor(editor, ignore_user_mappings=True)
    assert kp1 is kp2
    assert kp_bang is not kp1

    @editor.add_key_binding('Z')
    def _dummy_binding(_event) -> None:
        pass

    kp3 = _build_normal_key_processor(editor, ignore_user_mappings=False)
    kp_bang2 = _build_normal_key_processor(editor, ignore_user_mappings=True)
    assert kp3 is not kp1
    assert kp_bang2 is kp_bang

    # Intermediate `_on_text_changed` / `run_reporter` calls are suppressed during `:norm`
    # and fired once at completion; `Document._cache.lines` is not rebuilt on sequential edits.
    n = 600
    eb.buffer.text = '\n'.join(f'row_{i:04d}' for i in range(n))
    reporter_calls = 0
    orig_run_reporter = eb.run_reporter

    def counted_reporter() -> None:
        nonlocal reporter_calls
        reporter_calls += 1
        orig_run_reporter()

    eb.run_reporter = counted_reporter
    handle_command(editor, ':%norm A;')
    assert reporter_calls == 1
    assert eb.has_unsaved_changes is True
    assert eb.buffer.document._cache.lines is None
    assert eb.buffer.text.startswith('row_0000;')
    assert eb.buffer.text.endswith(f'row_{n - 1:04d};')

    # Cross-line edits (`kdd`, `dd`, `O`) invalidate the anchor safely and match Vim semantics:
    # row 1 (l2) -> kdd deletes l1 -> [l2,l3,l4,l5]
    # row 2 (l4) -> kdd deletes l3 -> [l2,l4,l5]
    # row 3 (clamped to l5) -> kdd deletes l4 -> [l2,l5]
    eb.buffer.text = 'l1\nl2\nl3\nl4\nl5'
    handle_command(editor, ':2,4norm kdd')
    assert eb.buffer.document.lines == ['l2', 'l5']

    eb.buffer.text = 'a\nb\nc'
    handle_command(editor, ':1,3norm O#')
    assert eb.buffer.document.lines == ['#', '#', '#', 'a', 'b', 'c']

    # `:g/.../norm` with line-count deltas tracks offsets accurately.
    eb.buffer.text = 'keep 1\ndrop a\nkeep 2\ndrop b\nkeep 3'
    handle_command(editor, ':g/drop/norm dd')
    assert eb.buffer.document.lines == ['keep 1', 'keep 2', 'keep 3']


def test_162_sniff_filetype_avoids_pygments_guess_lexer_import_storm() -> None:
    """162: Plain extensionless/unnamed buffers must not import 215 Pygments lexer modules."""
    code = (
        "import sys\n"
        "from vith.editor import Editor\n"
        "from vith.lexer import DocumentLexer, sniff_filetype_from_text\n"
        "from prompt_toolkit.document import Document\n"
        "before = {m for m in sys.modules if m.startswith('pygments.lexers.')}\n"
        "for plain in ('12345\\n67890\\n', 'README\\nPlain notes\\n', 'สวัสดีชาวโลก\\n'):\n"
        "    assert sniff_filetype_from_text(plain) is None\n"
        "assert sniff_filetype_from_text('#!/usr/bin/env bash\\necho hi\\n') == 'bash'\n"
        "assert sniff_filetype_from_text('#!/usr/bin/env -S python3.14\\nprint(1)\\n') == 'python'\n"
        "assert sniff_filetype_from_text('<!DOCTYPE html><html></html>') == 'html'\n"
        "assert sniff_filetype_from_text('<?xml version=\"1.0\"?><a/>') == 'xml'\n"
        "assert sniff_filetype_from_text('<?php echo 1;') == 'php'\n"
        "assert sniff_filetype_from_text('{\"key\": [1, 2]}') == 'json'\n"
        "assert sniff_filetype_from_text('diff --git a/x b/x\\n--- a/x\\n+++ b/x\\n') == 'diff'\n"
        "e = Editor()\n"
        "e.load_initial_files([None])\n"
        "eb = e.window_arrangement.active_editor_buffer\n"
        "eb.buffer.text = '12345\\n67890\\n'\n"
        "assert eb.filetype == ''\n"
        "assert eb.tabstop == e.tabstop\n"
        "after = {m for m in sys.modules if m.startswith('pygments.lexers.')}\n"
        "loaded = sorted(m for m in (after - before) if m != 'pygments.lexers._mapping')\n"
        "assert not loaded, f'Unexpected Pygments lexer imports: {loaded}'\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr or proc.stdout


def test_162_document_lexer_bounds_line_generators_and_guards_eof() -> None:
    """162: Backward jumps cap active `line_generators` and out-of-range `get_line` returns `[]` in O(1)."""
    from prompt_toolkit.document import Document

    from vith.lexer import DocumentLexer

    class _StubEB:
        def __init__(self, location: str, text: str) -> None:
            self.location = location
            self.in_file_explorer_mode = False
            self.filetype_override = None
            self.buffer = type('_B', (), {'text': text, '_text_version': 0})()

    py_text = '\n'.join(f'def f_{i}():\n    return {i}' for i in range(600))
    doc = Document(py_text, 0)
    lexer = DocumentLexer(_StubEB('sample.py', py_text))
    get_line = lexer.lex_document(doc)

    # Out-of-range lines return [] immediately without creating/draining a generator.
    assert get_line(-1) == []
    assert get_line(len(doc.lines)) == []
    assert get_line(len(doc.lines) + 50) == []

    # Backward jumps cap `line_generators` at `MAX_LINE_GENERATORS` (<= 2).
    for row in range(1100, 100, -150):
        frags = get_line(row)
        assert frags
    closure = dict(
        zip(get_line.__code__.co_freevars, (c.cell_contents for c in get_line.__closure__))
    )
    assert len(closure['line_generators']) <= DocumentLexer.MAX_LINE_GENERATORS


def test_163_thai_sara_am_cache_document_tabstop_and_visual_block_fast_paths(
    editor, monkeypatch
) -> None:
    """163: SARA AM caches prev_screen rows, `_document_tabstop` avoids DummyApplication, Visual Block caches bounds."""
    from prompt_toolkit.application.current import set_app
    from prompt_toolkit.document import Document
    from prompt_toolkit.layout.screen import Char, Screen
    from prompt_toolkit.selection import SelectionState, SelectionType

    from vith.thai import install_prompt_toolkit_thai_patches
    from vith.thai import patches as thai_patches

    install_prompt_toolkit_thai_patches()

    # 1. `_document_tabstop` outside an active Application never calls `get_app()`.
    import prompt_toolkit.application.current as ptk_current

    def _forbid_get_app():
        raise AssertionError('get_app() should not be called by _document_tabstop')

    monkeypatch.setattr(ptk_current, 'get_app', _forbid_get_app)
    assert thai_patches._document_tabstop(Document('abc', 0)) == thai_patches.DEFAULT_TABSTOP
    assert thai_patches._is_vi_mode() is False

    # 2. `_sanitize_and_invalidate_sara_am` caches SARA AM columns on `new_screen`
    #    and poisons neighbors cleanly on differential changes (with and without cache).
    s1 = Screen(default_char=Char(' '))
    s1.data_buffer[0][0] = Char('ท', '')
    s1.data_buffer[0][1] = Char('ำ', '')
    s1.data_buffer[0][2] = Char('\u0e31', '')  # stray width-0 mark -> cleared to space
    thai_patches._sanitize_and_invalidate_sara_am(s1, None)
    assert s1.data_buffer[0][2].char == ' '
    assert getattr(s1, '_vith_sara_am_rows', None) == {0: {1}}

    s2 = Screen(default_char=Char(' '))
    s2.data_buffer[0][0] = Char('ค', '')  # left cell changed, SARA AM stayed -> poison x=1
    s2.data_buffer[0][1] = Char('ำ', '')
    thai_patches._sanitize_and_invalidate_sara_am(s2, s1)
    assert s1.data_buffer[0][1] == thai_patches._SARA_AM_POISON

    # Fallback when `previous_screen` was not previously processed (no cached map):
    s_raw_prev = Screen(default_char=Char(' '))
    s_raw_prev.data_buffer[0][0] = Char('ท', '')
    s_raw_prev.data_buffer[0][1] = Char('ำ', '')
    s_raw_cur = Screen(default_char=Char(' '))
    s_raw_cur.data_buffer[0][0] = Char('ท', '')
    s_raw_cur.data_buffer[0][1] = Char('า', '')  # SARA AM changed, left unchanged -> poison x=0
    thai_patches._sanitize_and_invalidate_sara_am(s_raw_cur, s_raw_prev)
    assert s_raw_prev.data_buffer[0][0] == thai_patches._SARA_AM_POISON

    # 3. Visual Block caches bounds on `doc._cache` and preserves Thai/tab/EOL semantics.
    editor.load_initial_files([None])
    lines = ['\tที่ดิน\tคำ', '', 'ab', '\tสวัสดี\tงาน']
    text = '\n'.join(lines)
    sel = SelectionState(original_cursor_position=1, type=SelectionType.BLOCK)
    doc = Document(text, cursor_position=len(text) - 1, selection=sel)
    with set_app(editor.application):
        r0 = doc.selection_range_at_line(0)
        r1 = doc.selection_range_at_line(1)
        r2 = doc.selection_range_at_line(2)
        r3 = doc.selection_range_at_line(3)
        assert r0 is not None and r0[0] == 1
        assert r1 is None  # empty row before col1=1
        # 181: highlight must match yank — a row past the block's visual
        # columns is skipped by both APIs.
        assert r2 is None
        assert r3 is not None and r3[0] == 1
        assert hasattr(doc._cache, '_vith_block_bounds')
        # `selection_ranges` skips rows whose visual width is strictly less than v_low:
        ranges = list(doc.selection_ranges())
        assert len(ranges) == 2


def test_164_completer_invalidates_non_cursor_edits_and_preserves_p3_cursor_line_cache(
    editor,
) -> None:
    """164: Production DocumentCompleter invalidates same-length non-cursor edits AND keeps P3 hits on cursor-line typing."""
    from prompt_toolkit.completion import CompleteEvent

    from vith.editor_buffer import EditorBuffer

    eb = EditorBuffer(editor)
    completer = eb.buffer.completer
    assert completer is not None
    words_completer = completer._words_completer  # type: ignore[attr-defined]
    event = CompleteEvent()

    # 1. Populate cache on line 1 while line 0 has 'beta'.
    eb.buffer.text = 'beta\nbe'
    eb.buffer.cursor_position = len(eb.buffer.text)
    words = [c.text for c in completer.get_completions(eb.buffer.document, event)]
    assert 'beta' in words
    cached_list_1 = words_completer._other_words_sorted

    # 2. Typing on the cursor line MUST preserve P3 cache hit (same list object!).
    eb.buffer.insert_text('t')  # 'beta\nbet'
    words = [c.text for c in completer.get_completions(eb.buffer.document, event)]
    assert 'beta' in words
    assert words_completer._other_words_sorted is cached_list_1

    eb.buffer.delete_before_cursor(1)  # back to 'beta\nbe'
    words = [c.text for c in completer.get_completions(eb.buffer.document, event)]
    assert 'beta' in words
    assert words_completer._other_words_sorted is cached_list_1

    # 3. Same-length edit on non-cursor line via in-place overwrite MUST invalidate!
    eb.buffer.cursor_position = 0
    eb.buffer.insert_text('zzzz', overwrite=True)  # 'zzzz\nbe' (same length!)
    eb.buffer.cursor_position = len(eb.buffer.text)
    words = [c.text for c in completer.get_completions(eb.buffer.document, event)]
    assert 'beta' not in words
    assert words_completer._other_words_sorted is not cached_list_1

    # 4. Adversarial: edit line 0, complete on line 1, go back and edit line 0 again,
    #    then complete on line 1 -> MUST still invalidate!
    eb.buffer.cursor_position = 0
    eb.buffer.insert_text('best', overwrite=True)  # 'best\nbe'
    eb.buffer.cursor_position = len(eb.buffer.text)
    assert 'best' in [c.text for c in completer.get_completions(eb.buffer.document, event)]

    eb.buffer.cursor_position = 0
    eb.buffer.insert_text('xxxx', overwrite=True)  # 'xxxx\nbe'
    eb.buffer.cursor_position = len(eb.buffer.text)
    assert 'best' not in [c.text for c in completer.get_completions(eb.buffer.document, event)]

    # 5. Direct buffer.text assignment (e.g., Ex commands / undo) also invalidates!
    eb.buffer.text = 'bell\nbe'
    eb.buffer.cursor_position = len(eb.buffer.text)
    assert 'bell' in [c.text for c in completer.get_completions(eb.buffer.document, event)]
    eb.buffer.text = 'yyyy\nbe'
    eb.buffer.cursor_position = len(eb.buffer.text)
    assert 'bell' not in [c.text for c in completer.get_completions(eb.buffer.document, event)]


def test_165_thai_clusters_cell_columns_and_bisect_word_motions_parity() -> None:
    """165: Optimized `cell_stops`, `_compute_cell_columns`, and bisect word motions match exact references."""
    from prompt_toolkit.document import Document

    from vith.thai.clusters import _compute_cell_columns, cell_stops, prev_cell_offset
    from vith.thai.words import (
        _cluster_aligned_spans,
        _iter_word_spans,
        find_boundaries_of_current_word,
        find_next_word_beginning,
        find_next_word_ending,
        find_previous_word_beginning,
        find_previous_word_ending,
        find_thai_cluster_end,
    )

    # 1. `cell_stops` ASCII fast path and `_compute_cell_columns` ASCII+tab / Thai+CJK+tab parity.
    assert cell_stops('abc\td') == [0, 1, 2, 3, 4, 5]
    assert cell_stops('ที่ดิน') == [0, 3, 5, 6]
    assert _compute_cell_columns('', 4) == ((), (), 0)
    assert _compute_cell_columns('a\tb', 4) == ((0, 1, 2), (0, 1, 4), 5)
    assert _compute_cell_columns('ที่\tก', 4) == ((0, 3, 4), (0, 1, 4), 5)
    assert _compute_cell_columns('中\ta', 4) == ((0, 1, 2), (0, 2, 4), 5)

    # 2. Ro Han (รร) cluster detection and ASCII early return in `_cluster_aligned_spans`.
    assert find_thai_cluster_end('ธรรม', 0) == 3  # ธ + ร + ร
    assert find_thai_cluster_end('ที่ดิน', 0) == 3
    ascii_spans = [(0, 3), (4, 7)]
    assert _cluster_aligned_spans('foo bar', ascii_spans) is ascii_spans

    # 3. Exhaustive bisect word-motion parity against `_iter_word_spans` across multi-line mixed text.
    sample = 'ที่ดิน สวัสดี\n  hello การบ้าน!@#\n\nคำถาม สุดท้าย\tไทย'
    spans = _iter_word_spans(sample, False)
    for pos in range(len(sample) + 1):
        d = Document(sample, cursor_position=pos)
        for cnt in (1, 2, 3):
            exp_w = [s for s, _ in spans if s > pos]
            assert find_next_word_beginning(d, cnt, False) == (
                exp_w[cnt - 1] - pos if len(exp_w) >= cnt else None
            )
            exp_b = [s for s, _ in reversed(spans) if s < pos]
            assert find_previous_word_beginning(d, cnt, False) == (
                exp_b[cnt - 1] - pos if len(exp_b) >= cnt else None
            )
            exp_ge = [e for _, e in reversed(spans) if e <= pos]
            assert find_previous_word_ending(d, cnt, False) == (
                exp_ge[cnt - 1] - pos if len(exp_ge) >= cnt else None
            )
            exp_ei = [e for _, e in spans if e > pos]
            assert find_next_word_ending(d, True, cnt, False) == (
                exp_ei[cnt - 1] - pos if len(exp_ei) >= cnt else None
            )
            exp_e = []
            for s, e in spans:
                r, c_e = d.translate_index_to_position(e)
                ls = d.translate_row_col_to_index(r, 0)
                if ls + prev_cell_offset(d.lines[r], c_e, 1) > pos:
                    exp_e.append(e)
            assert find_next_word_ending(d, False, cnt, False) == (
                exp_e[cnt - 1] - pos if len(exp_e) >= cnt else None
            )

    # 4. `find_boundaries_of_current_word` bisect matches span containing cursor.
    doc_iw = Document('ที่ดิน สวัสดี', cursor_position=1)
    start_rel, end_rel = find_boundaries_of_current_word(doc_iw)
    assert doc_iw.text[1 + start_rel : 1 + end_rel] == 'ที่ดิน'

    # 5. Cursor-line motions do not build `Document._cache.lines`.
    unique_thai = 'uniq165 ที่ดิน สวัสดี การบ้าน\nบรรทัดสอง'
    d_fast = Document(unique_thai, cursor_position=0)
    assert find_next_word_beginning(d_fast, 1, False) is not None
    assert d_fast._cache.lines is None


def test_166_secondary_io_editing_and_command_hot_spots(editor, tmp_path, monkeypatch) -> None:
    """166: Verify all 7 secondary optimizations (>> splice, :w >> append, _read CR guard, :sort nu, DirectoryIO scandir, static :g/pat/m N, FiletypeCompleter)."""
    import gzip

    from prompt_toolkit.completion import CompleteEvent
    from prompt_toolkit.document import Document

    from vith.commands import completer as cmd_completer
    from vith.commands.handler import handle_command
    from vith.editor_buffer import EditorBuffer
    from vith.io.backends import DirectoryIO, FileIO, GZipFileIO
    from vith.key_bindings import _shift_lines

    # 1. `_shift_lines` splices without materializing `Document._cache.lines`.
    editor.load_initial_files([None])
    eb = editor.window_arrangement.active_editor_buffer
    eb.buffer.text = '\n'.join(f'uniq166_line_{i}' for i in range(200))
    eb.buffer.cursor_position = eb.buffer.text.index('uniq166_line_50')
    pad = ' ' * eb.shiftwidth if editor.expand_tab else '\t'
    _shift_lines(editor, eb.buffer, 2, indent=True)
    assert eb.buffer.document._cache.lines is None
    assert f'{pad}uniq166_line_50\n{pad}uniq166_line_51\nuniq166_line_52' in eb.buffer.text
    _shift_lines(editor, eb.buffer, 2, indent=False)
    assert eb.buffer.document._cache.lines is None
    assert '\nuniq166_line_50\nuniq166_line_51\nuniq166_line_52' in eb.buffer.text

    # 2. `:w >>` uses native `FileIO.append` without reading the target file (and keeps GZipFileIO valid).
    target = tmp_path / 'log.txt'
    target.write_text('existing\n', encoding='utf-8')
    orig_read = FileIO.read

    def _forbid_read(self, loc):
        if str(loc) == str(target):
            raise AssertionError('FileIO.read must not be called on native :w >> append')
        return orig_read(self, loc)

    monkeypatch.setattr(FileIO, 'read', _forbid_read)
    eb.buffer.text = 'appended'
    handle_command(editor, f':w >> {target}')
    assert target.read_text(encoding='utf-8') == 'existing\nappended\n'
    monkeypatch.setattr(FileIO, 'read', orig_read)

    # GZipFileIO.append still produces a single valid gzip stream:
    gz_target = tmp_path / 'log.txt.gz'
    gz_io = GZipFileIO()
    gz_io.write(str(gz_target), 'first\n')
    gz_io.append(str(gz_target), 'second\n')
    assert gzip.decompress(gz_target.read_bytes()).decode('utf-8') == 'first\nsecond\n'

    # 3. `EditorBuffer._read` preserves unix/dos/mac detection under `'\r' in text` guard.
    unix_f = tmp_path / 'unix.txt'
    dos_f = tmp_path / 'dos.txt'
    mac_f = tmp_path / 'mac.txt'
    unix_f.write_bytes(b'a\nb\n')
    dos_f.write_bytes(b'a\r\nb\r\n')
    mac_f.write_bytes(b'a\rb\r')
    assert EditorBuffer(editor, str(unix_f)).fileformat == 'unix'
    assert EditorBuffer(editor, str(dos_f)).fileformat == 'dos'
    assert EditorBuffer(editor, str(mac_f)).fileformat == 'mac'

    # 4. `:sort nu` / `:sort! nu` / `:sort iun` single-pass key extraction + adjacent dedup.
    eb.buffer.text = 'item 10 b\nitem 2 a\nitem 10 a\nno_num_b\nno_num_a\nno_num_a'
    handle_command(editor, ':%sort nu')
    # Stable numeric sort keeps original order within equal-key groups (Vim 9.1).
    assert eb.buffer.document.lines == ['no_num_b', 'no_num_a', 'item 2 a', 'item 10 b']
    eb.buffer.text = 'Alpha\nalpha\nBETA\nbeta'
    handle_command(editor, ':%sort! iu')
    # Reverse comparator reverses ties too, so the last original casing wins (Vim 9.1).
    assert eb.buffer.document.lines == ['beta', 'alpha']

    # 5. `DirectoryIO.read` uses `os.scandir` and lists sorted dirs before sorted files.
    sub = tmp_path / 'dir_b'
    sub.mkdir()
    (tmp_path / 'dir_a').mkdir()
    (tmp_path / 'file_z.txt').write_text('z', encoding='utf-8')
    (tmp_path / 'file_a.txt').write_text('a', encoding='utf-8')
    listing, enc = DirectoryIO().read(str(tmp_path))
    assert enc == 'utf-8'
    body = [ln for ln in listing.splitlines() if not ln.startswith('"') and ln not in ('../', './')]
    dir_entries = [ln for ln in body if ln.endswith('/')]
    file_entries = [ln for ln in body if not ln.endswith('/')]
    assert dir_entries == sorted(dir_entries)
    assert file_entries == sorted(file_entries)
    assert body == dir_entries + file_entries

    # 6. Static interior numeric `:g/pat/m N` and `:g/pat/t N` match Vim's two-pass semantics.
    eb.buffer.text = 'x0\na1\nx2\na3\nx4\nx5'
    handle_command(editor, ':g/^x/m 3')
    # before k=3: x0, x2 (forward before k=3); after k=3: x4, x5 (reversed at k=3).
    assert eb.buffer.document.lines == ['a1', 'x0', 'x2', 'x5', 'x4', 'a3']

    eb.buffer.text = 'x0\na1\nx2\na3'
    handle_command(editor, ':g/^x/t 2')
    assert eb.buffer.document.lines == ['x0', 'a1', 'x2', 'x0', 'x2', 'a3']

    # 7. `FiletypeCompleter` builds catalog in one `get_all_lexers()` pass and binary-searches prefixes.
    cmd_completer._filetype_catalog.cache_clear()
    comp = cmd_completer.FiletypeCompleter()
    py_matches = [c.text for c in comp.get_completions(Document('pyth'), CompleteEvent())]
    assert 'python' in py_matches
    assert all(m.startswith('pyth') for m in py_matches)
    assert cmd_completer._filetype_catalog.cache_info().currsize == 1
