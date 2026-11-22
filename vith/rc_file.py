"""
Configuration loading and hook execution.

Supports both modern declarative TOML configuration (~/.config/vith/config.toml)
with modular Python hook scripts, and legacy Python configuration files (~/.vithrc).
"""

from __future__ import annotations

import os
import traceback
from typing import TYPE_CHECKING, Any, Callable

from .commands import (
    SET_INT_OPTIONS,
    SET_LIST_OPTIONS,
    SET_TOGGLE_GROUPS,
    press_enter_to_continue,
)
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


def _build_config_option_map() -> dict[str, str]:
    """
    Derive configuration keys (short Vim aliases, canonical Vim option names,
    and Python attribute names) from the `:set` catalog, merged with TOML-only
    and extra options (docs/bugs/152.md). Short aliases are registered before
    canonical names so explicit long names take precedence if both appear.
    """
    mapping: dict[str, str] = {}
    for display, attr, on_names, _off_names in SET_TOGGLE_GROUPS:
        for alias in on_names:
            if alias not in (display, attr):
                mapping[alias] = attr
        mapping[display] = attr
        mapping[attr] = attr
    for display, attr, names, _minimum in SET_INT_OPTIONS:
        for alias in names:
            if alias not in (display, attr):
                mapping[alias] = attr
        mapping[display] = attr
        mapping[attr] = attr
    for display, attr, names in SET_LIST_OPTIONS:
        for alias in names:
            if alias not in (display, attr):
                mapping[alias] = attr
        mapping[display] = attr
        mapping[attr] = attr
    mapping.update(
        {
            'cold_text_compression': 'cold_text_compression',
            'cold_text_compression_level': 'cold_text_compression_level',
            'ff': 'fileformat',
            'fileformat': 'fileformat',
        }
    )
    return mapping


#: Map from configuration key (Vim name, alias, or Python attribute) to Editor attribute name.
_CONFIG_OPTION_MAP: dict[str, str] = _build_config_option_map()

# Derive the validation categories from the `:set` catalog so TOML and `:set`
# cannot drift (docs/bugs/138.md).
_TOGGLE_ATTRIBUTES = frozenset(attr for _display, attr, _on, _off in SET_TOGGLE_GROUPS)
_INT_ATTRIBUTE_MINIMUMS = {attr: minimum for _display, attr, _names, minimum in SET_INT_OPTIONS}
_MAX_OPTION_INT = 9999

# Exclude 2-character aliases (e.g. `ts`, `so`, `nu`, `ai`, `cc`) from bare
# `.vithrc` TOML auto-detection so legacy Python rc files with short top-level
# variables are not misclassified as TOML (docs/bugs/152.md).
_TOML_DETECTION_KEYS = frozenset(
    {key for key in _CONFIG_OPTION_MAP if len(key) >= 3}
    | {
        'editor',
        'tabstop',
        'filetype_tabstops',
        'hooks',
        'render',
        'colorscheme',
        'colo',
        'colorcolumn',
    }
)


def _parse_colorcolumn(value: Any) -> list[int]:
    """Parse a TOML `colorcolumn` value into a list of positive integers."""
    if isinstance(value, bool):
        raise ValueError('boolean is not a colorcolumn')
    if isinstance(value, int):
        numbers = [value]
    elif isinstance(value, str):
        numbers = [int(part.strip()) for part in value.split(',') if part.strip()]
    elif isinstance(value, (list, tuple)):
        numbers = [int(item) for item in value]
    else:
        raise ValueError('expected integer, string or list')
    if not numbers or any(number <= 0 for number in numbers):
        raise ValueError('colorcolumn values must be positive')
    return numbers


def _validated_option(
    editor: Editor,
    config_key: str,
    attr_name: str,
    value: Any,
    error_handler: Callable[[str], None] | None,
) -> Any | None:
    """
    Validate one TOML option value, reporting through `error_handler` (never
    raising) and returning the value to apply, or `None` when invalid.
    """
    if attr_name in _TOGGLE_ATTRIBUTES:
        if not isinstance(value, bool):
            _report_rc_error('Invalid value for %s: expected a boolean' % config_key, error_handler)
            return None
        return value

    if attr_name in _INT_ATTRIBUTE_MINIMUMS:
        minimum = _INT_ATTRIBUTE_MINIMUMS[attr_name]
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not minimum <= value <= _MAX_OPTION_INT
        ):
            _report_rc_error(
                'Invalid value for %s: expected an integer in %d..%d'
                % (config_key, minimum, _MAX_OPTION_INT),
                error_handler,
            )
            return None
        return value

    if attr_name == 'colorcolumn':
        try:
            return _parse_colorcolumn(value)
        except (TypeError, ValueError):
            _report_rc_error(
                'Invalid value for %s: expected positive integers' % config_key, error_handler
            )
            return None

    if attr_name == 'fileformat':
        fmt = str(value).strip().lower()
        if fmt not in ('dos', 'unix', 'mac'):
            _report_rc_error(
                'Invalid value for %s: expected dos, unix or mac' % config_key, error_handler
            )
            return None
        return fmt

    if attr_name == 'cold_text_compression' and not isinstance(value, str):
        # make_cold_text_compressor() calls .strip() on this value; a TOML
        # int/bool/list would crash the first buffer's construction
        # (docs/bugs/178.md). Unknown strings still fall back safely there.
        _report_rc_error('Invalid value for %s: expected a string' % config_key, error_handler)
        return None

    if attr_name == 'cold_text_compression_level' and (
        isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 22
    ):
        # zstd levels go up to 22; zlib/lzo clamp higher values themselves
        # (docs/bugs/188.md).
        _report_rc_error(
            'Invalid value for %s: expected an integer in 0..22' % config_key, error_handler
        )
        return None

    return value


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


