from .commands import SET_INT_OPTIONS, SET_LIST_OPTIONS, SET_TOGGLE_GROUPS
from .grammar import COMMAND_GRAMMAR

__all__ = ('CommandPreviewer',)


def _build_preview_maps():
    """
    Per-alias lookup maps derived from the commands.py `:set` catalog —
    the single source of truth shared with the command handlers
    (docs/bugs/073.md, closing the 028 two-tables drift).
    """
    toggles = {}
    for _display, attribute, on_names, off_names in SET_TOGGLE_GROUPS:
        for name in on_names:
            toggles[name] = (attribute, True)
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

    return toggles, ints, lists


_TOGGLE_PREVIEWS, _INT_VALUE_PREVIEWS, _LIST_VALUE_PREVIEWS = _build_preview_maps()


class CommandPreviewer(object):
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
        self._show_ruler = e.show_ruler
        self._relative_number = e.relative_number
        self._cursorcolumn = e.cursorcolumn
        self._cursorline = e.cursorline
        self._colorcolumn = e.colorcolumn
        self._tabstop = e.tabstop
        self._scroll_offset = e.scroll_offset
        # The full `:set` catalog is previewable, so the whole state must be
        # restorable — a preview abort (Esc) used to leak inverted values for
        # every option outside the list below (docs/bugs/091.md).
        self._paste_mode = e.paste_mode
        self._show_wildmenu = e.show_wildmenu
        self._expand_tab = e.expand_tab
        self._incsearch = e.incsearch
        self._ignore_case = e.ignore_case
        self._display_unprintable_characters = e.display_unprintable_characters
        self._enable_jedi = e.enable_jedi
        self._wrap_lines = e.wrap_lines
        self._break_indent = e.break_indent
        self._enable_mouse_support = e.enable_mouse_support

    def restore(self):
        """
        Focus of Vi command line lost, undo preview.
        """
        e = self.editor

        e.current_style = self._style
        e.show_line_numbers = self._show_line_numbers
        e.highlight_search = self._highlight_search
        e.show_ruler = self._show_ruler
        e.relative_number = self._relative_number
        e.cursorcolumn = self._cursorcolumn
        e.cursorline = self._cursorline
        e.colorcolumn = self._colorcolumn
        e.tabstop = self._tabstop
        e.scroll_offset = self._scroll_offset
        e.paste_mode = self._paste_mode
        e.show_wildmenu = self._show_wildmenu
        e.expand_tab = self._expand_tab
        e.incsearch = self._incsearch
        e.ignore_case = self._ignore_case
        e.display_unprintable_characters = self._display_unprintable_characters
        e.enable_jedi = self._enable_jedi
        e.wrap_lines = self._wrap_lines
        e.break_indent = self._break_indent
        e.enable_mouse_support = self._enable_mouse_support

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
        set_option = variables.get('set_option')

        # Preview colorschemes.
        if command in ('colorscheme', 'colo'):
            colorscheme = variables.get('colorscheme')
            if colorscheme:
                e.use_colorscheme(colorscheme)

        # Preview `:set` commands through the shared catalog above (the
        # same table the `:set` handlers are generated from, docs/bugs/
        # 073.md). Options that mutate vi_state (tildeop) are not
        # preview-restorable and stay ignored, as before.
        if command == 'set':
            spec = _TOGGLE_PREVIEWS.get(set_option)
            if spec is not None:
                setattr(e, spec[0], spec[1])
                return

            spec = _INT_VALUE_PREVIEWS.get(set_option)
            if spec is not None:
                self._preview_int_value(e, spec[0], spec[1], variables)
                return

            attribute = _LIST_VALUE_PREVIEWS.get(set_option)
            if attribute is not None:
                self._preview_list_value(e, attribute, variables)

    @staticmethod
    def _preview_int_value(e, attribute, minimum, variables):
        raw_value = variables.get('set_value')
        if raw_value and raw_value.isdigit() and int(raw_value) >= minimum:
            setattr(e, attribute, int(raw_value))

    @staticmethod
    def _preview_list_value(e, attribute, variables):
        # Honor the catalog-provided attribute (used to hard-code
        # `colorcolumn`, which 073-era drift left stale; docs/bugs/091.md).
        if attribute is None:
            return
        value = variables.get('set_value', '')
        if not value:
            setattr(e, attribute, [])
            return
        try:
            numbers = [int(v) for v in value.split(',')]
        except ValueError:
            return
        setattr(e, attribute, numbers)
