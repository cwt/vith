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
