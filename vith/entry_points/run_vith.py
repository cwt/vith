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

import sys

import docopt

from vith.editor import Editor
from vith.process import set_process_name
from vith.rc_file import find_config_file, run_rc_file

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

    # Apply configuration file.
    if vithrc:
        run_rc_file(editor, vithrc)
    else:
        config_file = find_config_file()
        if config_file:
            run_rc_file(editor, config_file)

    # Load files and run.
    editor.load_initial_files(locations, in_tab_pages=in_tab_pages, hsplit=hsplit, vsplit=vsplit)
    editor.run()


if __name__ == '__main__':
    run()
