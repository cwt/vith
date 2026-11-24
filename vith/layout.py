"""
The actual layout for the renderer.
"""

import re
import sys
from functools import lru_cache, partial

from prompt_toolkit.application.current import get_app
from prompt_toolkit.filters import Condition, has_arg, has_focus, is_searching
from prompt_toolkit.key_binding.vi_state import InputMode
from prompt_toolkit.layout import Float, FloatContainer, HSplit, Layout, VSplit
from prompt_toolkit.layout.containers import (
    ColorColumn,
    ConditionalContainer,
    ScrollOffsets,
    Window,
    WindowAlign,
)
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.margins import ConditionalMargin, NumberedMargin
from prompt_toolkit.layout.menus import CompletionsMenu
from prompt_toolkit.layout.processors import (
    BeforeInput,
    ConditionalProcessor,
    DisplayMultipleCursors,
    HighlightIncrementalSearchProcessor,
    HighlightMatchingBracketProcessor,
    HighlightSearchProcessor,
    HighlightSelectionProcessor,
    Processor,
    ShowTrailingWhiteSpaceProcessor,
    TabsProcessor,
    Transformation,
)
from prompt_toolkit.layout.utils import explode_text_fragments
from prompt_toolkit.mouse_events import MouseEventType
from prompt_toolkit.selection import SelectionType
from prompt_toolkit.utils import get_cwidth
from prompt_toolkit.widgets.toolbars import (
    CompletionsToolbar,
    FormattedTextToolbar,
    SearchToolbar,
    SystemToolbar,
    ValidationToolbar,
)

import vith.window_arrangement as window_arrangement

from .commands.lexer import create_command_lexer
from .editor_buffer import UNTITLED_LABEL
from .lexer import DocumentLexer
from .welcome_message import WELCOME_MESSAGE_HEIGHT, WELCOME_MESSAGE_TOKENS, WELCOME_MESSAGE_WIDTH

__all__ = ('EditorLayout',)


class _FocusSyncingBufferControl(BufferControl):
    """
    ``BufferControl`` that mirrors ``focus_on_click`` focus changes back into
    ``WindowArrangement`` (docs/bugs/134.md).

    prompt-toolkit's click handler only changes ``layout.current_control``;
    Ex commands resolve their target through ``active_editor_buffer``. Without
    this hook a mouse click leaves the two notions of "active window" out of
    sync and commands mutate the previously focused buffer.
    """

    def __init__(self, on_focus, **kwargs):
        super().__init__(**kwargs)
        self._on_focus = on_focus

    def mouse_handler(self, mouse_event):
        app = get_app()
        before = app.layout.current_control
        result = super().mouse_handler(mouse_event)
        if app.layout.current_control is self and before is not self:
            self._on_focus()
        return result


@lru_cache(maxsize=64)
def _try_char_for_encoding(character: str, backup: str, encoding: str) -> str:
    if character.encode(encoding, 'replace') == b'?':
        return backup
    return character


def _try_char(character, backup, encoding=None):
    """
    Return `character` if it can be encoded using sys.stdout, else return the
    backup character.
    """
    if encoding is None:
        encoding = getattr(sys.stdout, 'encoding', None) or 'utf-8'
    return _try_char_for_encoding(character, backup, encoding)


def get_welcome_message_tokens():
    "Static formatted text tokens shown in the welcome window."
    return WELCOME_MESSAGE_TOKENS


