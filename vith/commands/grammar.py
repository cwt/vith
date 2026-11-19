from prompt_toolkit.contrib.regular_languages.compiler import compile

from .commands import get_commands_taking_locations
from .ranges import ADDR_GRAMMAR, RANGE_GRAMMAR


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
        (?P<sub_range>%(range)s?) \s* (?P<command>s|substitute) (?P<sub_args>\s*[^a-zA-Z0-9_\\\s"].*)?   |

        # Read command (file or shell output insertion with optional line address).
        (?P<read_addr>%(addr)s)? \s* (?P<command>read|r) (\s* ! (?P<read_shell>.*) | \s+ (?P<location>[^\s]+))?   |

        # Range line-editing commands (:d, :y, :pu, :j with optional count).
        (?P<cmd_range>%(range)s)? \s* (?P<command>delete|yank|join|put|pu|d|y|j)(?P<force>!?) (\s+ (?P<cmd_arg>\d+))?   |

        # Range move/copy commands (:m, :t, :co with destination address).
        (?P<cmd_range>%(range)s)? \s* (?P<command>move|copy|co|m|t) (\s* (?P<dest_addr>%(addr)s) | \s+ (?P<dest_addr>[^\s]+))?   |

        # Range sort and retab commands.
        (?P<cmd_range>%(range)s)? \s* (?P<command>sort|retab)(?P<force>!?) (\s+ (?P<cmd_arg>[^\s]+))?   |

        # Global / vglobal commands (:[range]g[!]/pat/[cmd], :[range]v/pat/[cmd]).
        (?P<cmd_range>%(range)s)? \s* (?P<command>vglobal|global|g|v)(?P<force>!?) (?P<global_args>\s*[^a-zA-Z0-9_\\\s"!].*)   |

        # Range shell filter (requires explicit range; bare :!cmd stays interactive).
        (?P<filter_range>%(range)s) \s* ! (?P<filter_shell>.*)   |

        # Range/append/pipe write command (:[range]w[!] [file | >> file | !cmd]).
        (?P<cmd_range>%(range)s)? \s* (?P<command>write|w)(?P<force>!?) (\s* >> \s* (?P<write_append>[^\s]+) | \s+ ! (?P<write_shell>.*) | \s+ (?P<location>[^\s]+))?   |

        # Commands accepting a location.
        (?P<command>%(commands_taking_locations)s)(?P<force>!?)  \s+   (?P<location>[^\s]+)   |

        # Commands accepting a buffer.
        (?P<command>bwipeout|bdelete|sbuffer|buffer|bd|bw|sb|b)(?P<force>!?)  \s+   (?P<buffer_name>[^\s]+)    |

        # Tab move command with position argument.
        (?P<command>tabmove|tabm) \s+ (?P<tab_arg>[^\s]+)    |

        # Bare line-address jump (:<N>, :$, :., :+N, :-N, :'<, :'>).
        (?P<go_to_line>%(addr)s)                                |

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
        'addr': ADDR_GRAMMAR,
        'range': RANGE_GRAMMAR,
        # Longest-first alternation for commands taking locations: the
        # longest command name is matched directly instead of relying on
        # generic-branch backtracking (deterministic ordering is retained;
        # docs/bugs/009.md and docs/bugs/073.md).
        'commands_taking_locations': '|'.join(
            sorted(get_commands_taking_locations(), key=_longest_first)
        ),
    }
)
