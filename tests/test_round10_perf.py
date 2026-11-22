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
