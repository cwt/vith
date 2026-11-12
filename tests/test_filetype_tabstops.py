"""
Tests for per-filetype tab stops and buffer-local tabstop options.
"""

from __future__ import annotations

from vith.commands.handler import handle_command
from vith.commands.preview import CommandPreviewer
from vith.editor import DEFAULT_FILETYPE_TABSTOPS, FiletypeTabstops
from vith.key_bindings import _insert_indentation, _shift_lines


def _load_file(editor, tmp_path, filename, content):
    p = tmp_path / filename
    p.write_text(content)
    editor.window_arrangement.open_buffer(str(p), show_in_current_window=True)
    editor.sync_with_prompt_toolkit()
    return editor.window_arrangement.active_editor_buffer


def test_filetype_tabstops_defaults():
    # 2 spaces: web, config, shell, scripting
    assert DEFAULT_FILETYPE_TABSTOPS['javascript'] == 2
    assert DEFAULT_FILETYPE_TABSTOPS['typescript'] == 2
    assert DEFAULT_FILETYPE_TABSTOPS['html'] == 2
    assert DEFAULT_FILETYPE_TABSTOPS['css'] == 2
    assert DEFAULT_FILETYPE_TABSTOPS['json'] == 2
    assert DEFAULT_FILETYPE_TABSTOPS['yaml'] == 2
    assert DEFAULT_FILETYPE_TABSTOPS['bash'] == 2
    assert DEFAULT_FILETYPE_TABSTOPS['markdown'] == 2

    # 4 spaces: standard programming languages
    assert DEFAULT_FILETYPE_TABSTOPS['python'] == 4
    assert DEFAULT_FILETYPE_TABSTOPS['rust'] == 4
    assert DEFAULT_FILETYPE_TABSTOPS['go'] == 4
    assert DEFAULT_FILETYPE_TABSTOPS['c'] == 4
    assert DEFAULT_FILETYPE_TABSTOPS['cpp'] == 4
    assert DEFAULT_FILETYPE_TABSTOPS['csharp'] == 4
    assert DEFAULT_FILETYPE_TABSTOPS['java'] == 4
    assert DEFAULT_FILETYPE_TABSTOPS['php'] == 4
    assert DEFAULT_FILETYPE_TABSTOPS['zig'] == 4
    assert DEFAULT_FILETYPE_TABSTOPS['swift'] == 4

    # 8 spaces: hard-tab formats
    assert DEFAULT_FILETYPE_TABSTOPS['make'] == 8
    assert DEFAULT_FILETYPE_TABSTOPS['gitcommit'] == 8


def test_filetype_tabstops_alias_synchronization(editor):
    ft = FiletypeTabstops(DEFAULT_FILETYPE_TABSTOPS)

    # Aliases resolve to canonical tabstop:
    assert ft['py'] == 4
    assert ft.get('py') == 4
    assert 'py' in ft
    assert ft['js'] == 2
    assert ft['ts'] == 2
    assert ft['c++'] == 4
    assert ft['yml'] == 2

    # Setting an alias updates canonical entry:
    ft['py'] = 8
    assert ft['python'] == 8
    assert ft['py'] == 8

    # Setting canonical entry updates alias:
    ft['python'] = 2
    assert ft['python'] == 2
    assert ft['py'] == 2

    # Custom unmapped filetypes:
    ft['mycustom'] = 3
    assert ft['mycustom'] == 3
    assert ft.get('unknown', 8) == 8


def test_editor_buffer_filetype_and_tabstop_detection(editor, tmp_path):
    eb_py = _load_file(editor, tmp_path, 'test.py', 'print("hi")\n')
    assert eb_py.filetype == 'python'
    assert eb_py.tabstop == 4

    eb_js = _load_file(editor, tmp_path, 'test.js', 'console.log(1);\n')
    assert eb_js.filetype == 'javascript'
    assert eb_js.tabstop == 2

    eb_html = _load_file(editor, tmp_path, 'test.html', '<div></div>\n')
    assert eb_html.filetype == 'html'
    assert eb_html.tabstop == 2

    eb_c = _load_file(editor, tmp_path, 'test.c', 'int main() {}\n')
    assert eb_c.filetype == 'c'
    assert eb_c.tabstop == 4

    eb_make = _load_file(editor, tmp_path, 'Makefile', 'all:\n\techo 1\n')
    assert eb_make.filetype == 'make'
    assert eb_make.tabstop == 8

    eb_txt = _load_file(editor, tmp_path, 'pentax.txt', 'some text\n')
    assert eb_txt.tabstop == 8


