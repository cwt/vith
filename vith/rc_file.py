"""
Configuration loading and hook execution.

Supports both modern declarative TOML configuration (~/.config/vith/config.toml)
with modular Python hook scripts, and legacy Python configuration files (~/.vithrc).
"""

from __future__ import annotations

import os
import traceback
from typing import TYPE_CHECKING, Any

from .commands.commands import press_enter_to_continue
from .editor import Editor

if TYPE_CHECKING:
    pass

try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ImportError:
        tomllib = None  # type: ignore[assignment]

__all__ = (
    'apply_toml_config',
    'find_config_file',
    'run_hook_file',
    'run_rc_file',
)

# Alias preserved for callers/tests referencing the module-private name.
_press_enter_to_continue = press_enter_to_continue

#: Map from configuration key (Vim name or Python attribute) to Editor attribute name.
_CONFIG_OPTION_MAP: dict[str, str] = {
    # Boolean toggle options:
    'number': 'show_line_numbers',
    'show_line_numbers': 'show_line_numbers',
    'relativenumber': 'relative_number',
    'relative_number': 'relative_number',
    'hlsearch': 'highlight_search',
    'highlight_search': 'highlight_search',
    'incsearch': 'incsearch',
    'ignorecase': 'ignore_case',
    'ignore_case': 'ignore_case',
    'paste': 'paste_mode',
    'paste_mode': 'paste_mode',
    'ruler': 'show_ruler',
    'show_ruler': 'show_ruler',
    'wildmenu': 'show_wildmenu',
    'show_wildmenu': 'show_wildmenu',
    'expandtab': 'expand_tab',
    'expand_tab': 'expand_tab',
    'list': 'display_unprintable_characters',
    'display_unprintable_characters': 'display_unprintable_characters',
    'wrap': 'wrap_lines',
    'wrap_lines': 'wrap_lines',
    'breakindent': 'break_indent',
    'break_indent': 'break_indent',
    'mouse': 'enable_mouse_support',
    'enable_mouse_support': 'enable_mouse_support',
    'jedi': 'enable_jedi',
    'enable_jedi': 'enable_jedi',
    'cursorline': 'cursorline',
    'cursorcolumn': 'cursorcolumn',
    # Integer and string options:
    'tabstop': 'tabstop',
    'scrolloff': 'scroll_offset',
    'scroll_offset': 'scroll_offset',
    'cold_text_compression': 'cold_text_compression',
    'cold_text_compression_level': 'cold_text_compression_level',
}


