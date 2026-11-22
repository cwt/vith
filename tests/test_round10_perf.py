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


def test_159_render_html_markdown_rst_linear(monkeypatch) -> None:
    """159: `_render_html` avoids O(K^2) full-document joins and preserves block spacing."""
    from vith.commands import info as cmd_info

    monkeypatch.setattr(cmd_info.shutil, 'which', lambda _name: None)

    small_html = '<html><body><h1>Title</h1><p>First <b>para</b></p><p>Second<br>line</p><hr><pre>  x  </pre><p>end</p></body></html>'
    out = cmd_info._render_html(small_html)
    assert 'Title\n\nFirst para\n\nSecond\nline\n\n---\n\n  x  \n\nend' == out

    # 4,000 block tags should complete linearly without O(K^2) blowup.
    large_html = (
        '<html><body>' + ''.join(f'<p>Paragraph {i}</p>' for i in range(4000)) + '</body></html>'
    )
    rendered = cmd_info._render_html(large_html)
    assert rendered.startswith('Paragraph 0\n\nParagraph 1')
    assert rendered.endswith('Paragraph 3999')

    # Verify Markdown & RST fallback renderers with precompiled regexes.
    md = '# Heading\n\nSubheading\n----------\n\n* **bold** [link](https://example.com) `code`\n'
    md_out = cmd_info._render_markdown(md)
    assert 'Heading' in md_out and 'bold' in md_out and 'link' in md_out

    rst = 'Section\n=======\n\n.. note:: hello\n\nUse ``code`` and :ref:`target`.\n'
    rst_out = cmd_info._render_rst(rst)
    assert 'Section\n=======' in rst_out
    assert '.. note:: hello' in rst_out
    assert 'Use code and target.' in rst_out