class TabsControl(FormattedTextControl):
    """
    Displays the tabs at the top of the screen, when there is more than one
    open tab.
    """

    def __init__(self, editor):
        def location_for_tab(tab):
            return tab.active_window.editor_buffer.get_display_name(short=True)

        def create_tab_handler(index):
            "Return a mouse handler for this tab. Select the tab on click."

            def handler(mouse_event):
                if mouse_event.event_type == MouseEventType.MOUSE_DOWN:
                    editor.window_arrangement.active_tab_index = index
                    editor.sync_with_prompt_toolkit()
                else:
                    return NotImplemented

            return handler

        # Cache mouse handlers per tab index: `get_tokens` runs per frame,
        # and building a fresh closure per tab per frame churned allocations
        # (docs/bugs/072.md). Tab handlers are stateless w.r.t. the index.
        tab_handlers = {}
        self.tab_handlers = tab_handlers

        def get_tab_handler(index):
            try:
                return tab_handlers[index]
            except KeyError:
                handler = create_tab_handler(index)
                tab_handlers[index] = handler
                return handler

        def get_tokens():
            tab_pages = editor.window_arrangement.tab_pages
            selected_tab_index = editor.window_arrangement.active_tab_index

            if len(tab_handlers) > len(tab_pages):
                for stale_idx in [idx for idx in tab_handlers if idx >= len(tab_pages)]:
                    del tab_handlers[stale_idx]

            result = []
            append = result.append

            for i, tab in enumerate(tab_pages):
                caption = location_for_tab(tab)
                if tab.has_unsaved_changes:
                    caption = ' + ' + caption

                handler = get_tab_handler(i)

                if i == selected_tab_index:
                    append(('class:tabbar.tab.active', ' %s ' % caption, handler))
                else:
                    append(('class:tabbar.tab', ' %s ' % caption, handler))
                append(('class:tabbar', ' '))

            return result

        super().__init__(get_tokens, style='class:tabbar')


class TabsToolbar(ConditionalContainer):
    def __init__(self, editor):
        def several_tabs():
            return len(editor.window_arrangement.tab_pages) > 1

        super().__init__(
            Window(TabsControl(editor), height=1),
            filter=Condition(several_tabs),
        )


class CommandLine(ConditionalContainer):
    """
    The editor command line. (For at the bottom of the screen.)
    """

    def __init__(self, editor):
        super().__init__(
            Window(
                BufferControl(
                    buffer=editor.command_buffer,
                    input_processors=[BeforeInput(':')],
                    lexer=create_command_lexer(),
                ),
                height=1,
            ),
            filter=has_focus(editor.command_buffer),
        )


class WelcomeMessageWindow(ConditionalContainer):
    """
    Welcome message pop-up, which is shown during start-up when no other files
    were opened.
    """

    def __init__(self, editor):
        once_hidden = False  # Nonlocal

        def condition():
            nonlocal once_hidden
            # Get editor buffers
            buffers = editor.window_arrangement.editor_buffers

            # Only show when there is only one empty buffer, but once the
            # welcome message has been hidden, don't show it again.
            result = (
                len(buffers) == 1
                and buffers[0].buffer.text == ''
                and buffers[0].location is None
                and not once_hidden
            )
            if not result:
                once_hidden = True
            return result

        super().__init__(
            Window(
                FormattedTextControl(get_welcome_message_tokens),
                align=WindowAlign.CENTER,
                style="class:welcome",
            ),
            filter=Condition(condition),
        )


def _bufferlist_overlay_visible(editor):
    """
    True when the buffer list overlay should be displayed.
    (This is when someone starts typing ':b' or ':buffer' in the command line.)
    """

    @Condition
    def overlay_is_visible():
        app = get_app()

        text = editor.command_buffer.text.lstrip()
        return app.layout.has_focus(editor.command_buffer) and (
            any(text.startswith(p) for p in ['b ', 'b! ', 'buffer ', 'buffer! '])
        )

    return overlay_is_visible


