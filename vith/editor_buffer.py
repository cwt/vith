import asyncio
import os
import weakref
from typing import Any

from prompt_toolkit.application.current import get_app
from prompt_toolkit.document import Document
from prompt_toolkit.filters import to_filter

from vith.cold_text import ColdTextBuffer, make_cold_text_compressor
from vith.completion import DocumentCompleter
from vith.io.backends import expand_env_vars, expand_location
from vith.io.base import find_backend
from vith.lexer import sniff_filetype_from_text
from vith.reporting import checks_python_file, report
from vith.thai import current_line_and_col, is_thai_gate_latched, note_document_text

__all__ = ('EditorBuffer', 'UNTITLED_LABEL', 'is_same_write_target', 'normalize_write_target')

UNTITLED_LABEL = '[New file]'


def normalize_write_target(location: str | None) -> str | None:
    """Normalize a write path for same-file comparison (tilde/env/symlink safe; docs/bugs/152.md)."""
    if not location:
        return None
    if '://' in location:
        return location
    return os.path.realpath(os.path.abspath(expand_location(location)))


def is_same_write_target(current_location: str | None, target_location: str | None) -> bool:
    """Whether `target_location` resolves to the same file as `current_location`."""
    if not current_location or not target_location:
        return False
    if current_location == target_location:
        return True
    return normalize_write_target(current_location) == normalize_write_target(target_location)


