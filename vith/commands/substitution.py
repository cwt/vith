"""
`:substitute` parsing, regex safety guards, Vim replacement dialect, and
shared row-substitution engine reused by `:global` / `:vglobal`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .ranges import parse_range as parse_substitute_range
from .ranges import resolve_range, splice_buffer_lines

# Heuristic guard against catastrophic backtracking (docs/bugs/123.md, 142.md,
# 208.md): a group that contains a quantifier and is itself quantified, e.g.
# `(a+)+`, `((a)+)+`, `(a+){5,}$`, `([a-z]+)*$` or `(\w+\s?)+`. Escaped
# characters and `[...]` classes are stripped first, so `[a+]+` and
# `\(a+\)+` stay allowed.
_UNSAFE_COUNTED_REPEAT = re.compile(r'\{\d+(?:,\d*)?\}')
_MAX_SUBSTITUTE_PATTERN_LENGTH = 2000


def _strip_regex_literals(pattern_text: str) -> str:
    """
    Remove escaped characters and `[...]` classes from `pattern_text`, so
    the nested-quantifier guard only sees regex structure.

    A `]` immediately after `[` (or `[^`) is a literal class member in Python
    regex syntax; consume it so classes like `[]+(a+)+]` are stripped whole
    instead of exposing a spurious nested quantifier (docs/bugs/208.md).
    """
    result: list[str] = []
    index = 0
    while index < len(pattern_text):
        char = pattern_text[index]
        if char == '\\':
            index += 2
            continue
        if char == '[':
            index += 1
            if index < len(pattern_text) and pattern_text[index] == '^':
                index += 1
            if index < len(pattern_text) and pattern_text[index] == ']':
                index += 1
            while index < len(pattern_text):
                if pattern_text[index] == '\\':
                    index += 2
                    continue
                if pattern_text[index] == ']':
                    index += 1
                    break
                index += 1
            continue
        result.append(char)
        index += 1
    return ''.join(result)


def _has_overlapping_alternatives(body: str) -> bool:
    """Whether a group body has alternatives where one prefixes another (O(A log A))."""
    alternatives = sorted(part for part in body.split('|') if part)
    for index in range(len(alternatives) - 1):
        if alternatives[index + 1].startswith(alternatives[index]):
            return True
    return False


def _counted_repeat_at(pattern_text: str, index: int) -> tuple[str, int] | None:
    """Return the counted-repeat token at `index` and the next index."""
    match = _UNSAFE_COUNTED_REPEAT.match(pattern_text, index)
    if match is None:
        return None
    return match.group(0), match.end()


def _is_variable_quantifier(token: str) -> bool:
    """Whether a quantifier can repeat an unbounded number of times."""
    if token in ('+', '*'):
        return True
    if token.startswith('{'):
        bounds = token[1:-1].split(',', 1)
        if len(bounds) == 1:
            return False  # a fixed repeat is linear
        return bounds[1] == '' or bounds[1] != '0'
    return False


def _has_nested_quantifier(pattern_text: str) -> bool:
    """Parenthesis-stack scan for a quantified group containing a quantifier."""
    stack: list[dict[str, Any]] = []
    index = 0
    while index < len(pattern_text):
        char = pattern_text[index]
        if char == '(':
            stack.append({'body': [], 'quantified': False, 'counted': False})
            # `(?:`, `(?i:`, `(?P<name>` etc. — skip the `?` marker so it is
            # not mistaken for an optional quantifier of the group body.
            index += 2 if index + 1 < len(pattern_text) and pattern_text[index + 1] == '?' else 1
            continue
        if char == ')':
            frame = stack.pop() if stack else None
            index += 1
            token: str | None = None
            if index < len(pattern_text) and pattern_text[index] in '+*?':
                token = pattern_text[index]
                index += 1
            else:
                counted = _counted_repeat_at(pattern_text, index)
                if counted is not None:
                    token, index = counted
            if frame is not None:
                body = ''.join(frame['body'])
                if token is not None and (
                    _is_variable_quantifier(token)
                    and (
                        frame['quantified']
                        or frame['counted']
                        or _has_overlapping_alternatives(body)
                    )
                ):
                    return True
                if stack and (token is not None or frame['quantified'] or frame['counted']):
                    # A nested quantified group stays dangerous when an
                    # ancestor is quantified (`(?:(a+))+$`, docs/bugs/208.md).
                    stack[-1]['quantified'] = True
            continue
        if char in '+*?':
            if stack:
                stack[-1]['quantified'] = True
            index += 1
            continue
        counted = _counted_repeat_at(pattern_text, index)
        if counted is not None:
            token, index = counted
            if stack and _is_variable_quantifier(token):
                stack[-1]['quantified'] = True
                stack[-1]['counted'] = True
            continue
        if stack:
            stack[-1]['body'].append(char)
        index += 1
    return False


def is_unsafe_regex(editor: Any, pattern_text: str) -> bool:
    """
    Conservative catastrophic-backtracking guard shared by `:s` and `:g`
    (docs/bugs/123.md, 142.md, 208.md). Uses a parenthesis-stack scan so
    nested groups and outer counted repeats cannot bypass it, while disjoint
    alternations such as `(a|b)+$` remain allowed.
    """
    if len(pattern_text) > _MAX_SUBSTITUTE_PATTERN_LENGTH:
        editor.show_message('Unsafe pattern (nested quantifier): too long')
        return True
    stripped = _strip_regex_literals(pattern_text)
    if _has_nested_quantifier(stripped):
        editor.show_message('Unsafe pattern (nested quantifier): %s' % pattern_text)
        return True
    return False


def resolve_ignore_case(ignore_case: bool, smartcase: bool, pattern: str | None) -> bool:
    """
    Determine whether search/substitute should fold case for `pattern`,
    honoring `ignorecase` and `smartcase` (regex escapes/classes such as
    `\\S` or `[A-Z]` are stripped so they do not disable smartcase;
    docs/bugs/147.md, 152.md).
    """
    if not ignore_case:
        return False
    if smartcase and pattern:
        literals = _strip_regex_literals(pattern)
        if any(ch.isupper() for ch in literals):
            return False
    return True


def should_ignore_case(editor: Any, pattern: str | None) -> bool:
    """Resolve effective case-folding honoring `ignorecase` and `smartcase`."""
    checker = getattr(editor, 'should_ignore_case', None)
    if callable(checker):
        return bool(checker(pattern))
    return resolve_ignore_case(
        bool(getattr(editor, 'ignore_case', False)),
        bool(getattr(editor, 'smartcase', False)),
        pattern,
    )


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


_VIM_CASE_OPS = frozenset('ulULeE')


def _vim_replacement_function(replace: str, previous: str, group_count: int) -> Any:
    """
    Translate a Vim `:s` replacement into a C-level `re.sub` template string
    (when no case operators are present) or a coalesced `re.sub` callable
    (docs/bugs/139.md, 160.md).

    Vim's replacement dialect differs from Python's: `&` is the whole match,
    `\\&` is a literal ampersand, `\\r` inserts a line break, `\\n` inserts a
    NUL, `~` inserts the previous replacement, `\\0`..`\\9` are backreferences,
    and `\\u`/`\\U`/`\\l`/`\\L`/`\\e`/`\\E` are case operators. Raises
    `ValueError` for invalid backreferences or unsupported escapes.
    """
    tokens: list[tuple[str, Any]] = []
    literal_buf: list[str] = []
    has_case_op = False
    i = 0
    length = len(replace)

    def flush_literal() -> None:
        if literal_buf:
            tokens.append(('text', ''.join(literal_buf)))
            literal_buf.clear()

    while i < length:
        ch = replace[i]
        if ch == '\\' and i + 1 < length:
            nxt = replace[i + 1]
            if nxt in _VIM_CASE_OPS:
                flush_literal()
                tokens.append(('case', nxt))
                has_case_op = True
            elif nxt == '&':
                literal_buf.append('&')
            elif nxt == 'r':
                literal_buf.append('\n')
            elif nxt == 'n':
                literal_buf.append('\x00')
            elif nxt == '\\':
                literal_buf.append('\\')
            elif nxt.isdigit():
                group = int(nxt)
                if group > group_count:
                    raise ValueError('invalid backreference \\%d' % group)
                flush_literal()
                tokens.append(('group', group))
            elif nxt.isalpha():
                raise ValueError('invalid escape \\%s in replacement' % nxt)
            else:
                literal_buf.append(nxt)
            i += 2
            continue
        if ch == '&':
            flush_literal()
            tokens.append(('group', 0))
        elif ch == '~':
            literal_buf.append(previous)
        else:
            literal_buf.append(ch)
        i += 1
    flush_literal()

    if not has_case_op:
        # Compile to a Python `re.sub` template string so substitution runs
        # entirely in C without a per-match Python callback (docs/bugs/160.md).
        template_parts: list[str] = []
        for kind, value in tokens:
            if kind == 'text':
                template_parts.append(value.replace('\\', '\\\\'))
            else:
                template_parts.append(f'\\g<{value}>')
        return ''.join(template_parts)

    compiled_tokens = tuple(tokens)

    def expand(match: Any) -> str:
        mode: str | None = None
        one_shot: str | None = None
        pieces: list[str] = []

        for kind, value in compiled_tokens:
            if kind == 'case':
                if value in ('u', 'l'):
                    one_shot = value
                elif value in ('U', 'L'):
                    mode = value
                else:
                    mode = None
                    one_shot = None
                continue

            chunk: str = value if kind == 'text' else (match.group(value) or '')
            if not chunk:
                continue

            if one_shot is not None:
                first = chunk[0].upper() if one_shot == 'u' else chunk[0].lower()
                one_shot = None
                pieces.append(first)
                chunk = chunk[1:]
                if not chunk:
                    continue

            if mode == 'U':
                pieces.append(chunk.upper())
            elif mode == 'L':
                pieces.append(chunk.lower())
            else:
                pieces.append(chunk)
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
    editor.last_substitute_pattern = search
    # Keep publishing to the shared search register for `/`-style history and
    # `:g`, but never *read* it for a bare `:s` (docs/bugs/175.md).
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
    if search is None:
        # Bare `:s` repeats the last *substitution*, not the latest `/` search
        # (docs/bugs/175.md). An empty pattern from `:s//.../` still refers to
        # the shared search register, like Vim.
        search = getattr(editor, 'last_substitute_pattern', '') or search_state.text
    elif not search:
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
    changed_rows: dict[int, str] = {}
    first_changed_row: int | None = None
    last_changed_row: int | None = None
    last_substituted_row: int | None = None
    total_matches = 0
    matched_lines = 0

    for row in target_rows:
        try:
            result, count = prepared.pattern.subn(
                prepared.repl, lines[row], count=prepared.options.sub_count
            )
        except re.error as exc:
            editor.show_message('Invalid regex: %s' % exc)
            return
        if count > 0:
            changed_rows[row] = result
            total_matches += count
            matched_lines += 1
            last_substituted_row = row
            if first_changed_row is None or row < first_changed_row:
                first_changed_row = row
            if last_changed_row is None or row > last_changed_row:
                last_changed_row = row

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

    assert first_changed_row is not None
    assert last_changed_row is not None
    assert last_substituted_row is not None
    span_lines = [
        changed_rows.get(row, lines[row]) for row in range(first_changed_row, last_changed_row + 1)
    ]
    if not splice_buffer_lines(
        editor,
        eb,
        first_changed_row,
        last_changed_row,
        span_lines,
        last_substituted_row,
        first_non_blank=True,
    ):
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

    apply_substitute_to_rows(editor, eb, buffer_lines, line_numbers, prepared)


__all__ = (
    'apply_substitute_to_rows',
    'is_unsafe_regex',
    'parse_substitute_args',
    'parse_substitute_range',
    'prepare_substitute',
    'resolve_ignore_case',
    'should_ignore_case',
    'split_delimited_fields',
    'substitute',
)