class BufferListOverlay(ConditionalContainer):
    """
    Floating window that shows the list of buffers when we are typing ':b'
    inside the vim command line.
    """

    def __init__(self, editor):
        cached_search_string = ''
        cached_search_pattern = None

        def highlight_location(location, pattern, search_string, default_token):
            """
            Return a tokenlist with the `search_string` highlighted.
            """
            if not location:
                return []

            if pattern is None:
                token = default_token
                if location == search_string:
                    token += ' [SetCursorPosition]'
                return [(token, location)]

            result = []
            last = 0
            for m in pattern.finditer(location):
                start, end = m.start(), m.end()
                if start > last:
                    result.append((default_token, location[last:start]))
                if end > start:
                    result.append(('class:searchmatch', location[start:end]))
                last = end
            if last < len(location):
                result.append((default_token, location[last:]))

            if location == search_string and result:
                first_style, first_text = result[0]
                result[0] = (first_style + ' [SetCursorPosition]', first_text)

            return result

        def get_tokens():
            nonlocal cached_search_string, cached_search_pattern

            wa = editor.window_arrangement
            buffer_infos = wa.list_open_buffers()

            # Filter infos according to typed text.
            input_params = editor.command_buffer.text.lstrip().split(None, 1)
            search_string = input_params[1] if len(input_params) > 1 else ''

            # Cache the compiled search pattern keyed by `search_string` across
            # frames while typing `:b` (docs/bugs/072.md, 151.md).
            if search_string != cached_search_string:
                cached_search_string = search_string
                cached_search_pattern = (
                    re.compile(re.escape(search_string)) if search_string else None
                )
            search_pattern = cached_search_pattern

            if search_string:

                def matches(info):
                    """
                    True when we should show this entry.
                    """
                    # When the input appears in the location.
                    if input_params[1] in (info.editor_buffer.location or ''):
                        return True

                    # When the input matches this buffer's displayed (1-based,
                    # Vim ':ls'-style) index number.
                    if input_params[1] in str(info.index + 1):
                        return True

                    # When this entry is part of the current completions list.
                    b = editor.command_buffer

                    if b.complete_state and any(
                        info.editor_buffer.location in c.display_text
                        and input_params[1] in c.display_text
                        for c in b.complete_state.completions
                        if info.editor_buffer.location is not None
                    ):
                        return True

                    return False

                buffer_infos = [info for info in buffer_infos if matches(info)]

            # Render output.
            if len(buffer_infos) == 0:
                return [('', ' No match found. ')]
            else:
                result = []

                # Create title.
                result.append(('', '  '))
                result.append(('class:title', 'Open buffers\n'))

                # Align on terminal display width, not codepoint count, and
                # fetch each display name once (docs/bugs/225.md).
                named_infos = [
                    (info, info.editor_buffer.get_display_name()) for info in buffer_infos
                ]
                max_location_len = max(get_cwidth(name) for _info, name in named_infos)

                # Show info for each buffer.
                for info, display_name in named_infos:
                    eb = info.editor_buffer
                    char = '%' if info.is_active else ' '
                    char2 = 'a' if info.is_visible else ' '
                    char3 = ' + ' if info.editor_buffer.has_unsaved_changes else '   '
                    t = 'class:active' if info.is_active else ''

                    result.extend(
                        [
                            ('', ' '),
                            (t, '%3i ' % (info.index + 1)),
                            (t, '%s' % char),
                            (t, '%s ' % char2),
                            (t, '%s ' % char3),
                        ]
                    )
                    result.extend(
                        highlight_location(display_name, search_pattern, search_string, t)
                    )
                    result.extend(
                        [
                            (t, ' ' * (max_location_len - get_cwidth(display_name))),
                            (
                                t + ' class:lineno',
                                '  line %i' % (eb.buffer.document.cursor_position_row + 1),
                            ),
                            (t, ' \n'),
                        ]
                    )
                return result

        super().__init__(
            Window(
                FormattedTextControl(get_tokens),
                style='class:bufferlist',
                scroll_offsets=ScrollOffsets(top=1, bottom=1),
            ),
            filter=_bufferlist_overlay_visible(editor),
        )