class EditorBuffer:
    """
    Wrapper around a `prompt-toolkit` buffer.

    A 'prompt-toolkit' `Buffer` doesn't know anything about files, changes,
    etc... This wrapper contains the necessary data for the editor.
    """

    def __init__(self, editor, location=None, text=None) -> None:
        assert location is None or isinstance(location, str)
        assert text is None or isinstance(text, str)
        assert not (location and text)

        self._editor_ref = weakref.ref(editor)
        self.location = expand_env_vars(location) if location is not None else None
        self.encoding = 'utf-8'
        # Pygments lexer alias overriding filename-based detection
        # (`:set filetype=python`). `None` means auto-detect.
        self.filetype_override: str | None = None
        self._detected_filetype: str | None = None
        self._tabstop_override: int | None = None
        self._shiftwidth_override: int | None = None
        # Last visual selection 0-based line bounds `(start_row, end_row)`
        # recorded when entering command mode from selection (`'<`, `'>`).
        self.visual_marks: tuple[int, int] | None = None
        # Buffer-local named marks (`m{a-z}` -> `(row, col)` 0-based).
        self.marks: dict[str, tuple[int, int]] = {}

        #: is_new: True when this file does not yet exist in the storage.
        self.is_new = True
        self.is_scratch = False
        self.is_render_preview = False
        self.scratch_title: str | None = None
        self._rendered_lines: list[Any] | None = None
        self._rendered_version = 0
        self._live_render_controllers: list[Any] = []
        self._owned_render_controller: Any = None

        # Empty if not in file explorer mode, directory path otherwise.
        self.isdir = False
        self._had_trailing_newline = True
        # A configured `fileformat` is applied before any buffer exists, so
        # inherit it here; `_read()` still overrides it when file content
        # reveals a different line-ending style (docs/bugs/179.md).
        editor_ref = self.editor
        configured = getattr(editor_ref, 'fileformat', 'unix') if editor_ref is not None else 'unix'
        self._fileformat = configured if configured in ('dos', 'unix', 'mac') else 'unix'

        # Read text.
        unreadable = False
        if self.location:
            text = self._read(self.location)
            if text is None:
                # Not a new file: an unknown backend or a read failure.
                # Show a fallback-empty buffer, but keep it read-only so the
                # user cannot "save" over something they never saw
                # (docs/bugs/070.md).
                text = ''
                unreadable = True
        else:
            text = text or ''

        self._file_content = text

        # Sticky Thai-text renderer gate (see vith/thai.py): O(1) for
        # ASCII, so this is effectively free per buffer creation.
        note_document_text(self.location)
        note_document_text(self._file_content)

        # List of reporting errors, and index of those errors by line.
        # Initialized before `ColdTextBuffer` so `_on_text_changed` can never
        # observe uninitialized attributes during construction.
        self.report_errors: list[Any] = []
        self.report_errors_by_line: dict[int, Any] = {}
        self._reporter_is_running = False
        self._run_reporter_again = False

        # True when some changes are not yet written to file.
        self._has_unsaved_changes = False
        self._suppress_text_changed = 0
        self._last_text_len = len(text)
        self._last_line_count = text.count('\n') + 1

        # Create Buffer. Undo/redo snapshots are cold text: store them
        # compressed, while the active buffer text remains a plain string.
        compressor, compression_name, compression_level = make_cold_text_compressor(
            editor.cold_text_compression,
            editor.cold_text_compression_level,
        )
        self.buffer = ColdTextBuffer(
            multiline=True,
            completer=DocumentCompleter(editor, self),
            document=Document(text, 0),
            on_text_changed=self._on_text_changed,
            read_only=self.isdir or unreadable,
            cold_compressor=compressor,
            cold_compression_name=compression_name,
            cold_compression_level=compression_level,
        )
        self.buffer._vith_editor_buffer_ref = weakref.ref(self)  # type: ignore[attr-defined]

    @property
    def editor(self):
        """Back reference to the Editor."""
        return self._editor_ref()

    @property
    def filetype(self) -> str:
        """
        Effective filetype of the buffer (canonical Pygments lexer alias).

        Returns the `:set filetype` override if set, else the detected filetype
        from the buffer location or content sniffing, or an empty string.
        """
        if self.filetype_override:
            return self.filetype_override.lower()
        if self._detected_filetype is not None:
            return self._detected_filetype
        if self.location:
            try:
                from pygments.lexers import get_lexer_for_filename

                lexer = get_lexer_for_filename(self.location)
                aliases = getattr(lexer, 'aliases', None)
                if aliases:
                    self._detected_filetype = aliases[0].lower()
                    return self._detected_filetype
            except Exception:
                pass
        text = getattr(self, '_file_content', '') or ''
        if not text and hasattr(self, 'buffer'):
            text = self.buffer.text
        if text:
            sniffed = sniff_filetype_from_text(text)
            if sniffed:
                self._detected_filetype = sniffed
                return self._detected_filetype
        self._detected_filetype = ''
        return ''

    @property
    def tabstop(self) -> int:
        """
        Effective visual tab width for this buffer.

        Resolution order:
        1. Explicit buffer-local override (`self._tabstop_override`), e.g. from
           `:setlocal tabstop=N` or `:set tabstop=N`.
        2. Filetype-specific setting from `editor.filetype_tabstops` matching the
           buffer's effective `filetype`.
        3. Global editor default (`editor.tabstop`).
        """
        if self._tabstop_override is not None:
            return self._tabstop_override
        editor = self.editor
        if editor is not None:
            return editor.get_filetype_tabstop(self.filetype)
        return 8

    @tabstop.setter
    def tabstop(self, value: int | None) -> None:
        if value is not None and value < 1:
            raise ValueError('tabstop must be at least 1')
        self._tabstop_override = value

    @property
    def shiftwidth(self) -> int:
        """
        Effective indentation width for `>>`, `<<`, and `<C-t>`.

        Follows Vim's `shiftwidth=0` convention: when the configured shiftwidth
        (buffer-local override or global `editor.shiftwidth`) is `0`, returns
        the buffer's effective `tabstop`.
        """
        if self._shiftwidth_override is not None:
            raw = self._shiftwidth_override
        else:
            editor = self.editor
            raw = getattr(editor, 'shiftwidth', 0) if editor is not None else 0
        return self.tabstop if raw == 0 else raw

    @shiftwidth.setter
    def shiftwidth(self, value: int | None) -> None:
        if value is not None and value < 0:
            raise ValueError('shiftwidth must be non-negative')
        self._shiftwidth_override = value

    def _on_text_changed(self, *args):
        "Buffer text changed."
        if self._suppress_text_changed > 0:
            return
        # Amortized-O(1) dirty flag (docs/bugs/072.md): typing/deleting change
        # the text length, so the O(1) length check resolves the flag without
        # a linear memcmp; only same-length edits (overwrite/paste-over)
        # still pay one string comparison. The previous implementation ran a
        # full-text memcmp per keystroke on multi-MB buffers.
        text = self.buffer.text
        if len(text) != len(self._file_content):
            self._has_unsaved_changes = True
        else:
            self._has_unsaved_changes = text != self._file_content

        # Keep the sticky Thai-text renderer gate up to date without copying
        # the full buffer (`current_line`) or splitting `document.lines` when
        # the text is ASCII or the gate is already latched (docs/bugs/097.md, 157.md).
        if not is_thai_gate_latched() and not text.isascii():
            line, _col = current_line_and_col(self.buffer.document)
            note_document_text(line)
            if not is_thai_gate_latched():
                line_count = text.count('\n') + 1
                if line_count != self._last_line_count or abs(len(text) - self._last_text_len) > 1:
                    note_document_text(text)
                self._last_line_count = line_count
        self._last_text_len = len(text)

        self.run_reporter()
        if self._live_render_controllers:
            for ctrl in list(self._live_render_controllers):
                ctrl.schedule()

    def close_live_render_previews(self) -> None:
        """Stop any live `:render` controller owned by or watching this buffer."""
        owned = getattr(self, '_owned_render_controller', None)
        if owned is not None:
            owned.close()
        for ctrl in list(getattr(self, '_live_render_controllers', ())):
            ctrl.close()

    @property
    def has_unsaved_changes(self):
        """
        True when some changes are not yet written to file.
        """
        return self._has_unsaved_changes

    @property
    def fileformat(self) -> str:
        """Line-ending style used when writing this buffer (`:set ff`)."""
        return self._fileformat

    @fileformat.setter
    def fileformat(self, value: str) -> None:
        fmt = str(value).strip().lower()
        if fmt not in ('dos', 'unix', 'mac'):
            raise ValueError('Invalid fileformat: %s' % value)
        self._fileformat = fmt

    def line_ending(self) -> str:
        """End-of-line sequence for the current `fileformat`."""
        return {'dos': '\r\n', 'mac': '\r', 'unix': '\n'}[self._fileformat]

    @property
    def in_file_explorer_mode(self):
        """
        True when we are in file explorer mode (when this is a directory).
        """
        return self.isdir

    def _read(self, location):
        """
        Read file I/O backend.

        Returns the decoded text, `''` for a new file, or `None` when the
        location cannot be read at all (unknown backend or read failure) —
        callers must not silently treat `None` as a new empty file.
        """
        io = find_backend(self.editor.io_backends, location)
        if io is None:
            self.editor.show_message('Cannot read: %r' % location)
            return None

        exists = io.exists(location)
        self.isdir = io.isdir(location)

        if exists:
            # File exists. Read it.
            self.is_new = False
            try:
                text, self.encoding = io.read(location)

                # Record the dominant line ending so writes preserve it
                # (docs/bugs/140.md). Guard with `'\r' in text` so Unix files
                # skip 4 redundant full-file scans/copies (docs/bugs/166.md).
                if '\r' in text:
                    crlf_count = text.count('\r\n')
                    lone_cr_count = text.count('\r') - crlf_count
                    if crlf_count and crlf_count >= lone_cr_count:
                        self.fileformat = 'dos'
                    elif lone_cr_count:
                        self.fileformat = 'mac'
                    else:
                        self.fileformat = 'unix'
                    text = text.replace('\r\n', '\n').replace('\r', '\n')
                else:
                    self.fileformat = 'unix'

                # Drop trailing newline while editing.
                # (prompt-toolkit doesn't enforce the trailing newline.)
                if text.endswith('\n'):
                    self._had_trailing_newline = True
                    text = text[:-1]
                else:
                    self._had_trailing_newline = False
            except Exception as e:
                self.editor.show_message('Cannot read %r: %r' % (location, e))
                return None
            else:
                return text
        else:
            # File doesn't exist.
            self.is_new = True
            self._had_trailing_newline = True
            return ''

    def reload(self):
        """
        Reload file again from storage.
        """
        if self.location is None:
            # Untitled buffers have nothing to reload from (docs/bugs/189.md).
            self.editor.show_message('E32: No file name')
            return
        existed_before = not self.is_new
        location_snapshot = self.location
        text = self._read(self.location)
        if text is None:
            # Not readable (backend dropped/unknown scheme): keep current
            # contents instead of wiping the buffer with '' (docs/bugs/070.md).
            return
        if existed_before and self.is_new and not text:
            # The file vanished since we read it. A plain `:e` used to
            # silently blank the saved buffer with no message — Vim shows
            # E211 and keeps the buffer contents (docs/bugs/088.md).
            self.editor.show_message("E211: File no longer exists: %r" % (location_snapshot,))
            self.is_new = False
            return
        cursor_position = min(self.buffer.cursor_position, len(text))

        # `reset()` replaces the document AND clears undo/redo history and
        # version counters. A plain `document =` assignment leaves snapshots
        # from the previous file alive, so `u` after a clean reload resurrects
        # pre-reload content (docs/bugs/177.md). It also bypasses the
        # read-only filter that a document assignment trips (docs/bugs/133.md).
        self.buffer.reset(document=Document(text, cursor_position))
        if self.isdir:
            # `:e` may replace a file with a directory: keep the explorer
            # read-only, even if the old filter allowed editing.
            self.buffer.read_only = to_filter(True)

        self._file_content = text
        self._has_unsaved_changes = False
        self._last_text_len = len(text)
        self._last_line_count = self.buffer.document.text.count('\n') + 1
        self._detected_filetype = None
        # Diagnostics/lexer state belongs to the previous content; discard it
        # before the reporter runs on the reloaded text (docs/bugs/133.md).
        self.report_errors = []
        self.report_errors_by_line = {}
        # Feed the sticky Thai-gate with the reloaded text, else the first
        # render after reload runs unsanitized until the next edit.
        note_document_text(text)
        self.run_reporter()
        if self._live_render_controllers:
            for ctrl in list(self._live_render_controllers):
                ctrl.schedule()

    def _write_with_backend(self, io, target_location, payload):
        """
        Write `payload` via `io`, falling back from an unrepresentable
        encoding to UTF-8 (docs/bugs/070.md, 110.md).
        """
        try:
            io.write(target_location, payload, self.encoding)
        except UnicodeEncodeError:
            # The buffer was decoded with a lossy fallback encoding
            # (typically latin-1, which never fails to decode) and now
            # contains characters it cannot encode (e.g. Thai typed into
            # such a file). Save as utf-8 instead of refusing the save.
            # Mutate `self.encoding` only after the fallback write succeeds.
            old_encoding = self.encoding
            io.write(target_location, payload, 'utf-8')
            self.encoding = 'utf-8'
            self.editor.show_message(
                'Content not representable in %r; saved as %r' % (old_encoding, 'utf-8')
            )

    def _write_forced(self, io, target_location, payload):
        """
        Force-write a read-only local file: grant owner-write, write once,
        then restore the original mode. Returns whether the write succeeded
        (a message has been shown otherwise).
        """
        try:
            st_mode = os.stat(target_location).st_mode
        except OSError as e:
            self.editor.show_message('%s' % e)
            return False
        try:
            os.chmod(target_location, st_mode | 0o200)
        except OSError as e:
            self.editor.show_message('%s' % e)
            return False
        try:
            self._write_with_backend(io, target_location, payload)
        except Exception as e:
            self.editor.show_message('%s' % e)
            return False
        finally:
            try:
                os.chmod(target_location, st_mode)
            except OSError:
                pass
        return True

    def write(self, location=None, force=False, *, rename=False):
        """
        Write file to I/O backend.

        Returns ``True`` when the file was written, ``False`` otherwise
        (a message has been shown). With ``force`` (``:w!``), a read-only
        local file is temporarily made writable for one write and its mode
        is restored afterwards (docs/bugs/117.md).

        Strict Vim parity (Decision D1): writing to a different ``location``
        from an already-named buffer writes a copy without renaming the buffer
        or clearing ``has_unsaved_changes`` unless ``rename=True`` (``:saveas``).
        Writing an untitled buffer names it and marks it clean.
        """
        # Resolve the target location without mutating state up front.
        target_location = expand_env_vars(location) if location is not None else self.location
        if not target_location:
            self.editor.show_message('Unknown location: %r' % location)
            return False

        # Guard buffer-local `readonly` option (`:set ro`) unless forced (`:w!`).
        # Normalize paths so writing to the buffer's own file via a symlink or
        # relative path cannot bypass `:set ro` or leave dirty state stale
        # (docs/bugs/152.md).
        same_target = is_same_write_target(self.location, target_location)
        read_only = self.buffer.read_only
        is_ro = bool(read_only()) if callable(read_only) else bool(read_only)
        if is_ro and not force and (location is None or same_target or self.location is None):
            self.editor.show_message("E45: 'readonly' option is set (add ! to override)")
            return False

        # Find I/O backend that handles this location.
        io = find_backend(self.editor.io_backends, target_location)
        if io is None:
            self.editor.show_message('Unknown location: %r' % target_location)
            return False

        # Write it.
        try:
            line_ending = self.line_ending()
            eol = line_ending if self._had_trailing_newline else ''
            payload = self.buffer.text.replace('\n', line_ending) + eol
            try:
                self._write_with_backend(io, target_location, payload)
            except PermissionError:
                if not force:
                    raise
                if not self._write_forced(io, target_location, payload):
                    return False
        except Exception as e:
            # E.g. "No such file or directory."
            self.editor.show_message('%s' % e)
            return False
        else:
            updates_buffer = self.location is None or same_target or bool(rename)
            if updates_buffer:
                new_location = self.location if (same_target and not rename) else target_location
                location_changed = new_location != self.location
                self.is_new = False
                # Only mutate location after a successful write.
                self.location = new_location
                # When the save succeeds: update: _file_content.
                self._file_content = self.buffer.text
                self._has_unsaved_changes = False
                if location_changed:
                    self._detected_filetype = None
                    self.run_reporter()
                    if self._live_render_controllers:
                        for ctrl in list(self._live_render_controllers):
                            ctrl.schedule()
                    note_document_text(self.location)
                    if self.editor is not None:
                        self.editor.update_title()
            return True

    def get_display_name(self, short=False):
        """
        Return name as displayed.
        """
        if self.location is None:
            return self.scratch_title or UNTITLED_LABEL
        elif short:
            return os.path.basename(self.location)
        else:
            return self.location

    def __repr__(self):
        return '%s(buffer=%r)' % (self.__class__.__name__, self.buffer)

    def run_reporter(self):
        "Buffer text changed."
        # 1. Fast exit BEFORE touching _reporter_is_running:
        #    Skip unnamed buffers and non-Python files immediately. (report()
        #    in reporting.py only knows how to check .py files.)
        if not checks_python_file(self.location):
            if self.report_errors:
                self.report_errors = []
                self.report_errors_by_line = {}
                # The cleared index must be reflected immediately: without an
                # invalidate, stale pyflakes fragments/toolbar survive until
                # the next edit (docs/bugs/069.md).
                try:
                    get_app().invalidate()
                except RuntimeError:
                    pass  # No Application is currently running (e.g. startup).
            return

        # 2. Acquire the running event loop BEFORE setting _reporter_is_running.
        #    If called before Application.run() (e.g. CLI startup on Python
        #    3.14+, where asyncio.get_event_loop() raises RuntimeError when no
        #    loop is running), leave _reporter_is_running = False so that
        #    Editor.run() can trigger the reporter once the loop is active.
        #
        #    `loop` is initialized here so all dispatches below see a defined
        #    binding regardless of branch (docs/bugs/016.md).
        loop = None
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        if self._reporter_is_running:
            self._run_reporter_again = True
            return

        self._reporter_is_running = True
        text = self.buffer.text
        location = self.location
        document = self.buffer.document

        def in_executor():
            # Call reporter with (location, document), as in reporting.report.
            # An unexpected worker failure must still schedule `ready()`, or
            # `_reporter_is_running` stays True and diagnostics freeze forever
            # (docs/bugs/190.md).
            reporter_error: Exception | None = None
            try:
                report_errors = report(location, document)
            except Exception as exc:
                report_errors = []
                reporter_error = exc

            def ready():
                self._reporter_is_running = False
                if reporter_error is not None:
                    editor = self.editor
                    if editor is not None:
                        editor.show_message(f'Reporter error: {reporter_error}')

                # If the text has not changed while running in the worker
                # thread, update report_errors and only invalidate the UI
                # when the errors actually changed. (report_errors was NOT
                # cleared before the background check, so unchanged errors
                # do not cause flicker.)
                if text == self.buffer.text and not self._run_reporter_again:
                    if report_errors != self.report_errors:
                        self.report_errors = report_errors
                        by_line = {}
                        for err in report_errors:
                            by_line.setdefault(err.lineno, []).append(err)
                        self.report_errors_by_line = by_line
                        get_app().invalidate()
                else:
                    self._run_reporter_again = False
                    self.run_reporter()

            # Dispatch the callback back to the main thread in a thread-safe
            # way, from the executor thread (never from inside `ready`).
            loop.call_soon_threadsafe(ready)

        loop.run_in_executor(None, in_executor)
