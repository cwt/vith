"""
`:set` / `:setlocal` option catalog, execution, querying, formatting,
`:colorscheme`, and `:nohlsearch`.
"""

from __future__ import annotations

import codecs
from typing import Any

from prompt_toolkit.filters import to_filter

from .common import invalidate_app
from .ranges import is_buffer_read_only
from .registry import SET_COMMANDS, SET_COMMANDS_TAKING_VALUE, _cmd, cmd, set_cmd

#: Canonical `:set` option catalog — the single source of truth for option
#: names, editor attributes and validation bounds. Both the generated
#: `:set` handlers below and the command-line previewer
#: (`vith/commands/preview.py`) are built from this table (docs/bugs/073.md
#: finalizing the 028/partial-073 Table-6 dedup).
#: (option, editor attribute, ON aliases, OFF aliases)
SET_TOGGLE_GROUPS = (
    ('number', 'show_line_numbers', ('nu', 'number'), ('nonu', 'nonumber')),
    ('hlsearch', 'highlight_search', ('hlsearch', 'hls'), ('nohlsearch', 'nohls')),
    ('paste', 'paste_mode', ('paste',), ('nopaste',)),
    ('ruler', 'show_ruler', ('ruler', 'ru'), ('noruler', 'noru')),
    ('wildmenu', 'show_wildmenu', ('wildmenu', 'wmnu'), ('nowildmenu', 'nowmnu')),
    ('expandtab', 'expand_tab', ('expandtab', 'et'), ('noexpandtab', 'noet')),
    ('incsearch', 'incsearch', ('incsearch', 'is'), ('noincsearch', 'nois')),
    ('ignorecase', 'ignore_case', ('ignorecase', 'ic'), ('noignorecase', 'noic')),
    ('smartcase', 'smartcase', ('smartcase', 'scs'), ('nosmartcase', 'noscs')),
    ('autoindent', 'autoindent', ('autoindent', 'ai'), ('noautoindent', 'noai')),
    ('list', 'display_unprintable_characters', ('list',), ('nolist',)),
    ('jedi', 'enable_jedi', ('jedi',), ('nojedi',)),
    ('relativenumber', 'relative_number', ('relativenumber', 'rnu'), ('norelativenumber', 'nornu')),
    ('wrap', 'wrap_lines', ('wrap',), ('nowrap',)),
    ('breakindent', 'break_indent', ('breakindent', 'bri'), ('nobreakindent', 'nobri')),
    ('mouse', 'enable_mouse_support', ('mouse',), ('nomouse',)),
    ('cursorline', 'cursorline', ('cursorline', 'cul'), ('nocursorline', 'nocul')),
    ('cursorcolumn', 'cursorcolumn', ('cursorcolumn', 'cuc'), ('nocursorcolumn', 'nocuc')),
)

#: (display name, editor attribute, aliases, minimum)
SET_INT_OPTIONS = (
    ('tabstop', 'tabstop', ('tabstop', 'ts'), 1),
    ('shiftwidth', 'shiftwidth', ('shiftwidth', 'sw'), 0),
    ('scrolloff', 'scroll_offset', ('scrolloff', 'so'), 0),
)

#: (display name, editor attribute, aliases) — comma-separated integer list.
SET_LIST_OPTIONS = (('colorcolumn', 'colorcolumn', ('colorcolumn', 'cc')),)

#: Boolean toggle aliases not stored directly as simple attributes on `Editor`.
SET_EXTRA_TOGGLE_ON_NAMES = ('tildeop', 'top', 'readonly', 'ro')
_EXTRA_TOGGLE_OFF_NAMES = ('notildeop', 'notop', 'noreadonly', 'noro')
_EXTRA_TOGGLE_ALL_NAMES = frozenset(SET_EXTRA_TOGGLE_ON_NAMES + _EXTRA_TOGGLE_OFF_NAMES)
_EXTRA_VALUE_OPTION_NAMES = frozenset(('filetype', 'ft', 'fileencoding', 'fenc'))

_TOGGLE_ON_MAP: dict[str, tuple[str, str]] = {}
_TOGGLE_OFF_MAP: dict[str, tuple[str, str]] = {}
_INT_OPTION_MAP: dict[str, tuple[str, str, int]] = {}
_LIST_OPTION_MAP: dict[str, tuple[str, str]] = {}