class MessageToolbarBar(ConditionalContainer):
    """
    Pop-up (at the bottom) for showing error/status messages.
    """

    def __init__(self, editor):
        def has_message():
            return editor.message is not None

        def get_tokens():
            if editor.message:
                return [('class:message', editor.message)]
            else:
                return []

        super().__init__(FormattedTextToolbar(get_tokens), filter=Condition(has_message))


class ReportMessageToolbar(ConditionalContainer):
    """
    Toolbar that shows the messages, given by the reporter.
    (It shows the error message, related to the current line.)
    """

    def __init__(self, editor):
        def get_formatted_text():
            eb = editor.window_arrangement.active_editor_buffer
            if eb is None:
                return []

            lineno = eb.buffer.document.cursor_position_row
            errors = eb.report_errors_by_line.get(lineno)

            if not errors:
                return []

            # A line can have several pyflakes findings: show all of them,
            # separated by a blank, instead of only the first one.
            # (docs/bugs/012.md)
            fragments = []
            for i, error in enumerate(errors):
                if i:
                    fragments.append(('', '  '))
                fragments.extend(error.formatted_text)
            return fragments

        super().__init__(
            FormattedTextToolbar(get_formatted_text),
            filter=~has_focus(editor.command_buffer) & ~is_searching & ~has_focus('system'),
        )


class WindowStatusBar(FormattedTextToolbar):
    """
    The status bar, which is shown below each window in a tab page.
    """

    def __init__(self, editor, editor_buffer):
        def get_text():
            app = get_app()

            insert_mode = app.vi_state.input_mode in (InputMode.INSERT, InputMode.INSERT_MULTIPLE)
            replace_mode = app.vi_state.input_mode == InputMode.REPLACE
            sel = editor_buffer.buffer.selection_state
            temp_navigation = app.vi_state.temporary_navigation_mode
            visual_line = sel is not None and sel.type == SelectionType.LINES
            visual_block = sel is not None and sel.type == SelectionType.BLOCK
            visual_char = sel is not None and sel.type == SelectionType.CHARACTERS

            def mode():
                if get_app().layout.has_focus(editor_buffer.buffer):
                    if insert_mode:
                        if temp_navigation:
                            return ' -- (insert) --'
                        elif editor.paste_mode:
                            return ' -- INSERT (paste)--'
                        else:
                            return ' -- INSERT --'
                    elif replace_mode:
                        if temp_navigation:
                            return ' -- (replace) --'
                        else:
                            return ' -- REPLACE --'
                    elif visual_block:
                        return ' -- VISUAL BLOCK --'
                    elif visual_line:
                        return ' -- VISUAL LINE --'
                    elif visual_char:
                        return ' -- VISUAL --'
                return '                     '

            def recording():
                if app.vi_state.recording_register:
                    return 'recording '
                else:
                    return ''

            if editor_buffer.scratch_title:
                name_part = editor_buffer.scratch_title
            else:
                name_part = (editor_buffer.location or '') + (
                    f' {UNTITLED_LABEL}' if editor_buffer.is_new else ''
                )

            return ''.join(
                [
                    ' ',
                    recording(),
                    name_part,
                    ('*' if editor_buffer.has_unsaved_changes else ''),
                    (' '),
                    mode(),
                ]
            )

        super().__init__(get_text, style='class:toolbar.status')


class WindowStatusBarRuler(ConditionalContainer):
    """
    The right side of the Vim toolbar, showing the location of the cursor in
    the file, and the vertical scroll percentage.
    """

    def __init__(self, editor, buffer_window, buffer):
        def ruler_enabled():
            return editor.show_ruler

        def get_scroll_text():
            info = buffer_window.render_info

            if info:
                if info.full_height_visible:
                    return 'All'
                elif info.top_visible:
                    return 'Top'
                elif info.bottom_visible:
                    return 'Bot'
                else:
                    percentage = info.vertical_scroll_percentage
                    return '%2i%%' % percentage

            return ''

        def get_tokens():
            main_document = buffer.document

            return [
                (
                    'class:cursorposition',
                    '(%i,%i)'
                    % (
                        main_document.cursor_position_row + 1,
                        main_document.cursor_position_col + 1,
                    ),
                ),
                ('', ' - '),
                ('class:percentage', get_scroll_text()),
                ('', ' '),
            ]

        super().__init__(
            Window(
                FormattedTextControl(get_tokens),
                char=' ',
                align=WindowAlign.RIGHT,
                style='class:toolbar.status',
                height=1,
            ),
            filter=Condition(ruler_enabled),
        )


