import json
import os
import re
from functools import lru_cache
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

__all__ = ('DocumentLexer', 'sniff_filetype_from_text')

_SHEBANG_VERSION_RE = re.compile(r'[\d.]+$')
_SHEBANG_INTERPRETER_MAP: dict[str, str] = {
    'python': 'python',
    'pypy': 'python',
    'micropython': 'python',
    'bash': 'bash',
    'sh': 'bash',
    'zsh': 'bash',
    'dash': 'bash',
    'ksh': 'bash',
    'ash': 'bash',
    'mksh': 'bash',
    'fish': 'fish',
    'node': 'javascript',
    'nodejs': 'javascript',
    'deno': 'javascript',
    'bun': 'javascript',
    'ts-node': 'typescript',
    'tsx': 'typescript',
    'ruby': 'ruby',
    'jruby': 'ruby',
    'perl': 'perl',
    'lua': 'lua',
    'luajit': 'lua',
    'php': 'php',
    'awk': 'awk',
    'gawk': 'awk',
    'mawk': 'awk',
    'nawk': 'awk',
    'rscript': 'r',
    'pwsh': 'powershell',
    'powershell': 'powershell',
    'tclsh': 'tcl',
    'wish': 'tcl',
    'sed': 'sed',
}
_JSON_OBJECT_START_RE = re.compile(r'^\{\s*"[^"\n]+"\s*:')
_PYTHON_DEF_OR_CLASS_RE = re.compile(
    r'^(?:async\s+)?(?:def\s+[A-Za-z_]\w*\s*\([^)]*\)\s*(?:->\s*[^:\n]+)?'
    r'|class\s+[A-Za-z_]\w*(?:\s*\([^)]*\))?):\s*(?:#.*)?$',
    re.MULTILINE,
)
_PYTHON_IMPORT_RE = re.compile(
    r'^(?:from\s+[A-Za-z_][\w.]*\s+import\s+\S+|import\s+[A-Za-z_][\w.]*(?:\s+as\s+[A-Za-z_]\w*)?)\s*(?:#.*)?$',
    re.MULTILINE,
)
_PYTHON_BODY_HINT_RE = re.compile(
    r'(?:^if\s+__name__\s*==|^\s+return\b|\bprint\()',
    re.MULTILINE,
)


def _filetype_from_shebang(first_line: str) -> str | None:
    """Resolve a canonical Pygments alias from a `#!` shebang line."""
    parts = first_line[2:].strip().split()
    if not parts:
        return None
    cmd = os.path.basename(parts[0]).lower()
    if cmd == 'env':
        cmd = ''
        for tok in parts[1:]:
            if tok.startswith('-'):
                continue
            if '=' in tok and not tok.startswith('='):
                continue
            cmd = os.path.basename(tok).lower()
            break
    if not cmd:
        return None
    mapped = _SHEBANG_INTERPRETER_MAP.get(cmd)
    if mapped is not None:
        return mapped
    stripped = _SHEBANG_VERSION_RE.sub('', cmd)
    if stripped:
        return _SHEBANG_INTERPRETER_MAP.get(stripped)
    return None


def sniff_filetype_from_text(text: str) -> str | None:
    """
    Lightweight, deterministic content sniffer returning a canonical Pygments
    lexer alias (or `None` for plain text) without calling
    `pygments.lexers.guess_lexer()`, which imports all 215+ Pygments lexer
    modules (~67-89 ms freeze; docs/bugs/162.md).
    """
    if not text or not text.strip():
        return None

    sample = text if len(text) <= 4096 else text[:4096]
    first_line = sample.split('\n', 1)[0].strip()
    if first_line.startswith('#!'):
        shebang_ft = _filetype_from_shebang(first_line)
        if shebang_ft is not None:
            return shebang_ft

    stripped = sample.lstrip()
    head = stripped[:64].lower()
    if head.startswith('<?php'):
        return 'php'
    if head.startswith(('<!doctype html', '<html')):
        return 'html'
    if head.startswith('<?xml'):
        return 'xml'
    if sample.startswith('diff --git ') or (
        '\n--- ' in '\n' + sample and '\n+++ ' in sample and '\n@@ ' in sample
    ):
        return 'diff'

    if stripped.startswith(('{', '[')):
        if len(text) <= 65536:
            try:
                parsed = json.loads(text)
            except Exception:
                parsed = None
            if isinstance(parsed, (dict, list)):
                return 'json'
        elif _JSON_OBJECT_START_RE.match(stripped):
            return 'json'

    if _PYTHON_DEF_OR_CLASS_RE.search(sample) or (
        _PYTHON_IMPORT_RE.search(sample) and _PYTHON_BODY_HINT_RE.search(sample)
    ):
        return 'python'

    return None


