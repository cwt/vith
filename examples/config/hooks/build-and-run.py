"""
Example Python hook for Vith.
Place in ~/.config/vith/hooks/build-and-run.py and register in config.toml:

    [hooks]
    enabled = ["build-and-run.py"]
"""

from subprocess import call

from prompt_toolkit.application import run_in_terminal
from prompt_toolkit.filters import vi_insert_mode
from prompt_toolkit.key_binding.key_processor import KeyPress
from prompt_toolkit.keys import Keys


def configure(editor):
    """
    Hook configuration function.
    Receives the `vith.editor.Editor` instance to configure custom keybindings.
    """
    # Typing 'jj' in insert mode returns to navigation mode:
    @editor.add_key_binding('j', 'j', filter=vi_insert_mode)
    def exit_insert_mode(event):
        event.app.key_processor.feed(KeyPress(Keys.Escape))

    # F9: Execute the current Python file in terminal:
    @editor.add_key_binding(Keys.F9)
    def save_and_execute_python_file(event):
        eb = editor.current_editor_buffer
        if eb is None:
            return
        if eb.location is None:
            editor.show_message("File has no location. Please save first.")
            return
        if not eb.write():
            return

        def execute():
            call(['python3', eb.location])
            input('\nPress ENTER to continue...')

        run_in_terminal(execute)
