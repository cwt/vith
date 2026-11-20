"""Regressions for the low-severity Vim-semantics inventory (docs/bugs/147.md)."""

from __future__ import annotations

import os

from vith.commands.handler import handle_command


def test_unknown_colorscheme_reports_e185(editor) -> None:
    editor.message = None
    handle_command(editor, ':colo definitely-not-a-theme')
    assert editor.message == 'E185: Cannot find color scheme definitely-not-a-theme'

    editor.message = None
    handle_command(editor, ':colo default')
    assert editor.message is None


def test_file_command_normalizes_location(editor, tmp_path) -> None:
    editor.load_initial_files([None])
    eb = editor.window_arrangement.active_editor_buffer
    target = tmp_path / 'renamed.txt'
    target.write_text('x\n')

    handle_command(editor, ':f %s' % target)
    assert eb.location == os.path.abspath(str(target))
    assert eb.is_new is False
    assert eb.isdir is False


def test_append_write_uses_io_backend(editor) -> None:
    # A custom backend proves :w >> no longer bypasses editor.io_backends.
    storage: dict[str, str] = {'mem://append': 'first\n'}

    class MemoryIO:
        def can_open_location(self, location):
            return location in storage

        def exists(self, location):
            return location in storage

        def isdir(self, location):
            return False

        def read(self, location):
            return storage[location], 'utf-8'

        def write(self, location, text, encoding='utf-8'):
            storage[location] = text

    editor.io_backends.insert(0, MemoryIO())
    editor.load_initial_files([None])
    editor.window_arrangement.active_editor_buffer.buffer.text = 'one\ntwo'
    editor.message = None

    handle_command(editor, ':2w >> mem://append')

    assert editor.message is None
    assert storage['mem://append'] == 'first\ntwo\n'


def test_named_registers_for_delete_yank_put(editor) -> None:
    editor.load_initial_files([None])
    eb = editor.window_arrangement.active_editor_buffer
    eb.buffer.text = 'alpha\nbeta\ngamma'
    editor.sync_with_prompt_toolkit()

    handle_command(editor, ':2y a')
    assert editor.registers['a'] == 'beta'
    handle_command(editor, ':1pu a')
    assert eb.buffer.document.lines == ['alpha', 'beta', 'beta', 'gamma']

    handle_command(editor, ':1d 3')
    assert editor.registers['3'] == 'alpha'
    assert eb.buffer.document.lines == ['beta', 'beta', 'gamma']

    editor.message = None
    handle_command(editor, ':1pu z')
    assert editor.message == 'E353: Nothing in register z'


def test_setlocal_rejects_non_local_options(editor) -> None:
    editor.load_initial_files([None])
    eb = editor.window_arrangement.active_editor_buffer
    editor.show_line_numbers = True

    editor.message = None
    handle_command(editor, ':setlocal number')
    assert editor.message is not None
    assert 'not a buffer-local option' in editor.message
    assert editor.show_line_numbers is True

    editor.message = None
    handle_command(editor, ':setlocal ts=3')
    assert editor.message is None
    assert eb.tabstop == 3


def test_smartcase_ignores_regex_escapes(editor) -> None:
    editor.ignore_case = True
    editor.smartcase = True
    assert editor.should_ignore_case(r'\S\W') is True
    assert editor.should_ignore_case('[A-Z]foo') is True
    assert editor.should_ignore_case('Foo') is False
