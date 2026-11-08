import os

import pytest

from vith.commands.handler import handle_command


@pytest.fixture
def cwd_restored():
    """Return the process to the workspace cwd after the test."""
    original = os.getcwd()
    yield
    os.chdir(original)


def test_cd_no_arg_and_expanduser(editor, tmp_path, monkeypatch, cwd_restored):
    editor.load_initial_files([None])
    # `:cd` with no argument goes home — but only relative to a *fake* home
    # inside tmp (089: the test used to `os.chdir(home)` with the REAL home
    # and the suite never returned to the workspace cwd, breaking
    # subprocess-based tests that run afterwards).
    fake_home = tmp_path / 'fakehome'
    fake_home.mkdir()
    monkeypatch.setenv('HOME', str(fake_home))
    os.chdir(str(tmp_path))

    handle_command(editor, ':cd')
    assert os.getcwd() == str(fake_home)

    # :cd with an absolute path switches there.
    d = tmp_path / 'sub'
    d.mkdir()
    handle_command(editor, ':cd %s' % d)
    assert os.getcwd() == str(d)
