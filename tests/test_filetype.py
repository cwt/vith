"""Tests for `:set filetype` / `:set ft` buffer-local lexer override."""

from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document

from vith.commands.completer import FiletypeCompleter, create_command_completer
from vith.commands.grammar import COMMAND_GRAMMAR
from vith.commands.handler import handle_command
from vith.commands.preview import CommandPreviewer
from vith.lexer import DocumentLexer

PYTHON_TEXT = 'def foo():\n    return 1\n'


def _load_text_file(editor, tmp_path, filename, content):
    p = tmp_path / filename
    p.write_text(content)
    editor.load_initial_files([str(p)])
    editor.sync_with_prompt_toolkit()
    return editor.window_arrangement.active_editor_buffer


def _first_line_styles(editor_buffer):
    doc = Document(editor_buffer.buffer.text, 0)
    get_line = DocumentLexer(editor_buffer).lex_document(doc)
    return get_line(0)


def _has_keyword(styles):
    return any('keyword' in style.lower() for style, _ in styles)


def test_override_python_highlights_txt_buffer(editor, tmp_path):
    eb = _load_text_file(editor, tmp_path, 'notes.txt', PYTHON_TEXT)
    assert eb.filetype_override is None
    assert not _has_keyword(_first_line_styles(eb))

    handle_command(editor, ':set filetype=python')
    assert eb.filetype_override == 'python'
    assert _has_keyword(_first_line_styles(eb))


def test_ft_alias_and_canonicalization(editor, tmp_path):
    eb = _load_text_file(editor, tmp_path, 'notes.txt', PYTHON_TEXT)

    handle_command(editor, ':set ft=py')
    assert eb.filetype_override == 'python'
    assert _has_keyword(_first_line_styles(eb))


def test_invalid_filetype_shows_message(editor, tmp_path):
    eb = _load_text_file(editor, tmp_path, 'notes.txt', PYTHON_TEXT)

    handle_command(editor, ':set filetype=nosuchlang123')
    assert editor.message == 'Unknown filetype: nosuchlang123'
    assert eb.filetype_override is None
    assert not _has_keyword(_first_line_styles(eb))


def test_display_current_filetype(editor, tmp_path):
    _load_text_file(editor, tmp_path, 'notes.txt', PYTHON_TEXT)

    handle_command(editor, ':set filetype')
    assert editor.message == 'filetype=text'

    handle_command(editor, ':set filetype=python')
    handle_command(editor, ':set filetype')
    assert editor.message == 'filetype=python'

    handle_command(editor, ':set ft')
    assert editor.message == 'filetype=python'


def test_display_py_file_without_override(editor, tmp_path):
    eb = _load_text_file(editor, tmp_path, 'mod.py', PYTHON_TEXT)
    assert eb.filetype_override is None

    handle_command(editor, ':set filetype')
    assert editor.message == 'filetype=python'


def test_clear_override_reverts_to_filename(editor, tmp_path):
    eb = _load_text_file(editor, tmp_path, 'notes.txt', PYTHON_TEXT)
    handle_command(editor, ':set filetype=python')
    assert _has_keyword(_first_line_styles(eb))

    handle_command(editor, ':set filetype=')
    assert eb.filetype_override is None
    assert not _has_keyword(_first_line_styles(eb))

    handle_command(editor, ':set filetype=python')
    handle_command(editor, ':set filetype=off')
    assert eb.filetype_override is None

    handle_command(editor, ':set filetype=python')
    handle_command(editor, ':set filetype=none')
    assert eb.filetype_override is None


def test_untitled_buffer_override(editor):
    editor.load_initial_files([None])
    editor.sync_with_prompt_toolkit()
    eb = editor.window_arrangement.active_editor_buffer
    eb.buffer.text = PYTHON_TEXT

    doc = Document(eb.buffer.text, 0)
    plain = DocumentLexer(eb).lex_document(doc)(0)
    assert all(style == '' for style, _ in plain)

    handle_command(editor, ':set filetype=python')
    assert eb.filetype_override == 'python'
    assert _has_keyword(_first_line_styles(eb))


