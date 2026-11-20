"""Regressions for the Round 9 deduplication and logic-drift inventory (docs/bugs/152.md)."""

from __future__ import annotations

import os
from types import SimpleNamespace

from vith.commands.handler import handle_command
from vith.commands.options import (
    SET_INT_OPTIONS,
    SET_LIST_OPTIONS,
    SET_TOGGLE_GROUPS,
    set_options_lines,
)
from vith.commands.preview import CommandPreviewer
from vith.commands.ranges import resolve_insert_address, resolve_line_address
from vith.commands.substitution import resolve_ignore_case
from vith.commands.substitution import should_ignore_case as sub_should_ignore_case
from vith.editor_buffer import is_same_write_target, normalize_write_target
from vith.key_bindings import _get_buffer_shiftwidth, _get_buffer_tabstop, _resolve_editor_buffer
from vith.rc_file import _CONFIG_OPTION_MAP, _TOML_DETECTION_KEYS, apply_toml_config, run_rc_file

# --- 152 #1: _default_options derived from :set catalog ----------------------


def test_default_options_covers_full_set_catalog_and_copies_lists(editor):
    expected_attrs = {attr for _d, attr, _on, _off in SET_TOGGLE_GROUPS}
    expected_attrs |= {attr for _d, attr, _names, _min in SET_INT_OPTIONS}
    expected_attrs |= {attr for _d, attr, _names in SET_LIST_OPTIONS}
    expected_attrs.add('fileformat')

    assert expected_attrs == set(editor._default_options.keys())

    # List defaults must be shallow-copied so mutating `editor.colorcolumn` does
    # not corrupt the baseline.
    editor.colorcolumn.append(80)
    assert editor._default_options['colorcolumn'] == []


# --- 152 #2: _CONFIG_OPTION_MAP derived from :set catalog --------------------


def test_toml_config_option_map_supports_catalog_aliases_and_safe_vithrc_detection(
    editor, tmp_path
):
    # All short aliases from the :set catalog are mapped cleanly.
    for short_key, expected_attr in (
        ('nu', 'show_line_numbers'),
        ('rnu', 'relative_number'),
        ('hls', 'highlight_search'),
        ('ic', 'ignore_case'),
        ('scs', 'smartcase'),
        ('ai', 'autoindent'),
        ('ts', 'tabstop'),
        ('sw', 'shiftwidth'),
        ('so', 'scroll_offset'),
        ('cc', 'colorcolumn'),
        ('ff', 'fileformat'),
    ):
        assert _CONFIG_OPTION_MAP[short_key] == expected_attr

    # 2-char aliases stay out of bare `.vithrc` TOML auto-detection so legacy
    # Python rc files using short top-level variables still run as Python.
    for short_key in ('ts', 'sw', 'so', 'nu', 'ai', 'cc', 'ff', 'ic', 'is'):
        assert short_key not in _TOML_DETECTION_KEYS

    legacy_rc = tmp_path / '.vithrc'
    legacy_rc.write_text('ts = 3\ndef configure(editor):\n    editor.tabstop = ts\n')
    assert run_rc_file(editor, legacy_rc) is True
    assert editor.tabstop == 3

    # Canonical names take precedence over short aliases when both are present,
    # and a `[tabstop]` table does not trigger a bogus int-option error.
    errors = []
    ok = apply_toml_config(
        editor,
        {
            'editor': {'ts': 2, 'tabstop': 6, 'cc': 72, 'colorcolumn': [80, 100], 'nu': False},
            'tabstop': {'python': 4},
        },
        error_handler=errors.append,
    )
    assert ok is True
    assert errors == []
    assert editor.tabstop == 6
    assert editor.colorcolumn == [80, 100]
    assert editor.show_line_numbers is False
    assert editor.filetype_tabstops['python'] == 4


# --- 152 #3: shared buffer lookup, shared int setter, raw :set sw? -----------


def test_tabstop_shiftwidth_helpers_preserve_raw_sw_query_and_local_preview(editor):
    editor.load_initial_files([None])
    eb = editor.window_arrangement.active_editor_buffer
    editor.sync_with_prompt_toolkit()

    assert _resolve_editor_buffer(editor, eb.buffer) is eb
    assert _get_buffer_tabstop(editor, eb.buffer) == 8
    assert _get_buffer_shiftwidth(editor, eb.buffer) == 8

    # Raw `:set sw?` must report 0 (not effective 8) and bare `:set` must not
    # flag default `shiftwidth=0` as modified.
    handle_command(editor, ':set sw?')
    assert editor.message == 'shiftwidth=0'
    assert not any(line.startswith('shiftwidth=') for line in set_options_lines(editor))

    # Preview `:setlocal ts=2` mutates only the active buffer override and
    # restores cleanly on abort.
    previewer = CommandPreviewer(editor)
    previewer.save()
    previewer.preview(':setlocal ts=2')
    assert eb.tabstop == 2
    assert editor.tabstop == 8
    previewer.restore()
    assert eb.tabstop == 8
    assert editor.tabstop == 8