#: Options with real buffer-local semantics, accepted by `:setlocal`.
_LOCAL_SET_OPTION_NAMES = frozenset(
    (
        'tabstop',
        'ts',
        'shiftwidth',
        'sw',
        'filetype',
        'ft',
        'fileencoding',
        'fenc',
        'fileformat',
        'ff',
    )
)


def parse_set_tokens(variables: Any) -> list[tuple[str, str | None]]:
    """
    Extract `(option, value)` tokens from matched `:set` / `:setlocal`
    grammar variables (supporting bare `:set` and multi-argument `:set`).
    """
    first_opt = variables.get('set_option')
    if first_opt is None:
        return []
    tokens: list[tuple[str, str | None]] = [(first_opt, variables.get('set_value'))]
    rest = variables.get('set_rest')
    if rest:
        for raw_tok in rest.split():
            if '=' in raw_tok:
                opt_name, opt_val = raw_tok.split('=', 1)
                tokens.append((opt_name, opt_val))
            else:
                tokens.append((raw_tok, None))
    return tokens


def _get_int_option_value(editor: Any, attribute: str, eb: Any = None) -> int:
    """Read the effective value of an integer `:set` option (honoring buffer overrides)."""
    if eb is None and attribute in ('tabstop', 'shiftwidth'):
        eb = getattr(editor.window_arrangement, 'active_editor_buffer', None)
    if attribute == 'tabstop' and eb is not None:
        return int(eb.tabstop)
    if attribute == 'shiftwidth' and eb is not None:
        override = getattr(eb, '_shiftwidth_override', None)
        if override is not None:
            return int(override)
    return int(getattr(editor, attribute))


def apply_int_option_value(
    editor: Any,
    attribute: str,
    number: int,
    *,
    is_local: bool = False,
    require_buffer: bool = True,
) -> bool:
    """
    Assign integer option `attribute` to `editor` and/or the active buffer,
    shared by `:set`/`:setlocal` and `CommandPreviewer` (docs/bugs/152.md).
    """
    if attribute in ('tabstop', 'shiftwidth'):
        eb = getattr(editor.window_arrangement, 'active_editor_buffer', None)
        if is_local:
            if eb is None:
                if require_buffer:
                    editor.show_message('No buffer')
                return False
            setattr(eb, attribute, number)
        else:
            setattr(editor, attribute, number)
            if eb is not None:
                setattr(eb, attribute, number)
    else:
        setattr(editor, attribute, number)
    return True


def snapshot_default_options(editor: Any) -> dict[str, Any]:
    """
    Snapshot factory defaults for every cataloged `:set` option on `editor`
    (plus `fileformat`) for bare `:set` diffing (docs/bugs/152.md).
    """
    defaults: dict[str, Any] = {}
    for _display, attr, _on, _off in SET_TOGGLE_GROUPS:
        defaults[attr] = getattr(editor, attr)
    for _display, attr, _names, _min in SET_INT_OPTIONS:
        defaults[attr] = getattr(editor, attr)
    for _display, attr, _names in SET_LIST_OPTIONS:
        defaults[attr] = list(getattr(editor, attr))
    defaults['fileformat'] = getattr(editor, 'fileformat', 'unix')
    return defaults


def _set_int_option(
    editor: Any,
    value: str | None,
    attribute: str,
    display_name: str,
    minimum: int,
    is_local: bool = False,
) -> bool:
    """
    Shared implementation of the integer-valued `:set` handlers.

    `value is None` displays the current value; invalid input shows vim-like
    error messages; otherwise the attribute is set when `value >= minimum`.
    Returns `True` on success, `False` on validation/state error.
    """
    if value is None:
        current_value = _get_int_option_value(editor, attribute)
        editor.show_message('%s=%i' % (display_name, current_value))
        return True
    try:
        number = int(value)
    except ValueError:
        editor.show_message('Number required after =')
        return False
    if number < minimum:
        editor.show_message('Argument must be positive')
        return False
    if not apply_int_option_value(editor, attribute, number, is_local=is_local):
        return False
    invalidate_app()
    return True