def find_config_file() -> str | None:
    """
    Find the user configuration file in standard locations.

    Search order:
    1. $XDG_CONFIG_HOME/vith/config.toml (or ~/.config/vith/config.toml)
    2. ~/.vith/config.toml
    3. ~/.vithrc (TOML or Python)
    4. ~/.pyvimrc (legacy fallback)
    """
    xdg_home = os.environ.get('XDG_CONFIG_HOME')
    if xdg_home:
        xdg_config = os.path.join(os.path.expanduser(xdg_home), 'vith', 'config.toml')
    else:
        xdg_config = os.path.expanduser('~/.config/vith/config.toml')

    candidates = [
        xdg_config,
        os.path.expanduser('~/.vith/config.toml'),
        os.path.expanduser('~/.vithrc'),
        os.path.expanduser('~/.pyvimrc'),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return None


def _resolve_hook_path(hook_name: str, config_path: str | None = None, custom_dir: str | None = None) -> str | None:
    """
    Resolve a hook script filename or path to an absolute path on disk.

    Search locations:
    1. Direct absolute/relative path if it exists
    2. Relative to config file directory (e.g. <config_dir>/hooks/<name>)
    3. Custom hooks directory if specified
    4. ~/.config/vith/hooks/<name>
    5. ~/.vith/hooks/<name>
    """
    expanded = os.path.expanduser(hook_name)
    if os.path.isabs(expanded) and os.path.exists(expanded):
        return os.path.abspath(expanded)
    if os.path.exists(expanded):
        return os.path.abspath(expanded)

    search_dirs: list[str] = []
    if config_path:
        config_dir = os.path.dirname(os.path.abspath(os.path.expanduser(config_path)))
        if custom_dir:
            search_dirs.append(os.path.join(config_dir, custom_dir))
        search_dirs.append(os.path.join(config_dir, 'hooks'))
        search_dirs.append(config_dir)

    xdg_home = os.environ.get('XDG_CONFIG_HOME')
    if xdg_home:
        search_dirs.append(os.path.join(os.path.expanduser(xdg_home), 'vith', 'hooks'))
    search_dirs.append(os.path.expanduser('~/.config/vith/hooks'))
    search_dirs.append(os.path.expanduser('~/.vith/hooks'))

    for directory in search_dirs:
        candidate = os.path.join(directory, hook_name)
        if os.path.exists(candidate):
            return os.path.abspath(candidate)
        if not hook_name.endswith('.py'):
            candidate_py = candidate + '.py'
            if os.path.exists(candidate_py):
                return os.path.abspath(candidate_py)

    return None


def run_hook_file(editor: Editor, hook_path: str | os.PathLike) -> bool:
    """
    Execute a Python hook file for custom keybindings or callbacks.

    The hook file is executed in an isolated namespace containing `editor`.
    If a `configure` function is defined, it is called with `editor`.
    """
    resolved = os.path.expanduser(os.fspath(hook_path))
    if not os.path.exists(resolved):
        print('Hook file not found: %r' % resolved)
        _press_enter_to_continue()
        return False

    try:
        namespace: dict[str, Any] = {'editor': editor}
        with open(resolved, 'r', encoding='utf-8') as f:
            code = compile(f.read(), resolved, 'exec')
            exec(code, namespace, namespace)

        if 'configure' in namespace and callable(namespace['configure']):
            namespace['configure'](editor)
        return True
    except Exception:
        traceback.print_exc()
        _press_enter_to_continue()
        return False


def apply_toml_config(editor: Editor, toml_data: dict[str, Any], config_path: str | None = None) -> None:
    """
    Apply parsed TOML configuration to an Editor instance.

    Processes:
    - [editor] table or top-level editor options
    - [tabstop] / [filetype_tabstops] table for per-filetype tabstops
    - [hooks] table for registering and loading enabled Python hook scripts
    """
    editor_table: dict[str, Any] = {}
    if isinstance(toml_data.get('editor'), dict):
        editor_table.update(toml_data['editor'])

    # Merge top-level options for flexibility:
    for key, value in toml_data.items():
        if key in _CONFIG_OPTION_MAP or key in ('colorscheme', 'colo', 'colorcolumn'):
            editor_table.setdefault(key, value)

    # Apply mapped editor options:
    for config_key, attr_name in _CONFIG_OPTION_MAP.items():
        if config_key in editor_table:
            setattr(editor, attr_name, editor_table[config_key])

    # Apply colorscheme:
    if 'colorscheme' in editor_table:
        editor.use_colorscheme(str(editor_table['colorscheme']))
    elif 'colo' in editor_table:
        editor.use_colorscheme(str(editor_table['colo']))

    # Apply colorcolumn:
    if 'colorcolumn' in editor_table:
        val = editor_table['colorcolumn']
        if isinstance(val, list):
            editor.colorcolumn = [int(x) for x in val]
        elif isinstance(val, str):
            editor.colorcolumn = [int(x.strip()) for x in val.split(',') if x.strip()]
        elif isinstance(val, int):
            editor.colorcolumn = [val]

    # Apply per-filetype tabstops:
    tabstop_table = toml_data.get('tabstop') or toml_data.get('filetype_tabstops')
    if isinstance(tabstop_table, dict):
        for ft, width in tabstop_table.items():
            try:
                editor.filetype_tabstops[str(ft)] = int(width)
            except (ValueError, TypeError):
                pass

    # Process and execute registered hooks:
    hooks_section = toml_data.get('hooks')
    enabled_hooks: list[str] = []
    custom_dir: str | None = None
    if isinstance(hooks_section, dict):
        custom_dir = hooks_section.get('directory')
        enabled = hooks_section.get('enabled') or hooks_section.get('hooks')
        if isinstance(enabled, list):
            enabled_hooks.extend(str(h) for h in enabled)
    elif isinstance(hooks_section, list):
        enabled_hooks.extend(str(h) for h in hooks_section)

    for hook_name in enabled_hooks:
        hook_path = _resolve_hook_path(hook_name, config_path=config_path, custom_dir=custom_dir)
        if hook_path:
            run_hook_file(editor, hook_path)
        else:
            print('Hook file %r not found' % hook_name)
            _press_enter_to_continue()


def _run_python_rc_file(editor: Editor, rc_file: str, content: str) -> None:
    """Execute a legacy Python configuration file defining `configure(editor)`."""
    try:
        namespace: dict[str, Any] = {'editor': editor}
        code = compile(content, rc_file, 'exec')
        exec(code, namespace, namespace)

        if 'configure' in namespace and callable(namespace['configure']):
            namespace['configure'](editor)
    except Exception:
        traceback.print_exc()
        _press_enter_to_continue()


def run_rc_file(editor: Editor, rc_file: str | os.PathLike) -> None:
    """
    Run configuration file.

    Automatically detects TOML format vs legacy Python script. When the file
    ends in `.toml` or contains TOML table declarations, it is parsed and applied
    declaratively; otherwise it falls back to Python execution.
    """
    assert isinstance(editor, Editor)
    assert isinstance(rc_file, (str, os.PathLike))

    rc_file_str = os.path.expanduser(os.fspath(rc_file))

    if not os.path.exists(rc_file_str):
        print('Impossible to read %r' % rc_file_str)
        _press_enter_to_continue()
        return

    try:
        with open(rc_file_str, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception as e:
        print('Error reading %r: %s' % (rc_file_str, e))
        _press_enter_to_continue()
        return

    is_toml_file = rc_file_str.endswith('.toml')

    if is_toml_file:
        if tomllib is None:
            print('Error: tomllib is required to parse TOML configuration files.')
            _press_enter_to_continue()
            return
        try:
            data = tomllib.loads(content)
        except Exception as e:
            print('Error in TOML configuration file %r: %s' % (rc_file_str, e))
            _press_enter_to_continue()
            return
        apply_toml_config(editor, data, config_path=rc_file_str)
        return

    # For non-.toml files (e.g. ~/.vithrc): try parsing as TOML first:
    if tomllib is not None and content.strip():
        try:
            data = tomllib.loads(content)
            # If it parsed as TOML and contains recognized tables/options, apply as TOML:
            if (
                'editor' in data
                or 'tabstop' in data
                or 'filetype_tabstops' in data
                or 'hooks' in data
                or any(k in _CONFIG_OPTION_MAP for k in data)
            ):
                apply_toml_config(editor, data, config_path=rc_file_str)
                return
        except Exception:
            # Not valid TOML; fall through to Python
            pass

    # Fall back to legacy Python execution:
    _run_python_rc_file(editor, rc_file_str, content)
