"""Regressions for the Round 8 performance inventory (docs/bugs/149.md)."""

from __future__ import annotations

from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document

from vith.commands.commands import _format_register_preview
from vith.completion import DocumentWordsCompleter
from vith.editor_buffer import EditorBuffer
from vith.lexer import DocumentLexer


def test_completer_cache_invalidates_on_text_version():
    completer = DocumentWordsCompleter()
    event = CompleteEvent()
    doc = Document('beta\nbe', cursor_position=len('beta\nbe'))
    assert 'beta' in [c.text for c in completer.get_completions(doc, event, 1)]

    # Same lengths, different non-cursor text, different version: stale word
    # must not be offered.
    changed = Document('zzzz\nbe', cursor_position=len('zzzz\nbe'))
    words = [c.text for c in completer.get_completions(changed, event, 2)]
    assert 'beta' not in words


def test_cached_lexer_miss_retries_after_text_change(editor):
    eb = EditorBuffer(editor)
    lexer = DocumentLexer(eb)
    assert lexer._ensure_lexer(eb.location) is False

    eb.buffer.text = 'def answer():\n    return 42\n'
    eb.buffer._text_version += 1
    assert lexer._ensure_lexer(eb.location) is True


def test_register_preview_truncates_before_escaping():
    preview = _format_register_preview('a' * 100000)
    assert len(preview) == 200
    assert preview.endswith('...')