# --- 152 #4: shared 1-based address resolver ---------------------------------


def test_address_resolvers_share_parsing_and_preserve_zero_offset_semantics():
    # Bare `0` maps to row 0 for line addresses (`:0s`) and insertion index 0.
    assert resolve_line_address('0', 2, 5, None) == (0, None)
    assert resolve_insert_address('0', 2, 5, None) == (0, None)

    # Offset from `0`: `0+1` -> line 1 (row 0 / insert 1); `0-1` -> invalid.
    assert resolve_line_address('0+1', 2, 5, None) == (0, None)
    assert resolve_insert_address('0+1', 2, 5, None) == (1, None)
    assert resolve_line_address('0-1', 2, 5, None) == (-2, None)
    assert resolve_insert_address('0-1', 2, 5, None) == (
        None,
        'E19: No such line in current buffer',
    )

    # Marks and relative offsets match across both resolvers.
    assert resolve_line_address("'<+1", 0, 5, (1, 3)) == (2, None)
    assert resolve_insert_address("'<+1", 0, 5, (1, 3)) == (3, None)
    assert resolve_insert_address("'>", 0, 5, None) == (None, 'E20: Mark not set')


# --- 152 #5: unified should_ignore_case fallback -----------------------------


def test_should_ignore_case_fallback_matches_editor_smartcase_for_escapes(editor):
    editor.ignore_case = True
    editor.smartcase = True
    stub = SimpleNamespace(ignore_case=True, smartcase=True)

    for pattern, expected in (
        (r'\S\W', True),
        ('[A-Z]foo', True),
        (r'[A\]B]', True),
        ('Foo', False),
        (r'\[A\]', False),
        ('lower', True),
    ):
        assert editor.should_ignore_case(pattern) is expected, pattern
        assert sub_should_ignore_case(editor, pattern) is expected, pattern
        assert sub_should_ignore_case(stub, pattern) is expected, pattern
        assert resolve_ignore_case(True, True, pattern) is expected, pattern


# --- 152 #6: unified write-target normalization & :set ro symlink guard ------


def test_write_via_symlink_or_relative_path_enforces_readonly_and_clears_dirty(
    editor, tmp_path, monkeypatch
):
    real_file = tmp_path / 'real.txt'
    link_file = tmp_path / 'link.txt'
    real_file.write_text('original\n')
    os.symlink(real_file, link_file)

    editor.load_initial_files([str(real_file)])
    eb = editor.window_arrangement.active_editor_buffer
    eb.buffer.text = 'edited'
    assert eb.has_unsaved_changes is True

    # `:set ro` must block `:w <symlink>` and `:w ./real.txt` without `!`.
    handle_command(editor, ':set ro')
    handle_command(editor, f':w {link_file}')
    assert editor.message == "E45: 'readonly' option is set (add ! to override)"
    assert real_file.read_text() == 'original\n'
    assert eb.has_unsaved_changes is True

    monkeypatch.chdir(tmp_path)
    editor.message = None
    handle_command(editor, ':w ./real.txt')
    assert editor.message == "E45: 'readonly' option is set (add ! to override)"
    assert real_file.read_text() == 'original\n'

    # `:w! <symlink>` overrides readonly, writes the file, clears dirty state,
    # and keeps the buffer's canonical location unchanged.
    editor.message = None
    handle_command(editor, f':w! {link_file}')
    assert editor.message is None
    assert real_file.read_text() == 'edited\n'
    assert eb.has_unsaved_changes is False
    assert eb.location == str(real_file)

    # Normal writable `:w <symlink>` also clears dirty state cleanly.
    handle_command(editor, ':set noro')
    eb.buffer.text = 'edited again'
    assert eb.has_unsaved_changes is True
    handle_command(editor, f':w {link_file}')
    assert editor.message is None
    assert real_file.read_text() == 'edited again\n'
    assert eb.has_unsaved_changes is False
    assert eb.location == str(real_file)

    # `:sav <symlink>` renames the buffer to the requested path.
    handle_command(editor, f':sav {link_file}')
    assert editor.message is None
    assert eb.location == str(link_file)

    # Non-local URL targets are normalized cleanly without filesystem resolution.
    assert normalize_write_target('http://example.com/a') == 'http://example.com/a'
    assert is_same_write_target('http://example.com/a', 'http://example.com/a') is True
    assert is_same_write_target('http://example.com/a', 'http://example.com/b') is False
