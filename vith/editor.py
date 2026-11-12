"""
The main editor class.

Usage::

    files_to_edit = ['file1.txt', 'file2.py']
    e = Editor(files_to_edit)
    e.run()  # Runs the event loop, starts interaction.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import TYPE_CHECKING, Any

import pygments
from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.enums import EditingMode
from prompt_toolkit.filters import Condition
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding.vi_state import InputMode
from prompt_toolkit.styles import DynamicStyle

from .cold_text import format_cold_text_stats
from .commands.completer import create_command_completer
from .commands.handler import handle_command
from .commands.preview import CommandPreviewer
from .help import HELP_TEXT
from .io import DirectoryIO, FileIO, GZipFileIO, HttpIO
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


@lru_cache(maxsize=512)
def _canonical_filetype(name: str) -> str:
    """Resolve a filetype name or alias to its primary canonical alias."""
    try:
        from pygments.lexers import get_lexer_by_name

        lexer = get_lexer_by_name(name)
        aliases = getattr(lexer, 'aliases', ())
        if aliases:
            return aliases[0].lower()
    except Exception:
        pass
    return name.lower()


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
        canonical = _canonical_filetype(str(key).lower())
        super().__setitem__(canonical, int(value))

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

    def __init__(self, config_directory='~/.vith', input=None, output=None):
        set_process_name('vith')
        install_prompt_toolkit_thai_patches()
        self.input = input
        self.output = output

        # Vi options.
        self.show_line_numbers = True
        self.highlight_search = True
        self.paste_mode = False
        self.show_ruler = True
        self.show_wildmenu = True
        self.expand_tab = True  # Insert spaces instead of tab characters.
        self.tabstop = 8  # Number of spaces that a tab character represents (Vim default: 8).
        self._filetype_tabstops = FiletypeTabstops(DEFAULT_FILETYPE_TABSTOPS)
        self.incsearch = True  # Show matches while typing search string.
        self.ignore_case = False  # Ignore case while searching.
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
        self.colorcolumn = []  # ':set colorcolumn'. List of integers.

        # Ensure config directory exists (with legacy fallback).
        target_dir = os.path.abspath(os.path.expanduser(config_directory))
        if os.path.expanduser(config_directory) == os.path.expanduser(
            '~/.vith'
        ) and not os.path.exists(target_dir):
            legacy_dir = os.path.abspath(os.path.expanduser('~/.pyvim'))
            if os.path.exists(legacy_dir):
                target_dir = legacy_dir
        self.config_directory = target_dir
        # makedirs for nested config paths; exist_ok guards the race
        # between check and create (docs/bugs/070.md).
        os.makedirs(self.config_directory, exist_ok=True)

        self.window_arrangement = WindowArrangement(self)
        self.message = None

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
            on_text_changed=note_command_text,
        )

        # Create key bindings registry.
        self.key_bindings = create_key_bindings(self)

        # Create layout and CommandLineInterface instance.
        self.editor_layout = EditorLayout(self, self.window_arrangement)
        self.application = self._create_application()

        # Hide message when a key is pressed.
        def key_pressed(_):
            self.message = None

        self.application.key_processor.before_key_press += key_pressed

        # Command line previewer.
        self.previewer = CommandPreviewer(self)

        self.last_substitute_text = ''

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

        application = Application(
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

    def show_message(self, message):
        """
        Set a warning message. The layout will render it as a "pop-up" at the
        bottom.
        """
        self.message = message

    def use_colorscheme(self, name='default'):
        """
        Apply new colorscheme. (By name.)
        """
        try:
            self.current_style = get_editor_style_by_name(name)
        except pygments.util.ClassNotFound:
            pass

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
            basename = os.path.basename(eb.location) or eb.location
            return f"vith - {basename}"
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

    def show_help(self):
        """
        Show help in new window.
        """
        self.window_arrangement.hsplit(text=HELP_TEXT)
        self.sync_with_prompt_toolkit()  # Show new window.

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
        self.window_arrangement.hsplit(text=text)
        self.sync_with_prompt_toolkit()  # Show new window.

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

            # Update terminal title on launch.
            self.update_title()

            # Now that the event loop is running, start the reporters that
            # were deferred at startup (see EditorBuffer.run_reporter).
            for eb in self.window_arrangement.editor_buffers:
                eb.run_reporter()

        try:
            # Run eventloop of prompt_toolkit.
            self.application.run(pre_run=pre_run)
        finally:
            self._clear_terminal_title()

    def enter_command_mode(self):
        """
        Go into command mode.
        """
        self.application.layout.focus(self.command_buffer)
        self.application.vi_state.input_mode = InputMode.INSERT

        self.previewer.save()

    def leave_command_mode(self, append_to_history=False):
        """
        Leave command mode. Focus document window again.
        """
        self.previewer.restore()

        self.application.layout.focus_last()
        self.application.vi_state.input_mode = InputMode.NAVIGATION

        self.command_buffer.reset(append_to_history=append_to_history)
