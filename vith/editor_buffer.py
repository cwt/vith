import asyncio
import os
import weakref

from prompt_toolkit.application.current import get_app
from prompt_toolkit.document import Document

from vith.cold_text import ColdTextBuffer, make_cold_text_compressor
from vith.completion import DocumentCompleter
from vith.reporting import checks_python_file, report
from vith.thai import note_document_text

__all__ = ('EditorBuffer', 'UNTITLED_LABEL')

UNTITLED_LABEL = '[New file]'


class EditorBuffer:
    """
    Wrapper around a `prompt-toolkit` buffer.

    A 'prompt-toolkit' `Buffer` doesn't know anything about files, changes,
    etc... This wrapper contains the necessary data for the editor.
    """

    def __init__(self, editor, location=None, text=None):
        assert location is None or isinstance(location, str)
        assert text is None or isinstance(text, str)
        assert not (location and text)

        self._editor_ref = weakref.ref(editor)
        self.location = location
        self.encoding = 'utf-8'
        # Pygments lexer alias overriding filename-based detection
        # (`:set filetype=python`). `None` means auto-detect.
        self.filetype_override: str | None = None

        #: is_new: True when this file does not yet exist in the storage.
        self.is_new = True

        # Empty if not in file explorer mode, directory path otherwise.
        self.isdir = False
        self._had_trailing_newline = True

        # Read text.
        unreadable = False
        if location:
            text = self._read(location)
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
        self.report_errors = []
        self.report_errors_by_line = {}
        self._reporter_is_running = False
        self._run_reporter_again = False

        # True when some changes are not yet written to file.
        self._has_unsaved_changes = False
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
        self.buffer._vith_editor_buffer_ref = weakref.ref(self)

    @property
    def editor(self):
        """Back reference to the Editor."""
        return self._editor_ref()

    def _on_text_changed(self, *args):
        "Buffer text changed."
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

        # Keep the sticky Thai-text renderer gate up to date (O(1) for ASCII
        # content; one C-regex scan per edit while unlatched and non-ASCII).
        note_document_text(self.buffer.document.current_line)

        # Multi-line edits (paste, undo/redo, full-buffer replacement) may
        # introduce Thai on rows other than current_line (docs/bugs/097.md).
        # When unlatched, note full text if line count changed or multiple chars changed.
        lines = self.buffer.document.lines
        if len(lines) != self._last_line_count or abs(len(text) - self._last_text_len) > 1:
            note_document_text(text)
        self._last_text_len = len(text)
        self._last_line_count = len(lines)

        self.run_reporter()

    @property
    def has_unsaved_changes(self):
        """
        True when some changes are not yet written to file.
        """
        return self._has_unsaved_changes

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
        io = next(
            (b for b in self.editor.io_backends if b.can_open_location(location)),
            None,
        )
        if io is None:
            self.editor.show_message('Cannot read: %r' % location)
            return None

        exists = io.exists(location)
        self.isdir = io.isdir(location)

        if exists in (True, NotImplemented):
            # File could exist. Read it.
            self.is_new = False
            try:
                text, self.encoding = io.read(location)

                # Replace \r\n by \n.
                text = text.replace('\r\n', '\n')

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

        self.buffer.document = Document(text, cursor_position)
        self._file_content = text
        self._has_unsaved_changes = False
        # Feed the sticky Thai-gate with the reloaded text, else the first
        # render after reload runs unsanitized until the next edit.
        note_document_text(text)

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

    def write(self, location=None, force=False):
        """
        Write file to I/O backend.

        Returns ``True`` when the file was written, ``False`` otherwise
        (a message has been shown). With ``force`` (``:w!``), a read-only
        local file is temporarily made writable for one write and its mode
        is restored afterwards (docs/bugs/117.md).
        """
        # Resolve the target location without mutating state up front.
        target_location = location if location is not None else self.location
        if not target_location:
            self.editor.show_message('Unknown location: %r' % location)
            return False

        # Find I/O backend that handles this location.
        io = next(
            (b for b in self.editor.io_backends if b.can_open_location(target_location)),
            None,
        )
        if io is None:
            self.editor.show_message('Unknown location: %r' % target_location)
            return False

        # Write it.
        try:
            eol = '\n' if self._had_trailing_newline else ''
            payload = self.buffer.text + eol
            try:
                self._write_with_backend(io, target_location, payload)
            except PermissionError:
                if not force:
                    raise
                if not self._write_forced(io, target_location, payload):
                    return False
            self.is_new = False
        except Exception as e:
            # E.g. "No such file or directory."
            self.editor.show_message('%s' % e)
            return False
        else:
            location_changed = target_location != self.location
            # Only mutate location after a successful write.
            self.location = target_location
            # When the save succeeds: update: _file_content.
            self._file_content = self.buffer.text
            self._has_unsaved_changes = False
            if location_changed:
                self.run_reporter()
                note_document_text(self.location)
            return True

    def get_display_name(self, short=False):
        """
        Return name as displayed.
        """
        if self.location is None:
            return UNTITLED_LABEL
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
            report_errors = report(location, document)

            def ready():
                self._reporter_is_running = False

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
