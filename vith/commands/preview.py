from .commands import SET_INT_OPTIONS, SET_LIST_OPTIONS, SET_TOGGLE_GROUPS, parse_set_tokens
from .grammar import COMMAND_GRAMMAR

__all__ = ('CommandPreviewer',)


def _build_preview_maps():
    """
    Per-alias lookup maps derived from the commands.py `:set` catalog —
    the single source of truth shared with the command handlers
    (docs/bugs/073.md, closing the 028 two-tables drift).
    """
    toggles = {}
    toggle_on_attrs = {}
    for _display, attribute, on_names, off_names in SET_TOGGLE_GROUPS:
        for name in on_names:
            toggles[name] = (attribute, True)
            toggle_on_attrs[name] = attribute
        for name in off_names:
            toggles[name] = (attribute, False)

    ints = {}
    for _display, attribute, names, minimum in SET_INT_OPTIONS:
        for name in names:
            ints[name] = (attribute, minimum)

    lists = {}
    for _display, attribute, names in SET_LIST_OPTIONS:
        for name in names:
            lists[name] = attribute

    return toggles, toggle_on_attrs, ints, lists


_TOGGLE_PREVIEWS, _TOGGLE_ON_ATTRS, _INT_VALUE_PREVIEWS, _LIST_VALUE_PREVIEWS = (
    _build_preview_maps()
)


