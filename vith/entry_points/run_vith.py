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
import sys

import docopt

from vith.editor import Editor
from vith.process import set_process_name
from vith.rc_file import run_rc_file

__all__ = ('run',)


def run():
    set_process_name('vith')

    a = docopt.docopt(__doc__)
    locations = a['<location>']
    in_tab_pages = a['-p']
    hsplit = a['-o']
    vsplit = a['-O']
    vithrc = a['-u']

    # Fail with a friendly message instead of the bare AssertionError in
    # `load_initial_files` (docs/bugs/070.md).
    if sum(1 for flag in (in_tab_pages, hsplit, vsplit) if flag) > 1:
        print("Error: -p, -o and -O are mutually exclusive.", file=sys.stderr)
        sys.exit(2)

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
