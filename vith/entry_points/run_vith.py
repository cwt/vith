#!/usr/bin/env python
"""
vith: Pure Python Vim clone.
Usage:
    vith [-p] [-o] [-O] [-u <vithrc>] [<location>...]

Options:
    -p           : Open files in tab pages.
    -o           : Split horizontally.
    -O           : Split vertically.
    -u <vithrc> : Use this .vithrc file instead.
"""

import os

import docopt

from vith.editor import Editor
from vith.rc_file import run_rc_file

__all__ = ('run',)


def run():
    a = docopt.docopt(__doc__)
    locations = a['<location>']
    in_tab_pages = a['-p']
    hsplit = a['-o']
    vsplit = a['-O']
    vithrc = a['-u']

    # Create new editor instance.
    editor = Editor()

    # Apply rc file.
    if vithrc:
        run_rc_file(editor, vithrc)
    else:
        default_vithrc = os.path.expanduser('~/.vithrc')
        legacy_pyvimrc = os.path.expanduser('~/.pyvimrc')

        if os.path.exists(default_vithrc):
            run_rc_file(editor, default_vithrc)
        elif os.path.exists(legacy_pyvimrc):
            run_rc_file(editor, legacy_pyvimrc)

    # Load files and run.
    editor.load_initial_files(locations, in_tab_pages=in_tab_pages, hsplit=hsplit, vsplit=vsplit)
    editor.run()


if __name__ == '__main__':
    run()