def _set_list_option(editor: Any, value: str | None, attribute: str, display_name: str) -> bool:
    """Set or display a comma-separated integer list option (e.g. `colorcolumn`)."""
    if value is None:
        current = getattr(editor, attribute)
        formatted = ','.join(str(v) for v in current)
        editor.show_message(f'{display_name}={formatted}')
        return True
    try:
        if value:
            numbers = [int(val) for val in value.split(',')]
        else:
            numbers = []
    except ValueError:
        editor.show_message('Invalid value. Expecting comma separated list of integers')
        return False
    setattr(editor, attribute, numbers)
    return True


def _register_generated_set_options() -> None:
    """Materialize the `:set` catalog into `SET_COMMANDS` (docs/bugs/073.md)."""
    for display, attribute, on_names, off_names in SET_TOGGLE_GROUPS:

        def _on(editor: Any, _attribute: str = attribute) -> None:
            setattr(editor, _attribute, True)
            if _attribute == 'highlight_search':
                editor.clear_hlsearch_suppression()

        def _off(editor: Any, _attribute: str = attribute) -> None:
            setattr(editor, _attribute, False)

        for name in on_names:
            SET_COMMANDS[name] = _on
            _TOGGLE_ON_MAP[name] = (display, attribute)
        for name in off_names:
            SET_COMMANDS[name] = _off
            _TOGGLE_OFF_MAP[name] = (display, attribute)

    for display, attribute, names, minimum in SET_INT_OPTIONS:

        def _int_option(
            editor: Any,
            value: str | None,
            _display: str = display,
            _attribute: str = attribute,
            _minimum: int = minimum,
        ) -> None:
            _set_int_option(editor, value, _attribute, _display, _minimum)

        for name in names:
            SET_COMMANDS[name] = _int_option
            SET_COMMANDS_TAKING_VALUE.add(name)
            _INT_OPTION_MAP[name] = (display, attribute, minimum)

    for display, attribute, names in SET_LIST_OPTIONS:

        def _list_option(
            editor: Any,
            value: str | None,
            _attribute: str = attribute,
            _display: str = display,
        ) -> None:
            _set_list_option(editor, value, _attribute, _display)

        for name in names:
            SET_COMMANDS[name] = _list_option
            SET_COMMANDS_TAKING_VALUE.add(name)
            _LIST_OPTION_MAP[name] = (display, attribute)


_register_generated_set_options()


def _format_toggle_value(display: str, enabled: bool) -> str:
    return display if enabled else f'no{display}'


def _query_set_option(editor: Any, name: str) -> tuple[str | None, str | None]:
    """
    Format the current value of option `name` for `:set {opt}?` (or bare
    value-option query). Returns `(formatted, None)` or `(None, error)`.
    """
    toggle_info = _TOGGLE_ON_MAP.get(name) or _TOGGLE_OFF_MAP.get(name)
    if toggle_info is not None:
        display, attr = toggle_info
        return _format_toggle_value(display, bool(getattr(editor, attr))), None

    if name in ('tildeop', 'top', 'notildeop', 'notop'):
        enabled = bool(editor.application.vi_state.tilde_operator)
        return _format_toggle_value('tildeop', enabled), None

    if name in ('readonly', 'ro', 'noreadonly', 'noro'):
        eb = getattr(editor.window_arrangement, 'active_editor_buffer', None)
        if eb is None:
            return None, 'No buffer'
        return _format_toggle_value('readonly', is_buffer_read_only(eb.buffer)), None

    int_info = _INT_OPTION_MAP.get(name)
    if int_info is not None:
        display, attr, _min = int_info
        return f'{display}={_get_int_option_value(editor, attr)}', None

    list_info = _LIST_OPTION_MAP.get(name)
    if list_info is not None:
        display, attr = list_info
        val = getattr(editor, attr)
        return f"{display}={','.join(str(v) for v in val)}", None

    if name in ('filetype', 'ft'):
        eb = getattr(editor.window_arrangement, 'active_editor_buffer', None)
        if eb is None:
            return None, 'No buffer'
        return f'filetype={eb.filetype or ""}', None

    if name in ('fileencoding', 'fenc'):
        eb = getattr(editor.window_arrangement, 'active_editor_buffer', None)
        if eb is None:
            return None, 'No buffer'
        return f'fileencoding={eb.encoding or ""}', None

    return None, f'Unknown option: {name}'