def test_legacy_fallback_override(monkeypatch, editor, tmp_path):
    import vith.lexer as lexer_mod

    monkeypatch.setattr(lexer_mod, '_STREAMING_AVAILABLE', False)
    eb = _load_text_file(editor, tmp_path, 'notes.txt', PYTHON_TEXT)

    handle_command(editor, ':set filetype=python')
    assert eb.filetype_override == 'python'
    doc = Document(eb.buffer.text, 0)
    get_line = DocumentLexer(eb).lex_document(doc)
    assert get_line is not None


def test_lexer_cache_key_includes_override(editor, tmp_path):
    eb = _load_text_file(editor, tmp_path, 'notes.txt', PYTHON_TEXT)
    lexer = DocumentLexer(eb)
    doc = Document(eb.buffer.text, 0)

    lexer.lex_document(doc)
    assert lexer._cached_location == str(eb.location)
    assert lexer._cached_filetype is None
    first = lexer._pygments_lexer

    # Same location + same (empty) override reuses the instance.
    lexer.lex_document(doc)
    assert lexer._pygments_lexer is first

    # Changing the override invalidates the cache and changes the hash.
    before = lexer.invalidation_hash()
    eb.filetype_override = 'python'
    after = lexer.invalidation_hash()
    assert before != after
    lexer.lex_document(doc)
    assert lexer._cached_filetype == 'python'
    assert lexer._pygments_lexer is not first


def test_grammar_empty_value_is_clear():
    m = COMMAND_GRAMMAR.match(':set filetype=')
    assert m is not None
    assert m.variables().get('set_option') == 'filetype'
    assert m.variables().get('set_value') == ''


def _completions(completer, text):
    doc = Document(text, len(text))
    return list(completer.get_completions(doc, CompleteEvent()))


def test_filetype_completion_all_on_empty(editor):
    completer = create_command_completer(editor)
    comps = _completions(completer, 'set filetype=')
    texts = [c.text for c in comps]
    assert 'python' in texts
    assert 'bash' in texts
    assert len(texts) > 100

    # Leading colon form (as typed via `handle_command` strings).
    comps = _completions(completer, ':set filetype=')
    assert 'python' in [c.text for c in comps]


def test_filetype_completion_filters_prefix(editor):
    completer = create_command_completer(editor)

    comps = _completions(completer, 'set filetype=py')
    texts = [c.text for c in comps]
    assert 'python' in texts
    assert 'py' in texts
    assert 'bash' not in texts
    assert all(t.startswith('py') for t in texts)

    comps = _completions(completer, 'set filetype=b')
    texts = [c.text for c in comps]
    assert 'bash' in texts
    assert 'python' not in texts

    # `ft` alias completes the same way.
    comps = _completions(completer, 'set ft=py')
    assert 'python' in [c.text for c in comps]


def test_filetype_completion_does_not_leak_to_other_options(editor):
    completer = create_command_completer(editor)
    comps = _completions(completer, 'set tabstop=')
    assert 'python' not in [c.text for c in comps]


def test_filetype_completer_start_position(editor):
    completer = FiletypeCompleter()
    comps = _completions(completer, 'py')
    assert comps
    assert all(c.start_position == -2 for c in comps)


def test_preview_filetype_save_and_restore(editor, tmp_path):
    eb = _load_text_file(editor, tmp_path, 'notes.txt', PYTHON_TEXT)
    previewer = CommandPreviewer(editor)
    previewer.save()

    previewer.preview(':set filetype=python')
    assert eb.filetype_override == 'python'

    previewer.restore()
    assert eb.filetype_override is None

    # Invalid preview values are ignored, not applied.
    previewer.preview(':set filetype=nosuchlang123')
    assert eb.filetype_override is None
    previewer.restore()
