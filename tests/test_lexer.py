"""
Tests for `vith/lexer.py` (P1: cached, streaming `DocumentLexer`).
"""

from prompt_toolkit.document import Document
from prompt_toolkit.lexers.pygments import PygmentsLexer

from vith.lexer import DocumentLexer


class _StubEditorBuffer(object):
    def __init__(self, location, in_file_explorer_mode=False, text=None):
        self.location = location
        self.in_file_explorer_mode = in_file_explorer_mode
        if text is not None:
            self.buffer = Document(text, 0)


def _make_python_doc(num_lines=120):
    # Includes a 40-line multiline docstring (lines 40..80) to exercise
    # continuous tokenization across string boundaries.
    lines = []
    for i in range(num_lines):
        if i == 40:
            lines.append('def doc_func():\n    """start multiline docstring')
        elif 40 < i < 80:
            lines.append('    inside multiline docstring %i' % i)
        elif i == 80:
            lines.append('    end multiline docstring"""\n    return 42')
        else:
            lines.append('def func_%i(x):\n    return x * %i  # c %i' % (i, i, i))
    text = '\n'.join(lines)
    doc = Document(text, 0)
    _ = doc.lines
    return doc


def test_lex_document_matches_pygments_lexer():
    """Token-for-token parity with prompt_toolkit's `PygmentsLexer`."""
    doc = _make_python_doc()
    stub = _StubEditorBuffer('sample.py')

    new_get_line = DocumentLexer(stub).lex_document(doc)
    ref_get_line = PygmentsLexer.from_filename('sample.py', sync_from_start=False).lex_document(doc)

    for row in range(len(doc.lines)):
        assert new_get_line(row) == ref_get_line(row), 'Token mismatch at row %i: %r != %r' % (
            row,
            new_get_line(row),
            ref_get_line(row),
        )


def test_lexer_instance_is_cached():
    """The Pygments lexer instance must not be re-looked-up on every edit."""
    doc = _make_python_doc()
    stub = _StubEditorBuffer('sample.py')
    lexer = DocumentLexer(stub)

    get_line = lexer.lex_document(doc)
    get_line(5)

    assert lexer._pygments_lexer is not None
    first = lexer._pygments_lexer
    assert lexer._cached_location == 'sample.py'

    # A new `lex_document` call (simulating the next keystroke) reuses it:
    get_line = lexer.lex_document(doc)
    get_line(5)
    assert lexer._pygments_lexer is first


def test_unknown_extension_falls_back_to_simple_lexer():
    doc = _make_python_doc()
    stub = _StubEditorBuffer('sample.unknownext')
    lexer = DocumentLexer(stub)

    get_line = lexer.lex_document(doc)
    # No Pygments lexer is found (ClassNotFound): plain text highlighting,
    # and the miss must be cached (no re-scan of the Pygments registry).
    for style, _text in get_line(0):
        assert style == '', 'Expected no highlighting, got %r' % style
    assert lexer._pygments_lexer is None
    assert lexer._cached_location == 'sample.unknownext'


def test_no_location_falls_back_to_simple_lexer():
    doc = Document('def f():\n    return 1\n', 0)
    stub = _StubEditorBuffer(None)
    get_line = DocumentLexer(stub).lex_document(doc)
    for style, _text in get_line(0):
        assert style == ''


def test_directory_listing_mode_still_uses_directory_lexer():
    lines = [
        '" ==================================\n',
        '" Directory Listing\n',
        '../\n',
        './\n',
        'subdir/\n',
        'file.py\n',
        'file.md\n',
    ]
    doc = Document('\n'.join(lines), 0)
    _ = doc.lines
    stub = _StubEditorBuffer('/some/dir', in_file_explorer_mode=True)

    from vith.lexer import DirectoryListingLexer

    new_get_line = DocumentLexer(stub).lex_document(doc)
    # The directory-listing mode must keep using the `DirectoryListingLexer`
    # via `PygmentsLexer`, exactly as before the P1 streaming change:
    ref_get_line = PygmentsLexer(DirectoryListingLexer, sync_from_start=False).lex_document(doc)

    for row in range(len(doc.lines)):
        assert new_get_line(row) == ref_get_line(row)


def test_directory_listing_lexer_is_cached():
    # docs/bugs/030.md: constructing a `PygmentsLexer` scans the Pygments
    # registry; file-explorer buffers must reuse one instance instead of
    # rebuilding it on every keystroke.
    doc = Document('" Directory Listing\n../\nsubdir/\n', 0)
    _ = doc.lines
    stub = _StubEditorBuffer('/some/dir', in_file_explorer_mode=True)
    lexer = DocumentLexer(stub)

    first = lexer._get_directory_lexer()
    second = lexer._get_directory_lexer()
    assert first is second
    # Two `lex_document` calls (keystrokes) reuse it as well:
    lexer.lex_document(doc)
    assert lexer._get_directory_lexer() is first