def _append_set_query(editor: Any, name: str, queries: list[str]) -> bool:
    """Query option `name` and append to `queries`, or display the error."""
    text, err = _query_set_option(editor, name)
    if err is not None or text is None:
        editor.show_message(err or f'Unknown option: {name}')
        return False
    queries.append(text)
    return True


def _toggle_boolean_option(editor: Any, base_name: str) -> tuple[bool, bool]:
    """
    Invert boolean option `base_name` (used by `opt!` and `invopt`).
    Returns `(handled, ok)` where `ok=False` means an error was shown.
    """
    toggle_info = _TOGGLE_ON_MAP.get(base_name)
    if toggle_info is not None:
        _display, attr = toggle_info
        new_value = not bool(getattr(editor, attr))
        setattr(editor, attr, new_value)
        if attr == 'highlight_search' and new_value:
            editor.clear_hlsearch_suppression()
        return True, True
    if base_name in ('tildeop', 'top'):
        vi_state = editor.application.vi_state
        vi_state.tilde_operator = not bool(vi_state.tilde_operator)
        return True, True
    if base_name in ('readonly', 'ro'):
        eb = getattr(editor.window_arrangement, 'active_editor_buffer', None)
        if eb is None:
            editor.show_message('No buffer')
            return True, False
        eb.buffer.read_only = to_filter(not is_buffer_read_only(eb.buffer))
        return True, True
    return False, False


def set_options_lines(editor: Any, show_all: bool = False) -> list[str]:
    """
    Build formatted option lines for bare `:set` (changed from defaults) and
    `:set all` (all options), for display inside vith via `Editor.show_info`.
    """
    defaults = getattr(editor, '_default_options', {})
    eb = getattr(editor.window_arrangement, 'active_editor_buffer', None)
    entries: list[str] = []

    for display, attr, _on, _off in SET_TOGGLE_GROUPS:
        cur = bool(getattr(editor, attr))
        default_val = bool(defaults.get(attr, cur))
        if show_all or cur != default_val:
            entries.append(_format_toggle_value(display, cur))

    tilde_cur = bool(editor.application.vi_state.tilde_operator)
    if show_all or tilde_cur:
        entries.append(_format_toggle_value('tildeop', tilde_cur))

    ro_cur = is_buffer_read_only(eb.buffer) if eb is not None else False
    if show_all or ro_cur is not False:
        entries.append(_format_toggle_value('readonly', ro_cur))

    for display, attr, _names, _min in SET_INT_OPTIONS:
        cur_int = _get_int_option_value(editor, attr, eb=eb)
        default_int = int(defaults.get(attr, cur_int))
        if show_all or cur_int != default_int:
            entries.append(f'{display}={cur_int}')

    for display, attr, _names in SET_LIST_OPTIONS:
        cur_list = list(getattr(editor, attr))
        default_list = list(defaults.get(attr, []))
        if show_all or cur_list != default_list:
            entries.append(f"{display}={','.join(str(v) for v in cur_list)}")

    cur_ft = (eb.filetype or '') if eb is not None else ''
    if show_all or cur_ft != '':
        entries.append(f'filetype={cur_ft}')

    cur_fenc = (eb.encoding or 'utf-8') if eb is not None else 'utf-8'
    if show_all or cur_fenc != 'utf-8':
        entries.append(f'fileencoding={cur_fenc}')

    cur_ff = eb.fileformat if eb is not None else getattr(editor, 'fileformat', 'unix')
    default_ff = defaults.get('fileformat', 'unix')
    if show_all or cur_ff != default_ff:
        entries.append(f'fileformat={cur_ff}')

    entries.sort()
    lines = ['--- Options ---']
    if entries:
        lines.extend(entries)
    else:
        lines.append('(all options at default values)')
    return lines


def _reject_unexpected_value(editor: Any, opt: str, value: str | None) -> bool:
    if value is not None:
        editor.show_message(f'E474: Invalid argument: {opt}={value}')
        return True
    return False