# Cache from Pygments token type to prompt_toolkit style class name.
# (`pygments_token_to_classname` does a chain of `isinstance` checks, which
# adds up over hundreds of tokens per keystroke.) Bounded to avoid unbounded
# memory growth in long sessions.
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
    MAX_LINE_GENERATORS = 2

    def __init__(self, editor_buffer: Any) -> None:
        self.editor_buffer = editor_buffer
        self._cached_location: str | None = None
        self._cached_filetype: str | None = None
        self._cached_text_version: int = -1
        self._filename_lookup_missed: bool = False
        self._pygments_lexer: Any = None
        self._syntax_sync: Any = None
        self._directory_lexer: Any = None

    def invalidation_hash(self):
        # Invalidate the fragment cache when the buffer location (or file
        # explorer mode) changes, e.g. after `:w newname.py`, when the
        # `:set filetype` override changes, or when a live `:render` preview
        # publishes a new styled frame.
        rendered_ver = getattr(self.editor_buffer, '_rendered_version', 0)
        return (
            self.editor_buffer.location,
            getattr(self.editor_buffer, 'in_file_explorer_mode', False),
            getattr(self.editor_buffer, 'filetype_override', None),
            rendered_ver if isinstance(rendered_ver, int) else 0,
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

    def _sniff_lexer_for_location(self, location: str | None) -> Any | None:
        """
        Guess a Pygments lexer from buffer content for files Pygments cannot
        identify by name (extensionless scripts, docs/bugs/121.md, 162.md).

        Uses `sniff_filetype_from_text` so plain extensionless files never
        import all 215+ Pygments lexer modules via `guess_lexer()`.
        """
        buffer = getattr(self.editor_buffer, 'buffer', None)
        text = getattr(buffer, 'text', '')
        if not isinstance(text, str) or not text.strip():
            return None
        filetype = sniff_filetype_from_text(text)
        if not filetype:
            return None
        try:
            from pygments.lexers import get_lexer_by_name

            return get_lexer_by_name(filetype, stripnl=False, stripall=False, ensurenl=False)
        except Exception:
            return None

    def _lexer_for_override(self, override: str | None) -> Any | None:
        """Return a Pygments lexer instance for a `:set filetype` override."""
        if not override:
            return None
        try:
            from pygments.lexers import get_lexer_by_name

            return get_lexer_by_name(override, stripnl=False, stripall=False, ensurenl=False)
        except Exception:
            return None

    def _ensure_lexer(self, location: str | None) -> bool:
        """Load/cache the Pygments lexer for this location."""
        override = getattr(self.editor_buffer, 'filetype_override', None)
        text_version = getattr(getattr(self.editor_buffer, 'buffer', None), '_text_version', 0)
        location_unchanged = self._cached_location == location and self._cached_filetype == override
        miss_is_fresh = self._pygments_lexer is not None or (
            _STREAMING_AVAILABLE and self._cached_text_version == text_version
        )
        if location_unchanged and miss_is_fresh:
            # Already looked up: reuse the result (including a cached miss,
            # so we don't re-scan the Pygments registry on every edit).
            return self._pygments_lexer is not None

        if not location_unchanged:
            self._filename_lookup_missed = False

        # `:set filetype=...` takes precedence over filename detection.
        if override:
            lexer_inst = self._lexer_for_override(override)
            if lexer_inst is not None:
                self._filename_lookup_missed = False
                if not _STREAMING_AVAILABLE:
                    self._pygments_lexer = PygmentsLexer(
                        lexer_inst.__class__, sync_from_start=False
                    )
                    self._syntax_sync = None
                else:
                    self._cached_location = location
                    self._cached_filetype = override
                    self._cached_text_version = text_version
                    self._pygments_lexer = lexer_inst
                    self._syntax_sync = RegexSync.from_pygments_lexer_cls(lexer_inst.__class__)
                    return True
                self._cached_location = location
                self._cached_filetype = override
                self._cached_text_version = text_version
                return self._pygments_lexer is not None
            # Invalid override: fall through to filename detection instead
            # of crashing (the `:set` handler rejects these, so this only
            # happens for programmatically set values).

        if not _STREAMING_AVAILABLE:
            # Legacy fallback for prompt_toolkit builds without `RegexSync`:
            # cache the `PygmentsLexer` per location (docs/bugs/030.md, 065.md).
            self._pygments_lexer = (
                PygmentsLexer.from_filename(location, sync_from_start=False) if location else None
            )
            if self._pygments_lexer is None or isinstance(self._pygments_lexer, SimpleLexer):
                sniffed = self._sniff_lexer_for_location(location)
                if sniffed is not None:
                    self._pygments_lexer = PygmentsLexer(sniffed.__class__, sync_from_start=False)
            self._cached_location = location
            self._cached_filetype = override
            self._cached_text_version = text_version
            self._syntax_sync = None
            return self._pygments_lexer is not None

        lexer_inst = None
        if location and not self._filename_lookup_missed:
            from pygments.lexers import get_lexer_for_filename
            from pygments.util import ClassNotFound

            try:
                lexer_inst = get_lexer_for_filename(
                    location, stripnl=False, stripall=False, ensurenl=False
                )
            except ClassNotFound:
                self._filename_lookup_missed = True

        if lexer_inst is None:
            lexer_inst = self._sniff_lexer_for_location(location)
            if lexer_inst is None:
                self._cached_location = location
                self._cached_filetype = override
                self._cached_text_version = text_version
                self._pygments_lexer = None
                self._syntax_sync = None
                return False

        self._cached_location = location
        self._cached_filetype = override
        self._cached_text_version = text_version
        self._pygments_lexer = lexer_inst
        self._syntax_sync = RegexSync.from_pygments_lexer_cls(lexer_inst.__class__)
        return True

    def lex_document(self, document: Any) -> Any:
        """
        Call the lexer and return a get_tokens_for_line function.
        """
        rendered_lines = getattr(self.editor_buffer, '_rendered_lines', None)
        if isinstance(rendered_lines, list):
            n_rendered = len(rendered_lines)

            def get_rendered_line(i: int) -> Any:
                return rendered_lines[i] if 0 <= i < n_rendered else []

            return get_rendered_line

        location = self.editor_buffer.location

        if getattr(self.editor_buffer, 'in_file_explorer_mode', False):
            return self._get_directory_lexer().lex_document(document)

        if location or getattr(self.editor_buffer, 'filetype_override', None):
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
            num_lines = len(document.lines)
            max_generators = self.MAX_LINE_GENERATORS

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
                # Evict and close the least-recently-used generator before
                # inserting so old `document.text[start_index:]` slices are
                # freed immediately on backward scrolls (docs/bugs/162.md).
                while len(line_generators) >= max_generators:
                    oldest_gen = next(iter(line_generators))
                    del line_generators[oldest_gen]
                    oldest_gen.close()
                line_generators[generator] = row
                return generator

            def get_line(i):
                if i < 0 or i >= num_lines:
                    return []
                try:
                    return cache[i]
                except KeyError:
                    generator = get_generator(i)
                    for num, line in generator:
                        cache[num] = line
                        if num == i:
                            line_generators.pop(generator, None)
                            line_generators[generator] = i
                            if num + 1 in cache:
                                del cache[num + 1]
                            return cache[num]
                    line_generators.pop(generator, None)
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