def test_extensionless_python_gets_highlighted():
    # 121: files without a suffix fall back to content sniffing instead of
    # plain text.
    text = 'import os\n\n\ndef main():\n    print(os.getcwd())\n'
    doc = Document(text, 0)
    stub = _StubEditorBuffer('example', text=text)
    lexer = DocumentLexer(stub)

    get_line = lexer.lex_document(doc)
    assert any(style for style, _fragment in get_line(0)), 'expected highlighting, got plain text'
    # Sniffed lexer is cached per location (no registry re-scan per edit).
    assert lexer._cached_location == 'example'
    first = lexer._pygments_lexer
    assert first is not None
    lexer.lex_document(doc)
    assert lexer._pygments_lexer is first


def test_extensionless_shebang_python_gets_highlighted():
    # 121: a python shebang pins the Python lexer without registry guessing.
    text = '#!/usr/bin/env python\nprint("hi")\n'
    doc = Document(text, 0)
    stub = _StubEditorBuffer('runme', text=text)
    get_line = DocumentLexer(stub).lex_document(doc)
    assert any(style for style, _fragment in get_line(1)), 'expected highlighting, got plain text'


def test_extensionless_blank_stays_plain():
    # 121: empty/blank extensionless buffers keep the plain-text fallback.
    for text in ('', '   \n  \n'):
        doc = Document(text, 0)
        stub = _StubEditorBuffer('example', text=text)
        lexer = DocumentLexer(stub)
        get_line = lexer.lex_document(doc)
        for style, _fragment in get_line(0):
            assert style == ''
        assert lexer._pygments_lexer is None


def test_legacy_fallback_sniffs_extensionless(monkeypatch) -> None:
    # 121: the legacy branch sniffs content too instead of degrading to
    # plain text for extensionless scripts.
    import vith.lexer as lexer_mod

    monkeypatch.setattr(lexer_mod, "_STREAMING_AVAILABLE", False)

    text = 'import os\n\n\ndef main():\n    print(os.getcwd())\n'
    doc = Document(text, 0)
    stub = _StubEditorBuffer('example', text=text)
    lexer = lexer_mod.DocumentLexer(stub)
    get_line = lexer.lex_document(doc)
    assert any(style for style, _fragment in get_line(0))
    assert isinstance(lexer._pygments_lexer, PygmentsLexer)


def test_legacy_fallback_caches_pygments_lexer_per_location(monkeypatch) -> None:
    """065: the non-streaming legacy branch must cache the lexer per location
    instead of re-scanning the Pygments registry (and building a fresh
    `PygmentsLexer`) on every frame."""
    from unittest.mock import MagicMock

    import vith.lexer as lexer_mod

    monkeypatch.setattr(lexer_mod, "_STREAMING_AVAILABLE", False)

    calls = []
    sentinel_lexer = MagicMock()
    sentinel_lexer.lex_document = lambda document: lambda i: []

    def fake_from_filename(cls, filename, sync_from_start=True):
        calls.append(filename)
        return sentinel_lexer

    monkeypatch.setattr(lexer_mod.PygmentsLexer, "from_filename", classmethod(fake_from_filename))

    editor_buffer = MagicMock()
    editor_buffer.location = "f.txt"
    editor_buffer.in_file_explorer_mode = False
    document = MagicMock()

    lexer_instance = lexer_mod.DocumentLexer(editor_buffer)
    lexer_instance.lex_document(document)
    lexer_instance.lex_document(document)
    # Second call is a cache hit:
    assert calls == ["f.txt"]

    # A different location invalidates the cache:
    editor_buffer.location = "g.py"
    lexer_instance.lex_document(document)
    assert calls == ["f.txt", "g.py"]


def test_legacy_fallback_degrades_to_simple_lexer(monkeypatch) -> None:
    """065: unknown extensions on the legacy branch degrade to a `SimpleLexer`
    (prompt_toolkit's own `ClassNotFound` handling), not a crash."""
    from unittest.mock import MagicMock

    from prompt_toolkit.lexers import SimpleLexer

    import vith.lexer as lexer_mod

    monkeypatch.setattr(lexer_mod, "_STREAMING_AVAILABLE", False)

    editor_buffer = MagicMock()
    editor_buffer.location = "unknownextension.zzzz"
    editor_buffer.in_file_explorer_mode = False

    lexer_instance = lexer_mod.DocumentLexer(editor_buffer)
    for document in (MagicMock(), MagicMock()):
        result = lexer_instance.lex_document(document)
        assert result is not None
    # The degraded lexer was cached (not rebuilt per call):
    assert isinstance(lexer_instance._pygments_lexer, SimpleLexer)