def _apply_set_token(
    editor: Any,
    opt: str,
    value: str | None,
    *,
    is_local: bool,
    queries: list[str],
) -> bool:
    """Apply a single `:set` / `:setlocal` token; returns `False` on error."""
    if is_local and not opt.endswith('?') and opt not in _LOCAL_SET_OPTION_NAMES:
        # Only genuinely buffer-local options may be set with `:setlocal`
        # (docs/bugs/147.md); the rest would silently mutate global state.
        editor.show_message('E%i: %s is not a buffer-local option' % (518, opt))
        return False
    if opt == 'all':
        if _reject_unexpected_value(editor, opt, value):
            return False
        lines = set_options_lines(editor, show_all=True)
        editor.show_info('\n'.join(lines), title='[Options]')
        return True

    if opt.endswith('?'):
        if _reject_unexpected_value(editor, opt, value):
            return False
        return _append_set_query(editor, opt[:-1], queries)

    if opt.endswith('!'):
        if _reject_unexpected_value(editor, opt, value):
            return False
        base = opt[:-1]
        handled, ok = _toggle_boolean_option(editor, base)
        if handled:
            return ok
        if base in _INT_OPTION_MAP or base in _LIST_OPTION_MAP or base in _EXTRA_VALUE_OPTION_NAMES:
            editor.show_message(f'E474: Invalid argument: {opt}')
            return False
        editor.show_message(f'Unknown option: {opt}')
        return False

    if opt.startswith('inv') and opt not in SET_COMMANDS:
        base = opt[3:]
        if base in _TOGGLE_ON_MAP or base in SET_EXTRA_TOGGLE_ON_NAMES:
            if _reject_unexpected_value(editor, opt, value):
                return False
            _handled, ok = _toggle_boolean_option(editor, base)
            return ok
        editor.show_message(f'Unknown option: {opt}')
        return False

    if opt in _TOGGLE_ON_MAP or opt in _TOGGLE_OFF_MAP or opt in _EXTRA_TOGGLE_ALL_NAMES:
        if _reject_unexpected_value(editor, opt, value):
            return False
        result = SET_COMMANDS[opt](editor)
        return True if result is None else bool(result)

    int_info = _INT_OPTION_MAP.get(opt)
    if int_info is not None:
        if value is None:
            return _append_set_query(editor, opt, queries)
        display, attr, minimum = int_info
        return _set_int_option(editor, value, attr, display, minimum, is_local=is_local)

    list_info = _LIST_OPTION_MAP.get(opt)
    if list_info is not None:
        if value is None:
            return _append_set_query(editor, opt, queries)
        display, attr = list_info
        return _set_list_option(editor, value, attr, display)

    if opt in ('filetype', 'ft'):
        if value is None:
            return _append_set_query(editor, opt, queries)
        return bool(set_filetype(editor, value))

    if opt in ('fileencoding', 'fenc'):
        if value is None:
            return _append_set_query(editor, opt, queries)
        return bool(set_fileencoding(editor, value))

    if opt in SET_COMMANDS:
        if opt in SET_COMMANDS_TAKING_VALUE:
            SET_COMMANDS[opt](editor, value)
        else:
            if _reject_unexpected_value(editor, opt, value):
                return False
            SET_COMMANDS[opt](editor)
        return True

    editor.show_message('Unknown option: %s' % opt)
    return False


@_cmd('set')
@_cmd('setlocal')
@_cmd('setl')
def set_command_execute(editor: Any, variables: Any) -> None:
    """
    Execute a `:set` / `:setlocal` / `:setl` command (supporting bare `:set`,
    `:set all`, queries `opt?`, toggles `opt!` / `invopt`, and multi-argument
    option lists).
    """
    if variables.get('force'):
        editor.show_message('No ! allowed')
        return

    command = variables.get('command')
    is_local = command in ('setlocal', 'setl')
    tokens = parse_set_tokens(variables)

    if not tokens:
        lines = set_options_lines(editor, show_all=False)
        editor.show_info('\n'.join(lines), title='[Options]')
        return

    queries: list[str] = []
    for opt, value in tokens:
        if not _apply_set_token(editor, opt, value, is_local=is_local, queries=queries):
            return

    if queries:
        editor.show_message('  '.join(queries))


