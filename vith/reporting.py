"""
Reporting.

This is a way to highlight syntax errors in an open files.
Reporters are run in an executor (in a thread) to ensure not blocking the
input.

Usage::

    errors = report('location.py', Document('file content'))
"""

import pyflakes.api

__all__ = ('report', 'checks_python_file')


def checks_python_file(location):
    """
    Single source of truth for the `.py` gate.

    `EditorBuffer.run_reporter` probes this to avoid spawning background
    work for non-Python files; `report` uses the same predicate inside the
    worker thread, so the two sides can never diverge (see docs/bugs/010.md
    of the findings bundle).
    """
    return bool(location) and location.endswith('.py')


class ReporterError:
    """
    Error found by a reporter.
    """

    def __init__(self, lineno, start_column, end_column, formatted_text):
        self.lineno = lineno  # Zero based line number.
        self.start_column = start_column
        self.end_column = end_column
        self.formatted_text = formatted_text

    def __eq__(self, other):
        if not isinstance(other, ReporterError):
            return NotImplemented
        return (
            self.lineno,
            self.start_column,
            self.end_column,
            self.formatted_text,
        ) == (
            other.lineno,
            other.start_column,
            other.end_column,
            other.formatted_text,
        )


def _byte_offset_to_char_col(line: str, byte_offset: int | None) -> int:
    """
    Convert a 0-based UTF-8 byte offset (`ast` `col_offset`) into a 0-based
    character column within `line`.

    Uses `errors='ignore'` when decoding the byte prefix so a byte offset
    landing inside a multi-byte sequence or invalid UTF-8 never fabricates
    replacement characters (`U+FFFD`) that skew the character count.
    """
    if not byte_offset or byte_offset <= 0 or not line:
        return 0
    if line.isascii():
        return min(byte_offset, len(line))
    encoded_line = line.encode('utf-8', errors='surrogatepass')
    clamped = min(byte_offset, len(encoded_line))
    char_col = len(encoded_line[:clamped].decode('utf-8', errors='ignore'))
    return min(char_col, len(line))


def report(location, document):
    """
    Run reporter on document and return list of ReporterError instances.
    (Depending on the location it will or won't run anything.)

    Returns a list of `ReporterError`.
    """
    assert isinstance(location, str)

    if checks_python_file(location):
        return report_pyflakes(document)
    else:
        return []


def report_pyflakes(document):
    """
    Run pyflakes on document and return list of ReporterError instances.
    """
    # Run pyflakes on input.
    reporter = _FlakesReporter()
    pyflakes.api.check(document.text, '', reporter=reporter)

    def format_flake_message(message):
        return [
            ('class:flakemessage.prefix', 'pyflakes:'),
            ('', ' '),
            ('class:flakemessage', message.message % message.message_args),
        ]

    def message_to_reporter_error(message):
        """Turn pyflakes message into ReporterError."""
        row = max(0, message.lineno - 1)
        if row < len(document.lines):
            line = document.lines[row]
        else:
            line = ''
        char_col = _byte_offset_to_char_col(line, message.col)
        start_index = document.translate_row_col_to_index(row, char_col)
        line_start = document.translate_row_col_to_index(row, 0)
        line_end = min(line_start + len(line), len(document.text))
        start_index = max(line_start, min(start_index, line_end))
        end_index = start_index
        while end_index < line_end:
            ch = document.text[end_index]
            if ch == '_' or ch.isalnum():
                end_index += 1
            else:
                break

        return ReporterError(
            lineno=row,
            start_column=char_col,
            end_column=char_col + (end_index - start_index),
            formatted_text=format_flake_message(message),
        )

    # Construct list of ReporterError instances.
    return [message_to_reporter_error(m) for m in reporter.messages]


class _FlakesMessage:
    """Minimal pyflakes-message-like object for syntax/unexpected errors."""

    def __init__(self, location, msg, lineno, col):
        self.location = location
        self.lineno = lineno if lineno and lineno > 0 else 1
        self.col = col if col and col >= 0 else 0
        self.message = '%s'
        self.message_args = (msg,)


class _FlakesReporter:
    """
    Reporter class to be passed to pyflakes.api.check.
    """

    def __init__(self):
        self.messages = []

    def unexpectedError(self, location, msg):
        self.messages.append(_FlakesMessage(location, msg, 0, 0))

    def syntaxError(self, location, msg, lineno, offset, text):
        # Python's SyntaxError.offset is 1-based; convert to 0-based col_offset.
        col = (offset - 1) if offset and offset > 0 else 0
        self.messages.append(_FlakesMessage(location, msg, lineno, col))

    def flake(self, message):
        self.messages.append(message)