class SimpleArgToolbar(ConditionalContainer):
    """
    Simple control showing the Vi repeat arg.
    """

    def __init__(self):
        def get_tokens():
            arg = get_app().key_processor.arg
            if arg is not None:
                return [('class:arg', ' %s ' % arg)]
            else:
                return []

        super().__init__(
            Window(FormattedTextControl(get_tokens), align=WindowAlign.RIGHT), filter=has_arg
        )


class EditorLayout:
    """
    The main layout class.
    """

    def __init__(self, editor, window_arrangement):
        self.editor = editor  # Back reference to editor.
        self.window_arrangement = window_arrangement

        def wildmenu_enabled():
            return editor.show_wildmenu

        def _ignore_case_enabled():
            app = getattr(editor, 'application', None)
            if app is not None and app.layout.has_focus(editor.search_buffer):
                pattern = editor.search_buffer.text
            elif app is not None:
                pattern = app.current_search_state.text or editor.search_buffer.text
            else:
                pattern = editor.search_buffer.text
            return editor.should_ignore_case(pattern)

        # Mapping from (`window_arrangement.Window`, `EditorBuffer`) to a frame
        # (Layout instance).
        # We keep this as a cache in order to easily reuse the same frames when
        # the layout is updated. (We don't want to create new frames on every
        # update call, because that way, we would loose some state, like the
        # vertical scroll offset.)
        self._frames = {}

        self._fc = FloatContainer(
            content=VSplit(
                [
                    Window(BufferControl())  # Dummy window
                ]
            ),
            floats=[
                Float(
                    xcursor=True,
                    ycursor=True,
                    content=CompletionsMenu(
                        max_height=12,
                        scroll_offset=2,
                        extra_filter=~has_focus(editor.command_buffer),
                    ),
                ),
                Float(content=BufferListOverlay(editor), bottom=1, left=0),
                Float(
                    bottom=1,
                    left=0,
                    right=0,
                    height=1,
                    content=ConditionalContainer(
                        CompletionsToolbar(),
                        filter=has_focus(editor.command_buffer)
                        & ~_bufferlist_overlay_visible(editor)
                        & Condition(wildmenu_enabled),
                    ),
                ),
                Float(bottom=1, left=0, right=0, height=1, content=ValidationToolbar()),
                Float(bottom=1, left=0, right=0, height=1, content=MessageToolbarBar(editor)),
                Float(
                    content=WelcomeMessageWindow(editor),
                    height=WELCOME_MESSAGE_HEIGHT,
                    width=WELCOME_MESSAGE_WIDTH,
                ),
            ],
        )

        search_toolbar = SearchToolbar(
            vi_mode=True,
            search_buffer=editor.search_buffer,
            ignore_case=Condition(_ignore_case_enabled),
        )
        self.search_control = search_toolbar.control

        self.layout = Layout(
            FloatContainer(
                content=HSplit(
                    [
                        TabsToolbar(editor),
                        self._fc,
                        CommandLine(editor),
                        ReportMessageToolbar(editor),
                        SystemToolbar(),
                        search_toolbar,
                    ]
                ),
                floats=[
                    Float(right=0, height=1, bottom=0, width=5, content=SimpleArgToolbar()),
                ],
            )
        )

    def get_vertical_border_char(self):
        "Return the character to be used for the vertical border."
        return _try_char('\u2502', '|', get_app().output.encoding())

    def update(self):
        """
        Update layout to match the layout as described in the
        WindowArrangement.
        """
        # Start with an empty frames list everytime, to avoid memory leaks.
        existing_frames = self._frames
        self._frames = {}

        def create_layout_from_node(node):
            match node:
                case window_arrangement.Window():
                    # Create frame for Window, or reuse it, if we had one already.
                    key = (node, node.editor_buffer)
                    frame = existing_frames.get(key)
                    if frame is None:
                        frame, pt_window = self._create_window_frame(node.editor_buffer, node)

                        # Link layout Window to arrangement.
                        node.pt_window = pt_window

                    self._frames[key] = frame
                    return frame

                case window_arrangement.VSplit():
                    return VSplit(
                        [create_layout_from_node(n) for n in node],
                        padding=1,
                        padding_char=self.get_vertical_border_char(),
                        padding_style='class:frameborder',
                    )

                case window_arrangement.HSplit():
                    return HSplit([create_layout_from_node(n) for n in node])

                case _:
                    raise ValueError(f"Unknown window arrangement node: {node!r}")

        active_tab = self.window_arrangement.active_tab
        if active_tab is None:
            return
        layout = create_layout_from_node(active_tab.root)
        # Retain frames belonging to live inactive tabs so tab switches preserve
        # scroll offsets and `DocumentLexer`/`BufferControl` caches, while still
        # dropping frames for closed tabs/windows/buffers (docs/bugs/151.md).
        for tab in self.window_arrangement.tab_pages:
            if tab is active_tab:
                continue
            for win in tab.windows():
                key = (win, win.editor_buffer)
                frame = existing_frames.get(key)
                if frame is not None:
                    self._frames[key] = frame
        self._fc.content = layout

    def _create_window_frame(self, editor_buffer, arrangement_window=None):
        """
        Create a Window for the buffer, with underneath a status bar.

        `arrangement_window` links click focus back to the arrangement.
        """

        @Condition
        def wrap_lines():
            if getattr(editor_buffer, 'is_render_preview', False):
                return False
            return self.editor.wrap_lines

        def scroll_offset():
            return self.editor.scroll_offset

        def relative_numbers_enabled():
            if getattr(editor_buffer, 'is_render_preview', False):
                return False
            return self.editor.relative_number

        def line_numbers_enabled():
            if getattr(editor_buffer, 'is_render_preview', False):
                return False
            return self.editor.show_line_numbers

        def cursorline_enabled():
            if getattr(editor_buffer, 'is_render_preview', False):
                return False
            return self.editor.cursorline

        def cursorcolumn_enabled():
            if getattr(editor_buffer, 'is_render_preview', False):
                return False
            return self.editor.cursorcolumn

        def get_colorcolumns():
            if getattr(editor_buffer, 'is_render_preview', False):
                return []
            return [ColorColumn(pos) for pos in self.editor.colorcolumn]

        def on_focus():
            if arrangement_window is not None:
                self.window_arrangement.set_active_window(arrangement_window)

        window = Window(
            self._create_buffer_control(editor_buffer, on_focus),
            allow_scroll_beyond_bottom=True,
            scroll_offsets=ScrollOffsets(
                left=0,
                right=0,
                top=scroll_offset,
                bottom=scroll_offset,
            ),
            wrap_lines=wrap_lines,
            left_margins=[
                ConditionalMargin(
                    margin=NumberedMargin(
                        display_tildes=True, relative=Condition(relative_numbers_enabled)
                    ),
                    filter=Condition(line_numbers_enabled),
                ),
            ],
            cursorline=Condition(cursorline_enabled),
            cursorcolumn=Condition(cursorcolumn_enabled),
            colorcolumns=get_colorcolumns,
            ignore_content_width=True,
            ignore_content_height=True,
            get_line_prefix=partial(self._get_line_prefix, editor_buffer.buffer),
        )

        return HSplit(
            [
                window,
                VSplit(
                    [
                        WindowStatusBar(self.editor, editor_buffer),
                        WindowStatusBarRuler(self.editor, window, editor_buffer.buffer),
                    ],
                    width=Dimension(),
                ),  # Ignore actual status bar width.
            ]
        ), window

    def _create_buffer_control(self, editor_buffer, on_focus=None):
        """
        Create a new BufferControl for a given location.
        """

        @Condition
        def preview_search():
            return self.editor.incsearch

        def search_highlight_enabled():
            return self.editor.highlight_search and not self.editor._hlsearch_suppressed

        def unprintable_enabled():
            if getattr(editor_buffer, 'is_render_preview', False):
                return False
            return self.editor.display_unprintable_characters

        def tabstop_value():
            return getattr(editor_buffer, 'tabstop', self.editor.tabstop)

        def tab_char1():
            if self.editor.display_unprintable_characters:
                return '|'
            return ' '

        def tab_char2():
            if self.editor.display_unprintable_characters:
                return _try_char('\u2508', '.', get_app().output.encoding())
            return ' '

        input_processors = [
            # Pyflakes error highlighting must run BEFORE tab expansion:
            # `ReporterError.start_column` counts source codepoints (raw
            # lexer fragments), while `TabsProcessor` would shift the
            # fragment index space to display columns and misalign the
            # highlight on tab-indented lines. (docs/bugs/011.md)
            ReportingProcessor(editor_buffer),
            # Processor for visualising spaces. (should come before the
            # selection processor, otherwise, we won't see these spaces
            # selected.)
            ConditionalProcessor(ShowTrailingWhiteSpaceProcessor(), Condition(unprintable_enabled)),
            # Replace tabs by spaces.
            TabsProcessor(
                tabstop=tabstop_value,
                char1=tab_char1,
                char2=tab_char2,
            ),
            HighlightSelectionProcessor(),
            ConditionalProcessor(HighlightSearchProcessor(), Condition(search_highlight_enabled)),
            ConditionalProcessor(
                HighlightIncrementalSearchProcessor(),
                Condition(search_highlight_enabled) & preview_search,
            ),
            HighlightMatchingBracketProcessor(),
            DisplayMultipleCursors(),
        ]

        return _FocusSyncingBufferControl(
            on_focus=on_focus if on_focus is not None else lambda: None,
            lexer=DocumentLexer(editor_buffer),
            include_default_input_processors=False,
            input_processors=input_processors,
            buffer=editor_buffer.buffer,
            preview_search=preview_search,
            search_buffer_control=self.search_control,
            focus_on_click=True,
        )

    def _get_line_prefix(self, buffer, line_number, wrap_count):
        if wrap_count > 0:
            result = []

            # Add 'breakindent' prefix.
            if self.editor.break_indent:
                line = buffer.document.lines[line_number]
                prefix = line[: len(line) - len(line.lstrip())]
                result.append(('', prefix))

            # Add softwrap mark.
            result.append(('class:soft-wrap', '...'))
            return result
        return ''


class ReportingProcessor(Processor):
    """
    Highlight all pyflakes errors on the input.
    """

    def __init__(self, editor_buffer):
        self.editor_buffer = editor_buffer

    def apply_transformation(self, transformation_input):
        fragments = transformation_input.fragments

        # O(1) lookup in the error index (populated by
        # `EditorBuffer.run_reporter`), instead of an O(V x E) scan over all
        # errors for every visible line on every frame. Both
        # `TransformationInput.lineno` (the 0-based document row passed by
        # `BufferControl`) and `ReporterError.lineno` are 0-based.
        errors = self.editor_buffer.report_errors_by_line.get(transformation_input.lineno)

        if errors:
            fragments = explode_text_fragments(fragments)
            for error in errors:
                for i in range(error.start_column, error.end_column):
                    if i < len(fragments):
                        fragments[i] = ('class:flakeserror', fragments[i][1])

        return Transformation(fragments)
