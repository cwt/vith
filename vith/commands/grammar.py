from prompt_toolkit.contrib.regular_languages.compiler import compile

from .commands import get_commands_taking_locations


def _longest_first(name: str) -> tuple[int, str]:
    # Sort key: longest alternatives first, so `wq`/`write` win over `w`
    # without relying on parser backtracking (docs/bugs/073.md). The
    # secondary alphabetical term keeps ordering deterministic
    # (docs/bugs/009.md).
    return (-len(name), name)


#: The compiled grammar for the Vim command line.
COMMAND_GRAMMAR = compile(
    r"""
    # Allow leading colons and whitespace. (They are ignored.)
    :*
    \s*
    (
        # Substitute command
        ((?P<range_start>\d+)(,(?P<range_end>\d+))?)?  (?P<command>s|substitute) \s* / (?P<search>[^/]*) ( / (?P<replace>[^/]*) (?P<flags> ( / \w* )* )? )?   |

        # Commands accepting a location.
        (?P<command>%(commands_taking_locations)s)(?P<force>!?)  \s+   (?P<location>[^\s]+)   |

        # Commands accepting a buffer.
        (?P<command>b|buffer)(?P<force>!?)  \s+   (?P<buffer_name>[^\s]+)    |

        # Jump to line numbers.
        (?P<go_to_line>\d+)                                     |

        # Set operation
        (?P<command>set) \s+ (?P<set_option>[^\s=]+)
                             (=(?P<set_value>[^\s]+))?           |

        # Colorscheme command
        (?P<command>colorscheme|colo) \s+ (?P<colorscheme>[^\s]+)    |

        # Shell command
        !(?P<shell_command>.*)                                  |

        # Any other normal command.
        (?P<command>[^\s!]+)(?P<force>!?)                         |

        # Accept the empty input as well. (Ignores everything.)

        #(?P<command>colorscheme.+)    (?P<colorscheme>[^\s]+)  |
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
