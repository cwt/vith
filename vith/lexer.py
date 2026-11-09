from typing import Any

from prompt_toolkit.formatted_text.utils import split_lines
from prompt_toolkit.lexers import Lexer, PygmentsLexer, SimpleLexer
from pygments.lexer import RegexLexer
from pygments.token import Token

try:
    # Streaming sync support (prompt_toolkit >= 3.0.35, used by P1).
    from prompt_toolkit.lexers.pygments import RegexSync
    from prompt_toolkit.styles.pygments import pygments_token_to_classname

    _STREAMING_AVAILABLE = True
except ImportError:
    _STREAMING_AVAILABLE = False

__all__ = ('DocumentLexer',)

# Cache from Pygments token type to prompt_toolkit style class name.
# (`pygments_token_to_classname` does a chain of `isinstance` checks, which
# adds up over hundreds of tokens per keystroke.) Bounded to avoid unbounded
# memory growth in long sessions.
from functools import lru_cache


@lru_cache(maxsize=4096)
def _style_classname(token):
    return 'class:' + pygments_token_to_classname(token)


class DocumentLexer(Lexer):
    """
    Lexer that depending on the filetype, uses another pygments lexer.

    The Pygments lexer instance (and its `RegexSync`) is cached per location,
    so it is only looked up in the Pygments registry when the buffer location
    changes. The document itself is lexed lazily/streaming from the sync
    position near the cursor, instead of eagerly tokenizing everything up to
    EOF on every edit.
    """

    MIN_LINES_BACKWARDS = 50
    REUSE_GENERATOR_MAX_DISTANCE = 100

    def __init__(self, editor_buffer: Any) -> None:
        self.editor_buffer = editor_buffer
        self._cached_location: str | None = None
        self._pygments_lexer: Any = None
        self._syntax_sync: Any = None
        self._directory_lexer: Any = None

    def invalidation_hash(self):
        # Invalidate the fragment cache when the buffer location (or file
        # explorer mode) changes, e.g. after `:w newname.py`.
        return (
            self.editor_buffer.location,
            getattr(self.editor_buffer, 'in_file_explorer_mode', False),
        )

    def _get_directory_lexer(self):
        """
        Cached `PygmentsLexer` for directory listing buffers.

        Creating a `PygmentsLexer` scans the Pygments registry, so doing
        that per keystroke in file-explorer mode paid ~0.6 ms for every
        keypress (docs/bugs/030.md).
        """
        if self._directory_lexer is None:
            self._directory_lexer = PygmentsLexer(DirectoryListingLexer, sync_from_start=False)
        return self._directory_lexer

    def _sniff_lexer_for_location(self, location: str) -> Any | None:
        """
        Guess a Pygments lexer from buffer content for files Pygments cannot
        identify by name (extensionless scripts, docs/bugs/121.md).

        Returns a lexer instance, or `None` when nothing can be guessed.
        The result is cached per location by `_ensure_lexer`, so the
        registry scan happens at most once per file.
        """
        buffer = getattr(self.editor_buffer, 'buffer', None)
        text = getattr(buffer, 'text', '')
        if not isinstance(text, str) or not text.strip():
            return None
        first_line = text.split('\n', 1)[0]
        if first_line.startswith('#!') and 'python' in first_line:
            from pygments.lexers.python import PythonLexer

            return PythonLexer(stripnl=False, stripall=False, ensurenl=False)
        try:
            from pygments.lexers import guess_lexer

            sample = text if len(text) <= 4096 else text[:4096]
            return guess_lexer(sample)
        except Exception:
            return None

    def _ensure_lexer(self, location: str) -> bool:
        """Load/cache the Pygments lexer for this location."""
        if self._cached_location == location and (
            _STREAMING_AVAILABLE or self._pygments_lexer is not None
        ):
            # Already looked up: reuse the result (including a cached miss,
            # so we don't re-scan the Pygments registry on every edit).
            return self._pygments_lexer is not None

        if not _STREAMING_AVAILABLE:
            # Legacy fallback for prompt_toolkit builds without `RegexSync`:
            # cache the `PygmentsLexer` per location (docs/bugs/030.md, 065.md).
            self._pygments_lexer = PygmentsLexer.from_filename(location, sync_from_start=False)
            if self._pygments_lexer is None or isinstance(self._pygments_lexer, SimpleLexer):
                sniffed = self._sniff_lexer_for_location(location)
                if sniffed is not None:
                    self._pygments_lexer = PygmentsLexer(sniffed.__class__, sync_from_start=False)
            self._cached_location = location
            self._syntax_sync = None
            return self._pygments_lexer is not None

        from pygments.lexers import get_lexer_for_filename
        from pygments.util import ClassNotFound

        try:
            lexer_inst = get_lexer_for_filename(
                location, stripnl=False, stripall=False, ensurenl=False
            )
        except ClassNotFound:
            lexer_inst = self._sniff_lexer_for_location(location)
            if lexer_inst is None:
                self._cached_location = location
                self._pygments_lexer = None
                self._syntax_sync = None
                return False

        self._cached_location = location
        self._pygments_lexer = lexer_inst
        self._syntax_sync = RegexSync.from_pygments_lexer_cls(lexer_inst.__class__)
        return True

    def lex_document(self, document: Any) -> Any:
        """
        Call the lexer and return a get_tokens_for_line function.
        """
        location = self.editor_buffer.location

        if location:
            if getattr(self.editor_buffer, 'in_file_explorer_mode', False):
                return self._get_directory_lexer().lex_document(document)

            if not self._ensure_lexer(location):
                # No Pygments lexer found for this location (or Pygments
                # could not guess one): fall back to no highlighting.
                return SimpleLexer().lex_document(document)

            if not _STREAMING_AVAILABLE:
                return self._pygments_lexer.lex_document(document)

            pygments_lexer = self._pygments_lexer
            syntax_sync = self._syntax_sync
            if pygments_lexer is None or syntax_sync is None:
                return SimpleLexer().lex_document(document)

            cache: dict[int, list[Any]] = {}
            line_generators: dict[Any, int] = {}

            def create_line_generator(start_lineno, column=0):
                def get_text_fragments():
                    start_index = document.translate_row_col_to_index(start_lineno, column)
                    text = document.text[start_index:]
                    for _, t, v in pygments_lexer.get_tokens_unprocessed(text):
                        yield _style_classname(t), v

                # NOTE: `split_lines` is iterated lazily here (no `list()`):
                # `get_tokens_unprocessed` is a single continuous generator
                # starting from `start_lineno`, so multiline
                # strings/docstrings are never split across sync chunks, and
                # text past the visible area is only lexed when requested.
                yield from enumerate(split_lines(get_text_fragments()), start_lineno)

            def find_closest_generator(i):
                for generator, lineno in line_generators.items():
                    if lineno < i and i - lineno < self.REUSE_GENERATOR_MAX_DISTANCE:
                        return generator
                return None

            def get_generator(i):
                # Reuse a generator that already covers `i` when possible;
                # only create a fresh one (from the sync position) otherwise:
                # the reuse keeps each render frame to a handful of lexed
                # tokens instead of re-lexing per visible line.
                generator = find_closest_generator(i)
                if generator:
                    return generator
                start = max(0, i - self.MIN_LINES_BACKWARDS)
                if start == 0:
                    row, column = 0, 0
                else:
                    row, column = syntax_sync.get_sync_start_position(document, start)
                generator = create_line_generator(row, column)
                if column:
                    next(generator)
                    row += 1
                line_generators[generator] = row
                return generator

            def get_line(i):
                try:
                    return cache[i]
                except KeyError:
                    generator = get_generator(i)
                    for num, line in generator:
                        cache[num] = line
                        if num == i:
                            line_generators[generator] = i
                            if num + 1 in cache:
                                del cache[num + 1]
                            return cache[num]
                    return []

            return get_line

        return SimpleLexer().lex_document(document)


_DirectoryListing = Token.DirectoryListing


class DirectoryListingLexer(RegexLexer):
    """
    Highlighting of directory listings.
    """

    name = 'directory-listing'
    tokens = {
        str('root'): [  # Conversion to `str` because of Pygments on Python 2.
            (r'^".*', _DirectoryListing.Header),
            (r'^\.\./$', _DirectoryListing.ParentDirectory),
            (r'^\./$', _DirectoryListing.CurrentDirectory),
            (r'^[^"].*/$', _DirectoryListing.Directory),
            (r'^[^"].*\.(txt|rst|md)$', _DirectoryListing.Textfile),
            (r'^[^"].*\.(py)$', _DirectoryListing.PythonFile),
            (r'^[^"].*\.(pyc|pyd)$', _DirectoryListing.Tempfile),
            (r'^\..*$', _DirectoryListing.Dotfile),
        ]
    }
