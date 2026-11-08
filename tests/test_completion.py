"""
Tests for `vith/completion.py` (P3: singleton + line-cached word completer).
"""

from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document

from vith.completion import DocumentCompleter, DocumentWordsCompleter


def _completions(completer, text, cursor):
    doc = Document(text, cursor)
    return [c.text for c in completer.get_completions(doc, CompleteEvent())]


def test_words_from_other_lines():
    # 'f' on the cursor line matches 'foo' found in other lines:
    comps = _completions(DocumentWordsCompleter(), 'foo bar baz\nf', 13)
    assert 'foo' in comps


def test_words_from_current_line():
    # 'foobar' on the non-cursor lines completes 'fo' typed on the
    # current line:
    comps = _completions(DocumentWordsCompleter(), 'foobar\nfo', 9)
    assert comps == ['foobar']


def test_exact_word_before_cursor_not_offered():
    # The word itself is never offered as its own completion:
    comps = _completions(DocumentWordsCompleter(), 'foo bar\nfoo', 11)
    assert comps == []


def test_no_word_before_cursor_yields_nothing():
    comps = _completions(DocumentWordsCompleter(), 'foo bar\n ', 9)
    assert comps == []


def test_cache_key_stable_while_typing_on_current_line():
    comp = DocumentWordsCompleter()
    assert comp._cached_key is None

    c1 = _completions(comp, 'alpha beta\nal', 13)
    assert c1 == ['alpha']
    key = comp._cached_key
    assert key is not None

    # Typing on the current line leaves the prefix/suffix around that line
    # unchanged: the non-cursor word cache must *not* be recomputed.
    c2 = _completions(comp, 'alpha beta\nalp', 14)
    assert c2 == ['alpha']
    assert comp._cached_key == key, 'typing on the current line must not rescan the other lines'


def test_cache_invalidated_when_other_lines_change():
    comp = DocumentWordsCompleter()
    _completions(comp, 'alpha beta\nal', 13)
    key_before = comp._cached_key

    # 'alpine' appears on a non-cursor line of the *new* text:
    c3 = _completions(comp, 'alpha zebra alpine\nal', 21)
    assert c3 == ['alpha', 'alpine']
    assert comp._cached_key != key_before, (
        'changed other lines must invalidate the non-cursor word cache'
    )


def test_document_completer_persists_words_completer(editor, editor_buffer):
    # P3: `DocumentCompleter` must reuse a single `DocumentWordsCompleter`
    # across invocations (the cache is only useful if it persists).
    editor.enable_jedi = False
    editor_buffer.location = None  # treated as '.txt' -> words completer

    completer = DocumentCompleter(editor, editor_buffer)
    assert isinstance(completer._words_completer, DocumentWordsCompleter)
    assert completer._words_completer._cached_key is None

    doc = Document('gamma alpha\ngamm', 16)
    assert 'gamma' in [c.text for c in completer.get_completions(doc, CompleteEvent())]
    assert completer._words_completer._cached_key is not None

    words_completer = completer._words_completer
    list(completer.get_completions(doc, CompleteEvent()))
    assert completer._words_completer is words_completer, (
        'the words completer instance must persist across calls'
    )


def test_document_completer_python_uses_jedi(editor, editor_buffer, tmp_path):
    import pytest

    pytest.importorskip('jedi')

    p = tmp_path / 'mod.py'
    p.write_text('import os\n')
    editor.enable_jedi = True
    editor_buffer.location = str(p)

    completer = DocumentCompleter(editor, editor_buffer)
    doc = Document('import o', 8)
    # Iterate: this must exercise the Jedi path (both old `completions()`
    # and new `complete(line, column)` APIs) and not the words completer.
    names = [c.text for c in completer.get_completions(doc, CompleteEvent())]
    assert 'os' in names
    assert completer._words_completer._cached_key is None, (
        '.py files with Jedi enabled must not use the words completer'
    )
    # The jedi API introspection is computed once per session (docs/bugs/030.md):
    import vith.completion as _pc

    assert _pc._JEDI_TAKES_COLUMN is False


def test_document_completer_falls_back_without_jedi(editor, editor_buffer, monkeypatch):
    # jedi is optional: when the module is missing (or the probe is forced
    # off), `.py` buffers must complete with the words completer instead of
    # raising ImportError on the first Tab press (docs/bugs/003.md).
    monkeypatch.setattr('vith.completion._jedi_available', lambda: False)
    editor.enable_jedi = True
    editor_buffer.location = '/some/project/module.py'

    completer = DocumentCompleter(editor, editor_buffer)
    doc = Document('gamma alpha\ngamm', 16)
    names = [c.text for c in completer.get_completions(doc, CompleteEvent())]
    assert names == ['gamma']

    # And with no word before the cursor the fallback stays silent.
    doc = Document('import o', 8)
    assert [c.text for c in completer.get_completions(doc, CompleteEvent())] == []


def test_python_completer_missing_module_degrades_to_no_completions(
    editor, editor_buffer, monkeypatch
):
    # Even if selection ever picks `_PythonCompleter`, a missing jedi module
    # must degrade to "no completions" rather than crash (ImportError guard).
    import sys

    import vith.completion as completion_module

    monkeypatch.setitem(sys.modules, 'jedi', None)  # `import jedi` -> ImportError
    monkeypatch.setattr(completion_module, '_jedi_available', lambda: True)

    editor.enable_jedi = True
    editor_buffer.location = '/some/project/module.py'
    completer = DocumentCompleter(editor, editor_buffer)

    doc = Document('import o', 8)
    list(completer.get_completions(doc, CompleteEvent()))  # must not raise


def test_thai_words_not_split_at_combining_marks():
    text = 'ที่ดิน น้ำ ผู้ใหญ่\nที่'
    comps = _completions(DocumentWordsCompleter(), text, len('ที่ดิน น้ำ ผู้ใหญ่\nที่'))
    assert 'ที่ดิน' in comps or 'ดิน' not in comps
    # The corrupted single-base token 'ใหญ' (missing tone mark) must not appear.
    assert 'ใหญ' not in comps


def test_document_words_completer_cache_key_is_cheap():
    # 072: the cache key must not slice/hash the whole document per Tab.
    # Typing on the cursor line keeps the cache; length changes on other
    # lines invalidate it.
    from prompt_toolkit.document import Document

    from vith.completion import DocumentWordsCompleter

    completer = DocumentWordsCompleter()
    fake_event = object()  # (complete_event is unused by this completer)

    doc1 = Document('alpha bet\nother line\n', 2)  # 'al' completes 'alpha'
    words1 = [c.text for c in completer.get_completions(doc1, fake_event)]
    assert 'alpha' in words1
    key1 = completer._cached_key

    # Typing on the cursor line (row 0): key unchanged -> cache hit.
    doc2 = Document('alpha bets\nother line\n', 3)
    words2 = [c.text for c in completer.get_completions(doc2, fake_event)]
    assert 'alpha' in words2
    assert completer._cached_key == key1

    # Editing another line (suffix length changed): cache recomputed.
    doc3 = Document('alpha bets\nother longer line\n', 3)
    list(completer.get_completions(doc3, fake_event))
    assert completer._cached_key != key1


def test_jedi_availability_probe_is_cached():
    # 072: the find_spec probe used to run on every Tab.
    from vith.completion import _jedi_available

    _jedi_available.cache_clear()
    first = _jedi_available()
    second = _jedi_available()
    assert first is second
    info = _jedi_available.cache_info()
    assert info.misses == 1
    assert info.hits == 1