def _set_readonly(editor: Any, enabled: bool) -> bool:
    eb = editor.window_arrangement.active_editor_buffer
    if eb is None:
        editor.show_message('No buffer')
        return False
    eb.buffer.read_only = to_filter(enabled)
    return True


@set_cmd('readonly')
@set_cmd('ro')
def enable_readonly(editor: Any) -> bool:
    """Mark the active buffer read-only (`:set ro`)."""
    return _set_readonly(editor, True)


@set_cmd('noreadonly')
@set_cmd('noro')
def disable_readonly(editor: Any) -> bool:
    """Mark the active buffer writable (`:set noro`)."""
    return _set_readonly(editor, False)


@set_cmd('fileencoding', accepts_value=True)
@set_cmd('fenc', accepts_value=True)
def set_fileencoding(editor: Any, value: str | None) -> bool:
    """Get or set the active buffer's file encoding (`:set fenc=utf-8`)."""
    eb = editor.window_arrangement.active_editor_buffer
    if eb is None:
        editor.show_message('No buffer')
        return False
    if value is None:
        editor.show_message(f'fileencoding={eb.encoding or ""}')
        return True
    name = value.strip()
    if not name:
        editor.show_message(f'E474: Invalid argument: {value}')
        return False
    try:
        codecs.lookup(name)
    except LookupError:
        editor.show_message(f'E474: Invalid argument: {value}')
        return False
    eb.encoding = name.lower()
    return True


@set_cmd('fileformat', accepts_value=True)
@set_cmd('ff', accepts_value=True)
def set_fileformat(editor: Any, value: str | None) -> bool:
    """Get or set the active buffer's line-ending style (`:set ff=dos`)."""
    eb = editor.window_arrangement.active_editor_buffer
    if value is None:
        current = eb.fileformat if eb is not None else getattr(editor, 'fileformat', 'unix')
        editor.show_message(f'fileformat={current}')
        return True
    fmt = value.strip().lower()
    if fmt not in ('dos', 'unix', 'mac'):
        editor.show_message('E518: Invalid fileformat: %s' % value)
        return False
    editor.fileformat = fmt
    if eb is not None:
        eb.fileformat = fmt
    return True


@set_cmd('filetype', accepts_value=True)
@set_cmd('ft', accepts_value=True)
def set_filetype(editor: Any, value: str | None) -> bool:
    """Override the Pygments lexer for the active buffer (`:set ft=python`)."""
    eb = editor.window_arrangement.active_editor_buffer
    if eb is None:
        editor.show_message('No buffer')
        return False
    if value is None:
        ft = eb.filetype
        editor.show_message(f'filetype={ft or ""}')
        return True
    text = value.strip()
    if text == '' or text.lower() in ('off', 'none', 'auto'):
        new_override: str | None = None
    else:
        try:
            from pygments.lexers import get_lexer_by_name

            lexer = get_lexer_by_name(text)
        except Exception:
            editor.show_message('Unknown filetype: %s' % value)
            return False
        aliases = getattr(lexer, 'aliases', None)
        new_override = aliases[0] if aliases else text

    eb.filetype_override = new_override
    eb.tabstop = None
    for ctrl in list(getattr(eb, '_live_render_controllers', ())):
        ctrl.schedule(immediate=True)
    invalidate_app()
    return True


@set_cmd('tildeop')
@set_cmd('top')
def enable_tildeop(editor: Any) -> None:
    """Enable tilde operator."""
    editor.application.vi_state.tilde_operator = True


@set_cmd('notildeop')
@set_cmd('notop')
def disable_tildeop(editor: Any) -> None:
    """Disable tilde operator."""
    editor.application.vi_state.tilde_operator = False


@_cmd('colorscheme')
@_cmd('colo')
def color_scheme(editor: Any, variables: Any) -> None:
    """
    Apply a colorscheme by name.
    """
    colorscheme = variables.get('colorscheme')
    if colorscheme:
        editor.use_colorscheme(colorscheme, report=True)


@cmd('noh')
@cmd('nohlsearch')
def no_hlsearch(editor: Any) -> None:
    """
    Temporarily clear search highlighting until the next search (`:noh`).
    """
    editor.suppress_hlsearch()
