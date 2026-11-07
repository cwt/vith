"""
The welcome message. This is displayed when the editor opens without any files.
"""

import platform
import sys

import prompt_toolkit

import vith

version = sys.version_info
vith_version = vith.__version__

__all__ = (
    'WELCOME_MESSAGE_TOKENS',
    'WELCOME_MESSAGE_WIDTH',
    'WELCOME_MESSAGE_HEIGHT',
)

WELCOME_MESSAGE_WIDTH = 36


WELCOME_MESSAGE_TOKENS = [
    ('class:title', 'Vith - Thai-enabled Python Vi clone\n'),
    ('', "Fork of Jonathan Slenders's PyVim\n"),
    ('', 'By Chaiwat Suttipongsakul\n\n'),
    ('', 'version '),
    ('class:version', vith_version),
    ('', ', prompt_toolkit '),
    ('class:version', prompt_toolkit.__version__),
    ('', '\n'),
    ('', 'type :q'),
    ('class:key', '<Enter>'),
    ('', '            to exit\n'),
    ('', 'type :help'),
    ('class:key', '<Enter>'),
    ('', ' or '),
    ('class:key', '<F1>'),
    ('', ' for help\n\n'),
    ('', 'All feedback is appreciated.\n\n'),
    (
        'class:pythonversion',
        ' %s %i.%i.%i ' % (platform.python_implementation(), version[0], version[1], version[2]),
    ),
]

WELCOME_MESSAGE_HEIGHT = ''.join(t[1] for t in WELCOME_MESSAGE_TOKENS).count('\n') + 1