def test_shebang_filetype_detection(editor, tmp_path):
    eb_script = _load_file(editor, tmp_path, 'my_script', '#!/usr/bin/env python\nprint(1)\n')
    assert eb_script.filetype == 'python'
    assert eb_script.tabstop == 4


def test_set_filetype_changes_tabstop(editor, tmp_path):
    eb = _load_file(editor, tmp_path, 'notes.txt', 'hello\n')
    assert eb.tabstop == 8

    handle_command(editor, ':set filetype=python')
    assert eb.filetype == 'python'
    assert eb.tabstop == 4

    handle_command(editor, ':set ft=html')
    assert eb.filetype == 'html'
    assert eb.tabstop == 2

    handle_command(editor, ':set ft=none')
    assert eb.filetype == 'text'
    assert eb.tabstop == 8


def test_setlocal_tabstop_command(editor, tmp_path):
    eb1 = _load_file(editor, tmp_path, 'a.py', 'pass\n')
    eb2 = _load_file(editor, tmp_path, 'b.py', 'pass\n')

    # Both start with python tabstop 4:
    assert eb1.tabstop == 4
    assert eb2.tabstop == 4
    assert editor.tabstop == 8

    # Set local tabstop on eb2 only:
    handle_command(editor, ':setlocal tabstop=2')
    assert eb2.tabstop == 2
    assert eb1.tabstop == 4
    assert editor.tabstop == 8

    # Query local tabstop:
    handle_command(editor, ':setlocal tabstop')
    assert editor.message == 'tabstop=2'
    handle_command(editor, ':setl ts')
    assert editor.message == 'tabstop=2'

    # Short command alias :setl ts=3:
    handle_command(editor, ':setl ts=3')
    assert eb2.tabstop == 3

    # Validation:
    handle_command(editor, ':setlocal ts=0')
    assert editor.message == 'Argument must be positive'
    assert eb2.tabstop == 3


def test_set_tabstop_updates_global_and_active_buffer(editor, tmp_path):
    eb = _load_file(editor, tmp_path, 'doc.txt', 'hello\n')
    assert eb.tabstop == 8

    handle_command(editor, ':set tabstop=4')
    assert editor.tabstop == 4
    assert eb.tabstop == 4

    handle_command(editor, ':set ts=6')
    assert editor.tabstop == 6
    assert eb.tabstop == 6


def test_custom_filetype_tabstops_in_config(editor, tmp_path):
    editor.filetype_tabstops['markdown'] = 4
    editor.filetype_tabstops['c'] = 8

    eb_md = _load_file(editor, tmp_path, 'README.md', '# title\n')
    assert eb_md.tabstop == 4

    eb_c = _load_file(editor, tmp_path, 'main.c', 'int main() {}\n')
    assert eb_c.tabstop == 8


def test_indentation_bindings_use_buffer_tabstop(editor, tmp_path):
    eb = _load_file(editor, tmp_path, 'test.js', 'line 1\nline 2\n')
    assert eb.tabstop == 2
    editor.expand_tab = True

    # Test _insert_indentation:
    eb.buffer.cursor_position = 0
    _insert_indentation(editor, eb.buffer)
    assert eb.buffer.text.startswith('  line 1')

    # Test _shift_lines >> and <<:
    _shift_lines(editor, eb.buffer, count=1, indent=True)
    assert eb.buffer.document.lines[0].startswith('    line 1')

    _shift_lines(editor, eb.buffer, count=1, indent=False)
    assert eb.buffer.document.lines[0].startswith('  line 1')


def test_preview_setlocal_tabstop(editor, tmp_path):
    eb = _load_file(editor, tmp_path, 'test.py', 'pass\n')
    assert eb.tabstop == 4

    previewer = CommandPreviewer(editor)
    previewer.preview('setlocal ts=2')
    assert eb.tabstop == 2

    # Esc / restore:
    previewer.restore()
    assert eb.tabstop == 4


def test_preview_set_filetype(editor, tmp_path):
    eb = _load_file(editor, tmp_path, 'test.txt', 'pass\n')
    assert eb.tabstop == 8

    previewer = CommandPreviewer(editor)
    previewer.preview('set ft=python')
    assert eb.filetype == 'python'
    assert eb.tabstop == 4

    previewer.restore()
    assert eb.filetype == 'text'
    assert eb.tabstop == 8