def _resolve_hook_path(
    hook_name: str, config_path: str | None = None, custom_dir: str | None = None
) -> str | None:
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


def _report_rc_error(
    message: str,
    error_handler: Callable[[str], None] | None = None,
    *,
    exc_info: bool = False,
) -> None:
    """
    Report a configuration/hook error either via `error_handler` (in-TUI `:source`
    path, never invoking `input()`) or to stdout + `Press ENTER` at CLI startup.
    """
    if error_handler is not None:
        error_handler(message)
        return
    if exc_info:
        traceback.print_exc()
    else:
        print(message)
    _press_enter_to_continue()


def run_hook_file(
    editor: Editor,
    hook_path: str | os.PathLike,
    *,
    error_handler: Callable[[str], None] | None = None,
) -> bool:
    """
    Execute a Python hook file for custom keybindings or callbacks.

    The hook file is executed in an isolated namespace containing `editor`.
    If a `configure` function is defined, it is called with `editor`.
    """
    resolved = os.path.expanduser(os.fspath(hook_path))
    if not os.path.exists(resolved):
        _report_rc_error('Hook file not found: %r' % resolved, error_handler)
        return False

    try:
        namespace: dict[str, Any] = {'editor': editor}
        with open(resolved, 'r', encoding='utf-8') as f:
            code = compile(f.read(), resolved, 'exec')
            exec(code, namespace, namespace)

        if 'configure' in namespace and callable(namespace['configure']):
            namespace['configure'](editor)
        return True
    except Exception as exc:
        _report_rc_error(
            'Error in hook file %r: %s' % (resolved, exc), error_handler, exc_info=True
        )
        return False


def apply_toml_config(
    editor: Editor,
    toml_data: dict[str, Any],
    config_path: str | None = None,
    *,
    error_handler: Callable[[str], None] | None = None,
) -> bool:
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

    # Merge top-level options for flexibility (skip `[tabstop]` table dicts,
    # which configure per-filetype tabstops below):
    for key, value in toml_data.items():
        if key == 'tabstop' and isinstance(value, dict):
            continue
        if key in _CONFIG_OPTION_MAP or key in ('colorscheme', 'colo'):
            editor_table.setdefault(key, value)

    all_ok = True

    # Validate and apply mapped editor options. Invalid values are reported
    # (never raised) and skipped, leaving the previous value untouched.
    for config_key, attr_name in _CONFIG_OPTION_MAP.items():
        if config_key in editor_table:
            validated = _validated_option(
                editor, config_key, attr_name, editor_table[config_key], error_handler
            )
            if validated is None:
                all_ok = False
                continue
            setattr(editor, attr_name, validated)

    # Apply colorscheme:
    scheme = editor_table.get('colorscheme', editor_table.get('colo'))
    if scheme is not None:
        if not editor.use_colorscheme(str(scheme), report=False):
            _report_rc_error('E185: Cannot find color scheme %s' % scheme, error_handler)
            all_ok = False

    # Apply per-filetype tabstops:
    tabstop_table = toml_data.get('tabstop') or toml_data.get('filetype_tabstops')
    if isinstance(tabstop_table, dict):
        for ft, width in tabstop_table.items():
            try:
                editor.filetype_tabstops[str(ft)] = int(width)
            except (ValueError, TypeError):
                pass

    # Apply live `:render` preview configuration:
    render_section = toml_data.get('render')
    if render_section is not None:
        if not isinstance(render_section, dict):
            _report_rc_error('Invalid [render] section: expected a table', error_handler)
            all_ok = False
        else:
            if 'default_split' in render_section:
                raw_split = str(render_section['default_split']).strip().lower()
                if raw_split in ('v', 'vert', 'vertical'):
                    editor.render_default_split = 'vertical'
                elif raw_split in ('h', 'horiz', 'horizontal'):
                    editor.render_default_split = 'horizontal'
                else:
                    _report_rc_error(
                        'Invalid value for render.default_split: expected vertical or horizontal',
                        error_handler,
                    )
                    all_ok = False
            if 'debounce_ms' in render_section:
                raw_debounce = render_section['debounce_ms']
                if (
                    isinstance(raw_debounce, bool)
                    or not isinstance(raw_debounce, int)
                    or not 0 <= raw_debounce <= 10000
                ):
                    _report_rc_error(
                        'Invalid value for render.debounce_ms: expected an integer in 0..10000',
                        error_handler,
                    )
                    all_ok = False
                else:
                    editor.render_debounce_ms = raw_debounce
            if 'sync_scroll' in render_section:
                raw_sync = render_section['sync_scroll']
                if not isinstance(raw_sync, bool):
                    _report_rc_error(
                        'Invalid value for render.sync_scroll: expected a boolean',
                        error_handler,
                    )
                    all_ok = False
                else:
                    editor.render_sync_scroll = raw_sync
            if 'commands' in render_section:
                raw_cmds = render_section['commands']
                if not isinstance(raw_cmds, dict):
                    _report_rc_error(
                        'Invalid [render.commands] section: expected a table',
                        error_handler,
                    )
                    all_ok = False
                else:
                    for fmt_key, cmd_val in raw_cmds.items():
                        if not isinstance(cmd_val, str) or not cmd_val.strip():
                            _report_rc_error(
                                'Invalid value for render.commands.%s: expected a non-empty command string'
                                % fmt_key,
                                error_handler,
                            )
                            all_ok = False
                        else:
                            editor.render_commands[str(fmt_key).strip().lower()] = cmd_val.strip()

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
            if not run_hook_file(editor, hook_path, error_handler=error_handler):
                all_ok = False
        else:
            _report_rc_error('Hook file %r not found' % hook_name, error_handler)
            all_ok = False
    return all_ok


