from prompt_toolkit.contrib.regular_languages.compiler import compile

from .commands import get_commands_taking_locations


def _longest_first(name: str) -> tuple[int, str]:
    # Sort key: longest alternatives first, so `wq`/`write` win over `w`
    # without relying on parser backtracking (docs/bugs/073.md). The
    # secondary alphabetical term keeps ordering deterministic
    # (docs/bugs/009.md).
    return (-len(name), name)


#: The compiled grammar for the Vim command line.
# Substitute ranges support %, numeric, ., $, '<, '>, and optional +/- offsets;
# any non-alphanumeric delimiter other than backslash or double quote is allowed.
COMMAND_GRAMMAR = compile(
    r"""
    # Allow leading colons and whitespace. (They are ignored.)
    :*
    \s*
    (
        # Substitute command
        (?P<sub_range>(%%|(\d+|\.|\$|'<|'>)([+-]\d+)?(,(\d+|\.|\$|'<|'>)([+-]\d+)?)?)?) \s* (?P<command>s|substitute) (?P<sub_args>\s*[^a-zA-Z0-9_\\\s"].*)?   |

        # Commands accepting a location.
        (?P<command>%(commands_taking_locations)s)(?P<force>!?)  \s+   (?P<location>[^\s]+)   |

        # Commands accepting a buffer.
        (?P<command>b|buffer)(?P<force>!?)  \s+   (?P<buffer_name>[^\s]+)    |

        # Jump to line numbers.
        (?P<go_to_line>\d+)                                     |

        # Set operation
        (?P<command>set|setlocal|setl) \s+ (?P<set_option>[^\s=]+)
                                       (=(?P<set_value>[^\s]*))?           |

        # Colorscheme command
        (?P<command>colorscheme|colo) \s+ (?P<colorscheme>[^\s]+)    |

        # Shell command
        !(?P<shell_command>.*)                                  |

        # Any other normal command.
        (?P<command>[^\s!]+)(?P<force>!?)                         |

        # Accept the empty input as well. (Ignores everything.)
    )

    # Allow trailing space.
    \s*
"""
    % {
        # Longest-first alternation for commands taking locations: the
        # longest command name is matched directly instead of relying on
        # generic-branch backtracking (deterministic ordering is retained;
        # docs/bugs/009.md and docs/bugs/073.md).
        'commands_taking_locations': '|'.join(
            sorted(get_commands_taking_locations(), key=_longest_first)
        ),
    }
)