class CommandPreviewer:
    """
    Already show the effect of Vi commands before enter is pressed.
    """

    def __init__(self, editor):
        self.editor = editor
        self.save()

    def save(self):
        """
        Back up current editor state.
        """
        e = self.editor

        self._style = e.current_style
        self._show_line_numbers = e.show_line_numbers
        self._highlight_search = e.highlight_search
        self._hlsearch_suppressed = getattr(e, '_hlsearch_suppressed', False)
        self._show_ruler = e.show_ruler
        self._relative_number = e.relative_number
        self._cursorcolumn = e.cursorcolumn
        self._cursorline = e.cursorline
        self._colorcolumn = e.colorcolumn
        self._tabstop = e.tabstop
        self._shiftwidth = e.shiftwidth
        self._scroll_offset = e.scroll_offset
        # The full `:set` catalog is previewable, so the whole state must be
        # restorable — a preview abort (Esc) used to leak inverted values for
        # every option outside the list below (docs/bugs/091.md).
        self._paste_mode = e.paste_mode
        self._show_wildmenu = e.show_wildmenu
        self._expand_tab = e.expand_tab
        self._incsearch = e.incsearch
        self._ignore_case = e.ignore_case
        self._smartcase = e.smartcase
        self._autoindent = e.autoindent
        self._display_unprintable_characters = e.display_unprintable_characters
        self._enable_jedi = e.enable_jedi
        self._wrap_lines = e.wrap_lines
        self._break_indent = e.break_indent
        self._enable_mouse_support = e.enable_mouse_support
        # Buffer-local `:set filetype`, `tabstop`, and `shiftwidth` preview state
        # (lives on the active EditorBuffer, not on the Editor).
        self._filetype_buffer = None
        self._filetype_override = None
        self._buffer_tabstop_override = None
        self._buffer_shiftwidth_override = None
        try:
            eb = e.window_arrangement.active_editor_buffer
        except Exception:
            eb = None
        if eb is not None:
            self._filetype_buffer = eb
            self._filetype_override = getattr(eb, 'filetype_override', None)
            self._buffer_tabstop_override = getattr(eb, '_tabstop_override', None)
            self._buffer_shiftwidth_override = getattr(eb, '_shiftwidth_override', None)

    def restore(self):
        """
        Focus of Vi command line lost, undo preview.
        """
        e = self.editor

        e.current_style = self._style
        e.show_line_numbers = self._show_line_numbers
        e.highlight_search = self._highlight_search
        e._hlsearch_suppressed = self._hlsearch_suppressed
        e.show_ruler = self._show_ruler
        e.relative_number = self._relative_number
        e.cursorcolumn = self._cursorcolumn
        e.cursorline = self._cursorline
        e.colorcolumn = self._colorcolumn
        e.tabstop = self._tabstop
        e.shiftwidth = self._shiftwidth
        e.scroll_offset = self._scroll_offset
        e.paste_mode = self._paste_mode
        e.show_wildmenu = self._show_wildmenu
        e.expand_tab = self._expand_tab
        e.incsearch = self._incsearch
        e.ignore_case = self._ignore_case
        e.smartcase = self._smartcase
        e.autoindent = self._autoindent
        e.display_unprintable_characters = self._display_unprintable_characters
        e.enable_jedi = self._enable_jedi
        e.wrap_lines = self._wrap_lines
        e.break_indent = self._break_indent
        e.enable_mouse_support = self._enable_mouse_support
        eb = getattr(self, '_filetype_buffer', None)
        if eb is not None:
            try:
                eb.filetype_override = self._filetype_override
                eb._tabstop_override = self._buffer_tabstop_override
                eb._shiftwidth_override = self._buffer_shiftwidth_override
            except Exception:
                pass

    def preview(self, input_string):
        """
        Show effect of current Vi command.
        """
        # First, the apply.
        self.restore()
        self._apply(input_string)

    def _apply(self, input_string):
        """Apply command."""
        e = self.editor

        # Parse command.
        m = COMMAND_GRAMMAR.match(input_string)
        if m is None:
            return

        variables = m.variables()

        command = variables.get('command')

        match command:
            # Preview colorschemes.
            case 'colorscheme' | 'colo':
                colorscheme = variables.get('colorscheme')
                if colorscheme:
                    e.use_colorscheme(colorscheme, report=False)

            # Preview `:set` commands through the shared catalog above (the
            # same table the `:set` handlers are generated from, docs/bugs/
            # 073.md). Supports multi-argument `:set` and `!`/`inv` toggles;
            # bare `:set`, `:set all`, queries (`?`), and non-restorable
            # options stay side-effect free.
            case 'set' | 'setlocal' | 'setl':
                is_local = command in ('setlocal', 'setl')
                for set_option, set_value in parse_set_tokens(variables):
                    self._preview_set_token(e, set_option, set_value, is_local=is_local)

    def _preview_set_token(self, e, set_option, set_value, *, is_local=False):
        if set_option == 'all' or set_option.endswith('?'):
            return

        if set_option.endswith('!'):
            if set_value is not None:
                return
            attr = _TOGGLE_ON_ATTRS.get(set_option[:-1])
            if attr is not None:
                new_val = not bool(getattr(e, attr))
                setattr(e, attr, new_val)
                if attr == 'highlight_search' and new_val:
                    e._hlsearch_suppressed = False
            return

        if set_option.startswith('inv') and set_option not in _TOGGLE_PREVIEWS:
            if set_value is not None:
                return
            attr = _TOGGLE_ON_ATTRS.get(set_option[3:])
            if attr is not None:
                new_val = not bool(getattr(e, attr))
                setattr(e, attr, new_val)
                if attr == 'highlight_search' and new_val:
                    e._hlsearch_suppressed = False
            return

        token_vars = {'set_value': set_value}
        if set_option in ('filetype', 'ft'):
            self._preview_filetype_value(e, token_vars)
            return

        spec = _TOGGLE_PREVIEWS.get(set_option)
        if spec is not None and set_value is None:
            setattr(e, spec[0], spec[1])
            if spec[0] == 'highlight_search' and spec[1]:
                e._hlsearch_suppressed = False
            return

        spec = _INT_VALUE_PREVIEWS.get(set_option)
        if spec is not None:
            self._preview_int_value(e, spec[0], spec[1], token_vars, is_local=is_local)
            return

        attribute = _LIST_VALUE_PREVIEWS.get(set_option)
        if attribute is not None and set_value is not None:
            self._preview_list_value(e, attribute, token_vars)

    @staticmethod
    def _preview_int_value(e, attribute, minimum, variables, is_local=False):
        raw_value = variables.get('set_value')
        if raw_value and raw_value.isdigit() and int(raw_value) >= minimum:
            val = int(raw_value)
            if attribute in ('tabstop', 'shiftwidth'):
                eb = getattr(e.window_arrangement, 'active_editor_buffer', None)
                if eb is not None:
                    setattr(eb, f'_{attribute}_override', val)
                if not is_local:
                    setattr(e, attribute, val)
            else:
                setattr(e, attribute, val)

    @staticmethod
    def _preview_list_value(e, attribute, variables):
        # Honor the catalog-provided attribute (used to hard-code
        # `colorcolumn`, which 073-era drift left stale; docs/bugs/091.md).
        value = variables.get('set_value', '')
        if not value:
            setattr(e, attribute, [])
            return
        try:
            numbers = [int(v) for v in value.split(',')]
        except ValueError:
            return
        setattr(e, attribute, numbers)

    def _preview_filetype_value(self, e, variables):
        # Live-highlight while typing `:set filetype=...`; invalid names are
        # ignored (no message during preview). `None` (bare `:set filetype`)
        # and empty/`off` (clear) restore the saved override via `restore()`
        # already run by `preview()`.
        if variables.get('set_value') is None:
            return
        value = (variables.get('set_value') or '').strip()
        eb = getattr(self, '_filetype_buffer', None)
        if eb is None:
            try:
                eb = e.window_arrangement.active_editor_buffer
            except Exception:
                return
            self._filetype_buffer = eb
        if eb is None:
            return
        if value == '' or value.lower() in ('off', 'none', 'auto'):
            eb.filetype_override = None
            return
        try:
            from pygments.lexers import get_lexer_by_name

            lexer = get_lexer_by_name(value)
        except Exception:
            return
        aliases = getattr(lexer, 'aliases', None)
        eb.filetype_override = aliases[0] if aliases else value
