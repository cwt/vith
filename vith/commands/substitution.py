"""
`:substitute` parsing, regex safety guards, Vim replacement dialect, and
shared row-substitution engine reused by `:global` / `:vglobal`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .ranges import parse_range as parse_substitute_range
from .ranges import replace_buffer_lines, resolve_range

# Heuristic guard against catastrophic backtracking (docs/bugs/123.md): a
# group that contains a quantifier and is itself quantified, e.g. `(a+)+`,
# `([a-z]+)*$` or `(\w+\s?)+`. Escaped characters and `[...]` classes are
# stripped first, so `[a+]+` and `\(a+\)+` stay allowed.
_UNSAFE_NESTED_QUANTIFIER = re.compile(r'\([^()]*[+*?][^()]*\)[+*]')
_UNSAFE_QUANTIFIED_GROUP = re.compile(r'\(([^()]*)\)([+*])')
_UNSAFE_COUNTED_REPEAT = re.compile(r'\{\d+(?:,\d*)?\}')
_MAX_SUBSTITUTE_PATTERN_LENGTH = 2000


def _strip_regex_literals(pattern_text: str) -> str:
    """
    Remove escaped characters and `[...]` classes from `pattern_text`, so
    the nested-quantifier guard only sees regex structure.
    """
    return re.sub(r'\[[^\]]*\]', '', re.sub(r'\\.', '', pattern_text))


def _has_overlapping_alternatives(body: str) -> bool:
    """Whether a group body has alternatives where one prefixes another."""
    alternatives = [part for part in body.split('|') if part]
    for index, first in enumerate(alternatives):
        for second in alternatives[index + 1 :]:
            if first.startswith(second) or second.startswith(first):
                return True
    return False


def is_unsafe_regex(editor: Any, pattern_text: str) -> bool:
    """
    Conservative catastrophic-backtracking guard shared by `:s` and `:g`
    (docs/bugs/123.md, 142.md). Overlapping alternations (`(a|aa)+$`) and
    counted repeats inside a quantified group (`(a{1,3})+$`) are rejected in
    addition to the original nested-quantifier rule. Disjoint alternations
    such as `(a|b)+$` remain allowed.
    """
    if len(pattern_text) > _MAX_SUBSTITUTE_PATTERN_LENGTH:
        editor.show_message('Unsafe pattern (nested quantifier): too long')
        return True
    stripped = _strip_regex_literals(pattern_text)
    if _UNSAFE_NESTED_QUANTIFIER.search(stripped):
        editor.show_message('Unsafe pattern (nested quantifier): %s' % pattern_text)
        return True
    for match in _UNSAFE_QUANTIFIED_GROUP.finditer(stripped):
        body = match.group(1)
        if _has_overlapping_alternatives(body) or _UNSAFE_COUNTED_REPEAT.search(body):
            editor.show_message('Unsafe pattern (nested quantifier): %s' % pattern_text)
            return True
    return False


def should_ignore_case(editor: Any, pattern: str | None) -> bool:
    """Resolve effective case-folding honoring `ignorecase` and `smartcase`."""
    checker = getattr(editor, 'should_ignore_case', None)
    if callable(checker):
        return bool(checker(pattern))
    if not bool(getattr(editor, 'ignore_case', False)):
        return False
    if (
        bool(getattr(editor, 'smartcase', False))
        and pattern
        and any(ch.isupper() for ch in pattern)
    ):
        return False
    return True


def split_delimited_fields(text: str, delim: str, *, max_splits: int = 2) -> list[str]:
    """
    Split `text` into up to `max_splits + 1` segments on unescaped occurrences
    of `delim`, unescaping `\\<delim>` in the split prefix segments while
    preserving all other regex escapes and keeping the trailing remainder raw.
    """
    fields: list[str] = []
    current: list[str] = []
    i = 0
    length = len(text)

    while i < length and len(fields) < max_splits:
        ch = text[i]
        if ch == '\\' and i + 1 < length:
            next_ch = text[i + 1]
            if next_ch == delim:
                current.append(delim)
            else:
                current.append(ch)
                current.append(next_ch)
            i += 2
            continue
        if ch == delim:
            fields.append(''.join(current))
            current = []
            i += 1
            continue
        current.append(ch)
        i += 1

    if len(fields) == max_splits:
        fields.append(text[i:])
    else:
        fields.append(''.join(current))
    return fields


def parse_substitute_args(sub_args: str | None) -> tuple[str | None, str | None, str]:
    """
    Parse the argument tail of `:s` (`/<search>/<replace>/<flags>` with any
    non-alphanumeric delimiter) into `(search, replace, flags)`.
    """
    if not sub_args or not sub_args.lstrip():
        return None, None, ''

    stripped = sub_args.lstrip()
    delim = stripped[0]
    fields = split_delimited_fields(stripped[1:], delim, max_splits=2)

    search = fields[0]
    replace = fields[1] if len(fields) >= 2 else None
    flags = fields[2].replace(delim, '').rstrip() if len(fields) == 3 else ''
    return search, replace, flags


@dataclass(frozen=True)
class _SubstituteOptions:
    """Parsed execution options for `:substitute`."""

    sub_count: int
    ignore_case: bool
    report_only: bool
    print_last: bool


@dataclass(frozen=True)
class _PreparedSubstitute:
    """Validated and compiled `:substitute` operation ready to run on rows."""

    search: str
    replace: str | None
    options: _SubstituteOptions
    pattern: re.Pattern[str]
    repl: Any


def _vim_replacement_function(replace: str, previous: str, group_count: int) -> Any:
    """
    Translate a Vim `:s` replacement into a `re.sub` callable (docs/bugs/139.md).

    Vim's replacement dialect differs from Python's: `&` is the whole match,
    `\\&` is a literal ampersand, `\\r` inserts a line break, `\\n` inserts a
    NUL, `~` inserts the previous replacement, `\\0`..`\\9` are backreferences,
    and `\\u`/`\\U`/`\\l`/`\\L`/`\\e`/`\\E` are case operators. Raises
    `ValueError` for invalid backreferences or unsupported escapes.
    """
    case_ops = frozenset('ulULeE')
    tokens: list[tuple[str, Any]] = []
    i = 0
    while i < len(replace):
        ch = replace[i]
        if ch == '\\' and i + 1 < len(replace):
            nxt = replace[i + 1]
            if nxt in case_ops:
                tokens.append(('case', nxt))
            elif nxt == '&':
                tokens.append(('text', '&'))
            elif nxt == 'r':
                tokens.append(('text', '\n'))
            elif nxt == 'n':
                tokens.append(('text', '\x00'))
            elif nxt == '\\':
                tokens.append(('text', '\\'))
            elif nxt.isdigit():
                group = int(nxt)
                if group > group_count:
                    raise ValueError('invalid backreference \\%d' % group)
                tokens.append(('group', group))
            elif nxt.isalpha():
                raise ValueError('invalid escape \\%s in replacement' % nxt)
            else:
                tokens.append(('text', nxt))
            i += 2
            continue
        if ch == '&':
            tokens.append(('group', 0))
        elif ch == '~':
            tokens.append(('text', previous))
        else:
            tokens.append(('text', ch))
        i += 1

    def expand(match: Any) -> str:
        mode: str | None = None
        one_shot: str | None = None
        pieces: list[str] = []

        def emit(text: str) -> None:
            nonlocal one_shot
            for char in text:
                if one_shot == 'u':
                    pieces.append(char.upper())
                    one_shot = None
                elif one_shot == 'l':
                    pieces.append(char.lower())
                    one_shot = None
                elif mode == 'U':
                    pieces.append(char.upper())
                elif mode == 'L':
                    pieces.append(char.lower())
                else:
                    pieces.append(char)

        for kind, value in tokens:
            if kind == 'text':
                emit(value)
            elif kind == 'group':
                emit(match.group(value) or '')
            elif kind == 'case':
                if value in ('u', 'l'):
                    one_shot = value
                elif value in ('U', 'L'):
                    mode = value
                else:
                    mode = None
                    one_shot = None
        return ''.join(pieces)

    return expand


def _parse_substitute_flags(
    flags: str, default_ignore_case: bool
) -> tuple[_SubstituteOptions | None, str | None]:
    """
    Parse and validate `:substitute` flags (`g`, `i`, `I`, `n`, `p`), returning
    either `(_SubstituteOptions, None)` or `(None, error_message)`.
    """
    clean_flags = flags.replace('/', '')
    ignore_case = default_ignore_case
    sub_count = 1
    report_only = False
    print_last = False

    for ch in clean_flags:
        match ch:
            case 'g':
                sub_count = 0
            case 'i':
                ignore_case = True
            case 'I':
                ignore_case = False
            case 'n':
                report_only = True
            case 'p':
                print_last = True
            case _:
                return None, f'E488: Trailing characters: {ch}'

    return (
        _SubstituteOptions(
            sub_count=sub_count,
            ignore_case=ignore_case,
            report_only=report_only,
            print_last=print_last,
        ),
        None,
    )


def _resolve_substitute_range(
    range_start: str | None,
    range_end: str | None,
    cursor_row: int,
    line_count: int,
    visual_marks: tuple[int, int] | None,
) -> tuple[list[int] | None, str | None]:
    """
    Resolve substitute range bounds to an ordered list of 0-based row indices,
    or return an error message when a mark is unset or the range is invalid.
    """
    bounds, err = resolve_range(
        range_start,
        range_end,
        cursor_row,
        line_count,
        visual_marks,
        default_range='.',
    )
    if err is not None or bounds is None:
        return None, err
    start_row, end_row = bounds
    return list(range(start_row, end_row + 1)), None


def _record_substitute_state(
    editor: Any, search_state: Any, search: str, replace: str | None
) -> None:
    """
    Publish the last substitute/regex state shared by `:s` and `:g/.../s`
    (docs/bugs/135.md): a bare `:s` must reuse the *substitute* pattern, not
    the enclosing global pattern, and `:noh` suppression must be cleared.
    """
    editor.last_substitute_text = replace
    search_state.text = search
    editor.clear_hlsearch_suppression()


def prepare_substitute(
    editor: Any,
    search: str | None,
    replace: str | None,
    flags: str,
) -> _PreparedSubstitute | None:
    """
    Resolve history fallbacks, validate safety/flags, and compile the regex
    and replacement callable for `:s` and `:g/.../s`.
    """
    search_state = editor.application.current_search_state
    if not search:
        search = search_state.text
    if not search:
        editor.show_message('No previous regular expression')
        return None

    if replace is None:
        replace = editor.last_substitute_text

    # Reject patterns prone to catastrophic backtracking before compiling:
    # nested quantifiers freeze the whole editor on innocent input
    # (docs/bugs/123.md). Escaped and class-wrapped quantifiers are exempt.
    if is_unsafe_regex(editor, search):
        return None

    # Parse and validate flags (docs/bugs/083.md, docs/bugs/094.md).
    options, flag_error = _parse_substitute_flags(
        flags,
        default_ignore_case=should_ignore_case(editor, search),
    )
    if flag_error is not None or options is None:
        editor.show_message(flag_error)
        return None

    regex_flags = re.IGNORECASE if options.ignore_case else 0
    try:
        pattern = re.compile(search, flags=regex_flags)
    except re.error as exc:
        editor.show_message('Invalid regex: %s' % exc)
        return None

    try:
        repl = _vim_replacement_function(
            replace or '', editor.last_substitute_text or '', pattern.groups
        )
    except ValueError as exc:
        editor.show_message('Invalid regex: %s' % exc)
        return None

    return _PreparedSubstitute(
        search=search,
        replace=replace,
        options=options,
        pattern=pattern,
        repl=repl,
    )


def apply_substitute_to_rows(
    editor: Any,
    eb: Any,
    lines: list[str],
    target_rows: list[int],
    prepared: _PreparedSubstitute,
) -> None:
    """Apply a compiled substitution across `target_rows` of `lines` in `eb`."""
    new_lines = list(lines)
    last_substituted_row: int | None = None
    total_matches = 0
    matched_lines = 0

    for row in target_rows:
        try:
            result, count = prepared.pattern.subn(
                prepared.repl, new_lines[row], count=prepared.options.sub_count
            )
        except re.error as exc:
            editor.show_message('Invalid regex: %s' % exc)
            return
        if count > 0:
            new_lines[row] = result
            total_matches += count
            matched_lines += 1
            last_substituted_row = row

    if total_matches == 0:
        editor.show_message('Pattern not found')
        return

    search_state = editor.application.current_search_state
    if prepared.options.report_only:
        match_word = 'match' if total_matches == 1 else 'matches'
        line_word = 'line' if matched_lines == 1 else 'lines'
        editor.show_message(f'{total_matches} {match_word} on {matched_lines} {line_word}')
        _record_substitute_state(editor, search_state, prepared.search, prepared.replace)
        return

    assert last_substituted_row is not None
    if not replace_buffer_lines(editor, eb, new_lines, last_substituted_row, first_non_blank=True):
        return

    _record_substitute_state(editor, search_state, prepared.search, prepared.replace)
    if prepared.options.print_last:
        editor.show_message(eb.buffer.document.lines[last_substituted_row])


def substitute(
    editor: Any,
    range_start: str | None,
    range_end: str | None,
    search: str | None,
    replace: str | None,
    flags: str,
) -> None:
    """Substitute /search/ with /replace/ over a range of text."""
    eb = editor.window_arrangement.active_editor_buffer
    if eb is None:
        return

    prepared = prepare_substitute(editor, search, replace, flags)
    if prepared is None:
        return

    buffer = eb.buffer
    buffer_lines = buffer.document.lines
    line_numbers, range_error = _resolve_substitute_range(
        range_start,
        range_end,
        buffer.document.cursor_position_row,
        len(buffer_lines),
        getattr(eb, 'visual_marks', None),
    )
    if range_error is not None or line_numbers is None:
        editor.show_message(range_error)
        return

    apply_substitute_to_rows(editor, eb, list(buffer_lines), line_numbers, prepared)


__all__ = (
    'apply_substitute_to_rows',
    'is_unsafe_regex',
    'parse_substitute_args',
    'parse_substitute_range',
    'prepare_substitute',
    'should_ignore_case',
    'split_delimited_fields',
    'substitute',
)
