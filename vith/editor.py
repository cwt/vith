"""
The main editor class.

Usage::

    files_to_edit = ['file1.txt', 'file2.py']
    e = Editor(files_to_edit)
    e.run()  # Runs the event loop, starts interaction.
"""

from __future__ import annotations

import os
from collections import deque
from typing import TYPE_CHECKING, Any

import pygments
from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.enums import EditingMode
from prompt_toolkit.filters import Condition, to_filter
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding.vi_state import InputMode
from prompt_toolkit.styles import DynamicStyle

from .cold_text import format_cold_text_stats
from .commands.completer import create_command_completer
from .commands.handler import handle_command
from .commands.options import snapshot_default_options
from .commands.preview import CommandPreviewer
from .commands.substitution import resolve_ignore_case
from .editor_buffer import short_display_name
from .filetypes import canonical_filetype as _canonical_filetype
from .help import HELP_TEXT
from .io import DirectoryIO, FileIO, GZipFileIO, HttpIO, expand_location
from .key_bindings import create_key_bindings
from .layout import EditorLayout
from .process import set_process_name
from .style import generate_built_in_styles, get_editor_style_by_name
from .thai import (
    install_prompt_toolkit_thai_patches,
    note_document_text,
    reset_thai_text_gate_if_all_buffers_ascii,
)
from .window_arrangement import WindowArrangement

if TYPE_CHECKING:
    from .editor_buffer import EditorBuffer

__all__ = ('Editor', 'DEFAULT_FILETYPE_TABSTOPS', 'FiletypeTabstops')


def _terminal_title_extra_sequence(title_osc_body: str) -> str:
    """
    Build the combined OSC-0 window-title + tmux/screen suffix sequence,
    shared by set/clear paths (docs/bugs/073.md): when running under tmux or
    GNU screen, the stacked `ESC k <body> ESC \\` title sequence follows the
    OSC-0 escape.
    """
    extra_seq = f"\x1b]0;{title_osc_body}\x07"
    if (
        'TMUX' in os.environ
        or 'STY' in os.environ
        or os.environ.get('TERM', '').startswith(('screen', 'tmux'))
    ):
        extra_seq += f"\x1bk{title_osc_body}\x1b\\"
    return extra_seq


#: Standard tab stop settings for common programming and markup languages.
DEFAULT_FILETYPE_TABSTOPS: dict[str, int] = {
    # 2 spaces: Web, data interchange, shell, config, scripting
    'javascript': 2,
    'typescript': 2,
    'html': 2,
    'css': 2,
    'scss': 2,
    'sass': 2,
    'less': 2,
    'json': 2,
    'yaml': 2,
    'ruby': 2,
    'lua': 2,
    'bash': 2,
    'markdown': 2,
    'xml': 2,
    'graphql': 2,
    'vue': 2,
    'svelte': 2,
    # 4 spaces: General programming languages
    'python': 4,
    'rust': 4,
    'go': 4,
    'c': 4,
    'cpp': 4,
    'csharp': 4,
    'java': 4,
    'kotlin': 4,
    'php': 4,
    'zig': 4,
    'swift': 4,
    'scala': 4,
    'perl': 4,
    'sql': 4,
    # 8 spaces: Hard-tab oriented formats
    'make': 8,
    'gitcommit': 8,
}