def _run_python_rc_file(
    editor: Editor,
    rc_file: str,
    content: str,
    *,
    error_handler: Callable[[str], None] | None = None,
) -> bool:
    """Execute a legacy Python configuration file defining `configure(editor)`."""
    try:
        namespace: dict[str, Any] = {'editor': editor}
        code = compile(content, rc_file, 'exec')
        exec(code, namespace, namespace)

        if 'configure' in namespace and callable(namespace['configure']):
            namespace['configure'](editor)
        return True
    except Exception as exc:
        _report_rc_error(
            'Error in configuration file %r: %s' % (rc_file, exc),
            error_handler,
            exc_info=True,
        )
        return False


def run_rc_file(
    editor: Editor,
    rc_file: str | os.PathLike,
    *,
    error_handler: Callable[[str], None] | None = None,
) -> bool:
    """
    Run configuration file.

    Automatically detects TOML format vs legacy Python script. When the file
    ends in `.toml` or contains TOML table declarations, it is parsed and applied
    declaratively; otherwise it falls back to Python execution.

    When `error_handler` is provided (e.g. from `:source`), errors are passed
    to it instead of printing to stdout and prompting via `input()`.
    """
    assert isinstance(editor, Editor)
    assert isinstance(rc_file, (str, os.PathLike))

    rc_file_str = os.path.expanduser(os.fspath(rc_file))

    if not os.path.exists(rc_file_str):
        _report_rc_error('Impossible to read %r' % rc_file_str, error_handler)
        return False

    try:
        with open(rc_file_str, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception as e:
        _report_rc_error('Error reading %r: %s' % (rc_file_str, e), error_handler)
        return False

    is_toml_file = rc_file_str.endswith('.toml')

    if is_toml_file:
        if tomllib is None:
            _report_rc_error(
                'Error: tomllib is required to parse TOML configuration files.',
                error_handler,
            )
            return False
        try:
            data = tomllib.loads(content)
        except Exception as e:
            _report_rc_error(
                'Error in TOML configuration file %r: %s' % (rc_file_str, e),
                error_handler,
            )
            return False
        return apply_toml_config(editor, data, config_path=rc_file_str, error_handler=error_handler)

    # For non-.toml files (e.g. ~/.vithrc): try parsing as TOML first:
    if tomllib is not None and content.strip():
        try:
            data = tomllib.loads(content)
            # If it parsed as TOML and contains recognized tables/options, apply as TOML:
            if any(key in _TOML_DETECTION_KEYS for key in data):
                return apply_toml_config(
                    editor, data, config_path=rc_file_str, error_handler=error_handler
                )
        except Exception:
            # Not valid TOML; fall through to Python
            pass

    # Fall back to legacy Python execution:
    return _run_python_rc_file(editor, rc_file_str, content, error_handler=error_handler)
