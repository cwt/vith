"""
Tests for TOML configuration and modular Python hook execution.
"""

from __future__ import annotations

import os

import pytest

from vith.rc_file import (
    apply_toml_config,
    find_config_file,
    run_hook_file,
    run_rc_file,
)


def test_find_config_file_order(monkeypatch: pytest.MonkeyPatch, tmp_path):
    xdg_file = tmp_path / "config" / "vith" / "config.toml"
    vith_dir_file = tmp_path / ".vith" / "config.toml"
    vithrc_file = tmp_path / ".vithrc"
    pyvimrc_file = tmp_path / ".pyvimrc"

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(os.path, "expanduser", lambda p: p.replace("~", str(tmp_path)))

    # None exist initially:
    assert find_config_file() is None

    # pyvimrc fallback:
    pyvimrc_file.write_text("def configure(e): pass\n")
    assert find_config_file() == str(pyvimrc_file)

    # vithrc overrides pyvimrc:
    vithrc_file.write_text("[editor]\ntabstop = 4\n")
    assert find_config_file() == str(vithrc_file)

    # ~/.vith/config.toml overrides ~/.vithrc:
    vith_dir_file.parent.mkdir(parents=True, exist_ok=True)
    vith_dir_file.write_text("[editor]\ntabstop = 4\n")
    assert find_config_file() == str(vith_dir_file)

    # ~/.config/vith/config.toml has highest priority:
    xdg_file.parent.mkdir(parents=True, exist_ok=True)
    xdg_file.write_text("[editor]\ntabstop = 2\n")
    assert find_config_file() == str(xdg_file)


def test_apply_toml_config_editor_table(editor):
    data = {
        "editor": {
            "number": False,
            "relativenumber": True,
            "hlsearch": False,
            "incsearch": False,
            "ignorecase": True,
            "paste": True,
            "ruler": False,
            "wildmenu": False,
            "expandtab": False,
            "tabstop": 6,
            "scrolloff": 5,
            "list": False,
            "wrap": False,
            "breakindent": True,
            "mouse": False,
            "jedi": False,
            "cursorline": True,
            "cursorcolumn": True,
            "colorcolumn": [80, 100],
            "colorscheme": "default",
            "cold_text_compression": "zlib",
            "cold_text_compression_level": 3,
        }
    }
    apply_toml_config(editor, data)

    assert editor.show_line_numbers is False
    assert editor.relative_number is True
    assert editor.highlight_search is False
    assert editor.incsearch is False
    assert editor.ignore_case is True
    assert editor.paste_mode is True
    assert editor.show_ruler is False
    assert editor.show_wildmenu is False
    assert editor.expand_tab is False
    assert editor.tabstop == 6
    assert editor.scroll_offset == 5
    assert editor.display_unprintable_characters is False
    assert editor.wrap_lines is False
    assert editor.break_indent is True
    assert editor.enable_mouse_support is False
    assert editor.enable_jedi is False
    assert editor.cursorline is True
    assert editor.cursorcolumn is True
    assert editor.colorcolumn == [80, 100]
    assert editor.cold_text_compression == "zlib"
    assert editor.cold_text_compression_level == 3


def test_apply_toml_config_colorcolumn_variants(editor):
    apply_toml_config(editor, {"editor": {"colorcolumn": 80}})
    assert editor.colorcolumn == [80]

    apply_toml_config(editor, {"editor": {"colorcolumn": "80, 120"}})
    assert editor.colorcolumn == [80, 120]

    apply_toml_config(editor, {"editor": {"colo": "emacs"}})
    assert editor.current_style is not None


def test_apply_toml_config_top_level_options(editor):
    data = {
        "number": False,
        "tabstop": 4,
        "expandtab": True,
    }
    apply_toml_config(editor, data)
    assert editor.show_line_numbers is False
    assert editor.tabstop == 4
    assert editor.expand_tab is True


def test_apply_toml_config_tabstops(editor):
    data = {
        "tabstop": {
            "python": 4,
            "javascript": 2,
            "c": 8,
            "markdown": 4,
        }
    }
    apply_toml_config(editor, data)
    assert editor.filetype_tabstops["python"] == 4
    assert editor.filetype_tabstops["py"] == 4
    assert editor.filetype_tabstops["javascript"] == 2
    assert editor.filetype_tabstops["js"] == 2
    assert editor.filetype_tabstops["c"] == 8
    assert editor.filetype_tabstops["markdown"] == 4


def test_run_hook_file(editor, tmp_path):
    hook_file = tmp_path / "my_hook.py"
    hook_file.write_text(
        "def configure(e):\n"
        "    e.custom_hook_ran = True\n"
    )

    assert run_hook_file(editor, hook_file) is True
    assert getattr(editor, "custom_hook_ran", False) is True

    # Missing hook:
    assert run_hook_file(editor, tmp_path / "non_existent.py") is False

    # Broken hook (fails gracefully without raising):
    broken_hook = tmp_path / "broken.py"
    broken_hook.write_text("raise ValueError('oops')\n")
    assert run_hook_file(editor, broken_hook) is False


def test_toml_registered_hooks_executed(editor, tmp_path):
    config_dir = tmp_path / "vith"
    hooks_dir = config_dir / "hooks"
    hooks_dir.mkdir(parents=True)

    hook1 = hooks_dir / "setup_keys.py"
    hook1.write_text(
        "def configure(e):\n"
        "    e.hook1_active = True\n"
    )

    hook2 = hooks_dir / "unregistered.py"
    hook2.write_text(
        "def configure(e):\n"
        "    e.hook2_active = True\n"
    )

    config_file = config_dir / "config.toml"
    config_file.write_text(
        "[editor]\n"
        "tabstop = 4\n"
        "\n"
        "[hooks]\n"
        'enabled = ["setup_keys.py"]\n'
    )

    run_rc_file(editor, config_file)

    assert editor.tabstop == 4
    assert getattr(editor, "hook1_active", False) is True
    # Unregistered hook was never executed:
    assert getattr(editor, "hook2_active", False) is False


def test_run_rc_file_toml_in_vithrc(editor, tmp_path):
    rc_file = tmp_path / ".vithrc"
    rc_file.write_text(
        "[editor]\n"
        "tabstop = 2\n"
        "number = false\n"
        "\n"
        "[tabstop]\n"
        "markdown = 4\n"
    )

    run_rc_file(editor, rc_file)
    assert editor.tabstop == 2
    assert editor.show_line_numbers is False
    assert editor.filetype_tabstops["markdown"] == 4


def test_run_rc_file_legacy_python_in_vithrc(editor, tmp_path):
    rc_file = tmp_path / ".vithrc"
    rc_file.write_text(
        "def configure(editor):\n"
        "    editor.tabstop = 3\n"
        "    editor.show_line_numbers = False\n"
    )

    run_rc_file(editor, rc_file)
    assert editor.tabstop == 3
    assert editor.show_line_numbers is False


def test_run_rc_file_invalid_toml_file(editor, tmp_path):
    rc_file = tmp_path / "config.toml"
    rc_file.write_text("[editor\ninvalid toml syntax")

    # Does not raise, prints error:
    run_rc_file(editor, rc_file)


def test_run_rc_file_missing_file(editor, tmp_path):
    # Does not raise, prints error:
    run_rc_file(editor, tmp_path / "does_not_exist.toml")