class FiletypeTabstops(dict[str, int]):
    """
    Dictionary mapping filetype names to tabstop integers.

    Automatically normalizes keys so that looking up or setting any recognized
    alias (e.g. `'py'` vs `'python'`, `'ts'` vs `'typescript'`, `'js'` vs
    `'javascript'`) synchronizes with the canonical filetype name.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__()
        self.update(*args, **kwargs)

    def __setitem__(self, key: str, value: int) -> None:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError('tabstop must be an integer >= 1')
        if value < 1:
            raise ValueError('tabstop must be an integer >= 1')
        canonical = _canonical_filetype(str(key).lower())
        super().__setitem__(canonical, value)

    def __delitem__(self, key: str) -> None:
        canonical = _canonical_filetype(str(key).lower())
        super().__delitem__(canonical)

    def pop(self, key: str, *args: Any) -> Any:
        canonical = _canonical_filetype(str(key).lower())
        return super().pop(canonical, *args)

    def popitem(self) -> tuple[str, int]:
        return super().popitem()

    def setdefault(self, key: str, default: Any = None) -> Any:  # type: ignore[override]
        canonical = _canonical_filetype(str(key).lower())
        if super().__contains__(canonical):
            return super().__getitem__(canonical)
        if isinstance(default, bool) or not isinstance(default, int) or default < 1:
            raise ValueError('tabstop must be an integer >= 1')
        return super().setdefault(canonical, default)

    def update(self, *args: Any, **kwargs: Any) -> None:
        other = dict(*args, **kwargs)
        for k, v in other.items():
            self[k] = v

    def __getitem__(self, key: str) -> int:
        canonical = _canonical_filetype(str(key).lower())
        return super().__getitem__(canonical)

    def get(self, key: str, default: Any = None) -> Any:  # type: ignore[override]
        canonical = _canonical_filetype(str(key).lower())
        if super().__contains__(canonical):
            return super().__getitem__(canonical)
        return default

    def __contains__(self, key: object) -> bool:
        canonical = _canonical_filetype(str(key).lower())
        return super().__contains__(canonical)


class Editor:
    """
    The main class. Containing the whole editor.

    :param config_directory: Place where configuration is stored.
    :param input: (Optionally) `prompt_toolkit.input.Input` object.
    :param output: (Optionally) `prompt_toolkit.output.Output` object.
    """

    @property
    def variation_selector_always_wide(self) -> bool:
        """Instance-local VS16 policy; module flags are only a standalone fallback."""
        return bool(getattr(self, '_variation_selector_always_wide', False))

    @variation_selector_always_wide.setter
    def variation_selector_always_wide(self, value: bool) -> None:
        self._variation_selector_always_wide = bool(value)

    def __init__(self, config_directory='~/.vith', input=None, output=None):
        set_process_name('vith')
        install_prompt_toolkit_thai_patches()
        self.input = input
        self.output = output

        # Vi options.
        self.show_line_numbers = True
        self.highlight_search = True
        self._hlsearch_suppressed = False
        self.paste_mode = False
        self.show_ruler = True
        self.show_wildmenu = True
        # Vim defaults to 'noexpandtab': Tab inserts a literal tab character.
        # ':set expandtab' switches to inserting tabstop spaces instead.
        self.expand_tab = False
        self.tabstop = 8  # Number of spaces that a tab character represents (Vim default: 8).
        self.shiftwidth = 0  # ':set shiftwidth' (0 follows buffer tabstop, Vim convention).
        self._filetype_tabstops = FiletypeTabstops(DEFAULT_FILETYPE_TABSTOPS)
        self.incsearch = True  # Show matches while typing search string.
        self.ignore_case = False  # Ignore case while searching.
        self.smartcase = False  # ':set smartcase': override ignorecase if pattern has uppercase.
        self.autoindent = True  # ':set autoindent': copy previous line indent on Enter/o/O.
        self.enable_mouse_support = True
        self.display_unprintable_characters = True  # ':set list'
        self.enable_jedi = True  # ':set jedi', for Python Jedi completion.
        # Cold-text compression for undo/redo snapshots. Configure in
        # .vithrc with `editor.cold_text_compression = 'zstd'` etc.
        self.cold_text_compression = 'zstd'
        self.cold_text_compression_level = 1
        self.scroll_offset = 0  # ':set scrolloff'
        self.relative_number = False  # ':set relativenumber'
        self.wrap_lines = True  # ':set wrap'
        self.break_indent = False  # ':set breakindent'
        self.cursorline = False  # ':set cursorline'
        self.cursorcolumn = False  # ':set cursorcolumn'
        # Opt-in: treat VS16-promoted narrow emoji bases as 2 cells
        # (szn `variation-selector-always-wide`).
        self._variation_selector_always_wide = False
        self.colorcolumn = []  # ':set colorcolumn'. List of integers.
        self.fileformat = 'unix'  # ':set fileformat' (dos/unix/mac).
        # Live `:render` split preview defaults (configurable via `[render]` in config.toml).
        self.render_default_split = 'vertical'  # 'vertical' | 'horizontal'
        self.render_debounce_ms = 150
        self.render_sync_scroll = True
        self.render_commands = {}
        self._live_render_controllers: list[Any] = []
        # Simple line-register storage for `:d {x}` / `:y {x}` / `:pu {x}`.
        self.registers = {}

        # Snapshot factory defaults from the `:set` catalog for bare `:set`
        # changed-from-default display (docs/bugs/152.md).
        self._default_options = snapshot_default_options(self)

        # Ensure config directory exists (with legacy fallback).
        target_dir = os.path.abspath(expand_location(config_directory))
        if expand_location(config_directory) == expand_location('~/.vith') and not os.path.exists(
            target_dir
        ):
            legacy_dir = os.path.abspath(expand_location('~/.pyvim'))
            if os.path.exists(legacy_dir):
                target_dir = legacy_dir
        self.config_directory = target_dir
        # makedirs for nested config paths; exist_ok guards the race
        # between check and create (docs/bugs/070.md).
        os.makedirs(self.config_directory, exist_ok=True)

        self.window_arrangement = WindowArrangement(self)
        self.message = None
        self._messages: deque[str] = deque(maxlen=200)

        # Load styles. (Mapping from name to Style class.)
        self.styles = generate_built_in_styles()
        self.current_style = get_editor_style_by_name('vim')

        # I/O backends.
        self.io_backends = [
            DirectoryIO(),
            HttpIO(),
            GZipFileIO(),  # Should come before FileIO.
            FileIO(),
        ]

        # Create history and search buffers.
        def handle_action(buff):
            'When enter is pressed in the Vi command line.'
            text = buff.text  # Remember: leave_command_mode resets the buffer.

            # First leave command mode. We want to make sure that the working
            # pane is focussed again before executing the command handlers.
            self.leave_command_mode(append_to_history=True)

            # Execute command.
            handle_command(self, text)

        # The command line renders user-typed text outside the document
        # buffers: feed the sticky Thai-text renderer gate (see vith/thai.py).
        def note_command_text(buff):
            note_document_text(buff.text)

        def note_search_text(buff):
            self.clear_hlsearch_suppression()
            note_document_text(buff.text)

        commands_history = FileHistory(os.path.join(self.config_directory, 'commands_history'))
        self.command_buffer = Buffer(
            accept_handler=handle_action,
            enable_history_search=True,
            completer=create_command_completer(self),
            history=commands_history,
            multiline=False,
            on_text_changed=note_command_text,
        )

        search_buffer_history = FileHistory(os.path.join(self.config_directory, 'search_history'))
        self.search_buffer = Buffer(
            history=search_buffer_history,
            enable_history_search=True,
            multiline=False,
            on_text_changed=note_search_text,
        )

        # Create key bindings registry.
        self.key_bindings = create_key_bindings(self)
        self._builtin_key_binding_count = len(self.key_bindings.bindings)
        self._normal_command_depth = 0

        # Create layout and CommandLineInterface instance.
        self.editor_layout = EditorLayout(self, self.window_arrangement)
        self.application = self._create_application()
        # Thai cell-width helpers read the active Application's owning Editor
        # so the VS16 policy stays per-instance (docs/bugs/223.md).
        self.application._vith_editor = self

        # Hide message when a key is pressed.
        def key_pressed(_):
            self.message = None

        self.application.key_processor.before_key_press += key_pressed

        def after_key_pressed(_):
            if self._hlsearch_suppressed and self.application.layout.has_focus(self.search_buffer):
                self.clear_hlsearch_suppression()

        self.application.key_processor.after_key_press += after_key_pressed

        def after_render(_app):
            if self._live_render_controllers:
                for ctrl in list(self._live_render_controllers):
                    ctrl.on_after_render()

        self.application.after_render += after_render

        # Command line previewer.
        self.previewer = CommandPreviewer(self)

        self.last_substitute_text = ''
        # Kept separate from the shared search state so a `/` search or `:g`
        # cannot hijack the pattern reused by a bare `:s` (docs/bugs/175.md).
        self.last_substitute_pattern = ''

    @property
    def filetype_tabstops(self) -> FiletypeTabstops:
        """Mapping from filetype to tabstop width, configurable in .vithrc."""
        return self._filetype_tabstops

    @filetype_tabstops.setter
    def filetype_tabstops(self, value: dict[str, int]) -> None:
        self._filetype_tabstops = FiletypeTabstops(value)

    def get_filetype_tabstop(self, filetype: str | None) -> int:
        """
        Resolve the tabstop for a given filetype.

        Returns the value from `filetype_tabstops` matching the filetype or any
        of its aliases, falling back to `self.tabstop` (Vim default: 8).
        """
        if not filetype:
            return self.tabstop
        return self._filetype_tabstops.get(filetype, self.tabstop)

    def load_initial_files(self, locations, in_tab_pages=False, hsplit=False, vsplit=False):
        """
        Load a list of files.
        """
        if sum(1 for flag in (in_tab_pages, hsplit, vsplit) if flag) > 1:
            raise ValueError("in_tab_pages, hsplit and vsplit are mutually exclusive.")

        # When no files were given, open at least one empty buffer.
        locations2 = locations or [None]

        # First file
        self.window_arrangement.open_buffer(locations2[0])

        for f in locations2[1:]:
            if in_tab_pages:
                self.window_arrangement.create_tab(f)
            elif hsplit:
                self.window_arrangement.hsplit(location=f)
            elif vsplit:
                self.window_arrangement.vsplit(location=f)
            else:
                self.window_arrangement.open_buffer(f)

        self.window_arrangement.active_tab_index = 0

        if locations and len(locations) > 1:
            self.show_message('%i files loaded.' % len(locations))

    def _create_application(self):
        """
        Create CommandLineInterface instance.
        """

        # Create Application.
        def current_style_getter():
            "DynamicStyle hook for the editor colorscheme."
            return self.current_style

        def paste_mode_enabled():
            return self.paste_mode

        def mouse_support_enabled():
            return self.enable_mouse_support

        application: Any = Application(
            input=self.input,
            output=self.output,
            editing_mode=EditingMode.VI,
            layout=self.editor_layout.layout,
            key_bindings=self.key_bindings,
            style=DynamicStyle(current_style_getter),
            paste_mode=Condition(paste_mode_enabled),
            include_default_pygments_style=False,
            mouse_support=Condition(mouse_support_enabled),
            full_screen=True,
            enable_page_navigation_bindings=True,
        )

        # Handle command line previews.
        # (e.g. when typing ':colorscheme blue', it should already show the
        # preview before pressing enter.)
        def preview(_):
            if self.application.layout.has_focus(self.command_buffer):
                self.previewer.preview(self.command_buffer.text)

        self.command_buffer.on_text_changed += preview

        application.vi_state.input_mode = InputMode.NAVIGATION
        return application

    @property
    def current_editor_buffer(self) -> EditorBuffer | None:
        """
        Return the `EditorBuffer` that is currently active, or `None` when
        no editor buffer matches `application.current_buffer`.
        """
        current_buffer = self.application.current_buffer
        if (
            current_buffer is None
            or current_buffer is self.command_buffer
            or current_buffer is self.search_buffer
        ):
            return None

        # Fast path: the active editor buffer.
        active = self.window_arrangement.active_editor_buffer
        if active is not None and active.buffer == current_buffer:
            return active

        # O(1) back-reference attached by `EditorBuffer.__init__`.
        eb_ref = getattr(current_buffer, '_vith_editor_buffer_ref', None)
        if eb_ref is not None:
            eb = eb_ref()
            if eb is not None and eb in self.window_arrangement.editor_buffers:
                return eb

        # Fallback for synthetic/replaced buffers in tests.
        for b in self.window_arrangement.editor_buffers:
            if b.buffer == current_buffer:
                return b

        return None

    @property
    def add_key_binding(self):
        """
        Shortcut for adding new key bindings.
        (Mostly useful for a vithrc file, that receives this Editor instance
        as input.)
        """
        return self.key_bindings.add

    def show_message(self, message, *, record: bool = True):
        """
        Set a warning message. The layout will render it as a "pop-up" at the
        bottom, and record non-empty messages in the bounded history log.
        """
        self.message = message
        if message:
            # The message toolbar renders this text; report it to the sticky
            # Thai gate so SARA-AM sanitization also covers status banners
            # (docs/bugs/146.md).
            note_document_text(str(message))
        if record and message:
            self._messages.append(str(message))

    def suppress_hlsearch(self) -> None:
        """
        Temporarily clear search highlights until the next search (`:noh`).
        """
        self._hlsearch_suppressed = True
        try:
            self.application.invalidate()
        except Exception:
            pass

    def clear_hlsearch_suppression(self) -> None:
        """
        Re-enable search highlights when a new search or match jump starts.
        """
        if self._hlsearch_suppressed:
            self._hlsearch_suppressed = False
            try:
                self.application.invalidate()
            except Exception:
                pass

    def should_ignore_case(self, pattern: str | None) -> bool:
        """
        Return whether search/substitute should fold case for `pattern`,
        honoring both `ignore_case` and `smartcase` (when both are enabled,
        any literal uppercase character in `pattern` switches the match to
        case-sensitive, matching Vim).
        """
        return resolve_ignore_case(self.ignore_case, self.smartcase, pattern)

    def use_colorscheme(self, name='default', *, report: bool = True) -> bool:
        """
        Apply new colorscheme by name. Returns whether it was found.

        `:colorscheme` reports E185 for an unknown name; command previews
        apply candidate styles silently (docs/bugs/147.md).
        """
        try:
            # Reuse the lazily compiled/cached styles mapping instead of
            # recompiling the Pygments style on every preview keystroke
            # (docs/bugs/227.md).
            self.current_style = self.styles[name]
        except (KeyError, pygments.util.ClassNotFound):
            if report:
                self.show_message('E185: Cannot find color scheme %s' % name)
            return False
        return True

    def sync_with_prompt_toolkit(self):
        """
        Update the prompt-toolkit Layout and FocusStack.
        """
        # After executing a command, make sure that the layout of
        # prompt-toolkit matches our WindowArrangement.
        self.editor_layout.update()

        # Decay the sticky Thai-renderer gate when every buffer is plain
        # ASCII again (called here on buffer switches / commands, never
        # per keystroke; docs/bugs/072.md).
        reset_thai_text_gate_if_all_buffers_ascii(self.window_arrangement)

        # Make sure that the focus stack of prompt-toolkit has the current
        # page.
        window = self.window_arrangement.active_pt_window
        if window:
            self.application.layout.focus(window)

        self.update_title()

    def get_title(self) -> str:
        """
        Return the terminal window title for this editor session.
        """
        eb = self.window_arrangement.active_editor_buffer
        if eb and eb.location:
            return f"vith - {short_display_name(eb.location)}"
        return "vith"

    def update_title(self) -> None:
        """
        Send ANSI escape sequences to update terminal / tmux / szn window title.
        """
        title = self.get_title()
        self._write_terminal_title(title)

    def _write_terminal_title(self, title: str) -> None:
        """
        Write window title escape sequences to terminal output.
        """
        out = getattr(self.application, 'output', None)
        if not out:
            return

        clean_title = title.replace('\x1b', '').replace('\x07', '')

        # 1. prompt_toolkit output set_title (OSC 2 on VT100, Win32 console API on Windows)
        try:
            out.set_title(clean_title)
        except Exception:
            pass

        # 2. OSC 0 (sets both window title and icon name for xterm/iTerm2/Alacritty/Terminal.app)
        # 3. If running under tmux / screen, also send the screen-title sequence: ESC k <title> ESC \
        try:
            extra_seq = _terminal_title_extra_sequence(clean_title)
            out.write_raw(extra_seq)
            out.flush()
        except Exception:
            pass

    def _clear_terminal_title(self) -> None:
        """
        Restore or clear terminal / tmux / szn window title on exit.
        """
        out = getattr(self.application, 'output', None)
        if not out:
            return

        try:
            out.clear_title()
            extra_seq = _terminal_title_extra_sequence('')
            out.write_raw(extra_seq)
            out.flush()
        except Exception:
            pass

    def _leave_command_and_search_modes(self):
        """Cancel pending preview/search state before focusing a read-only split (207)."""
        layout = self.application.layout
        if layout.has_focus(self.command_buffer):
            self.leave_command_mode(append_to_history=False)
        elif layout.has_focus(self.search_buffer):
            self.search_buffer.reset()
            self._clear_pending_counted_inserts()
            layout.focus_last()
        self._clear_pending_counted_inserts()
        self.application.vi_state.input_mode = InputMode.NAVIGATION

    def show_info(self, text: str, title: str | None = None) -> None:
        """
        Display read-only informational text in a new horizontal split inside
        vith (like `:help` and `:compression`, close with `:q`).
        """
        self._leave_command_and_search_modes()
        if self.window_arrangement.active_tab is None:
            eb = self.window_arrangement._get_or_create_editor_buffer(text=text)
        else:
            self.window_arrangement.hsplit(text=text)
            eb = self.window_arrangement.active_editor_buffer

        if eb is not None:
            eb.is_scratch = True
            eb.scratch_title = title
            eb.buffer.read_only = to_filter(True)

        self.sync_with_prompt_toolkit()

    def show_help(self):
        """
        Show help in new window.
        """
        self.show_info(HELP_TEXT, title='[Help]')

    def show_compression(self):
        """
        Show cold-text compression stats in new window (like `:help`, close
        with `:q`).
        """
        eb = self.window_arrangement.active_editor_buffer
        buffer = eb.buffer if eb is not None else None
        stats_getter = getattr(buffer, 'cold_text_stats', None)

        if stats_getter is None:
            self.show_message('Cold-text compression is not available for this buffer.')
            return

        text = '\n'.join(format_cold_text_stats(stats_getter()))
        self.show_info(text, title='[Compression]')

    def run(self):
        """
        Run the event loop for the interface.
        This starts the interaction.
        """
        # Make sure everything is in sync, before starting.
        self.sync_with_prompt_toolkit()

        def pre_run():
            # Start in navigation mode.
            self.application.vi_state.input_mode = InputMode.NAVIGATION
            self._clear_pending_counted_inserts()

            # Update terminal title on launch.
            self.update_title()

            # Now that the event loop is running, start the reporters that
            # were deferred at startup (see EditorBuffer.run_reporter).
            for eb in self.window_arrangement.editor_buffers:
                eb.run_reporter()
            for ctrl in list(self._live_render_controllers):
                ctrl.schedule(immediate=True)

        try:
            # Run eventloop of prompt_toolkit, propagating the result set by
            # `:cq` (docs/bugs/145.md).
            result = self.application.run(pre_run=pre_run)
        finally:
            self._clear_terminal_title()
        return result

    def _clear_pending_counted_inserts(self):
        """Drop counted-insert replay state left by an interrupted insert (204)."""
        for eb in self.window_arrangement.editor_buffers:
            buff = getattr(eb, 'buffer', None)
            if buff is not None and hasattr(buff, '_vith_insert_repeat'):
                buff._vith_insert_repeat = None

    def enter_command_mode(self, initial_text: str = '') -> None:
        """
        Go into command mode, optionally pre-populating the command buffer.
        """
        self._clear_pending_counted_inserts()
        self.application.layout.focus(self.command_buffer)
        self.application.vi_state.input_mode = InputMode.INSERT

        self.previewer.save()
        if initial_text:
            self.command_buffer.document = Document(initial_text, len(initial_text))

    def leave_command_mode(self, append_to_history=False):
        """
        Leave command mode. Focus document window again.
        """
        self.previewer.restore()

        self._clear_pending_counted_inserts()
        self.application.layout.focus_last()
        self.application.vi_state.input_mode = InputMode.NAVIGATION

        self.command_buffer.reset(append_to_history=append_to_history)
