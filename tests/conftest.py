import pytest
from prompt_toolkit.input import DummyInput
from prompt_toolkit.output import DummyOutput

from vith.editor import Editor
from vith.thai import install_prompt_toolkit_thai_patches
from vith.window_arrangement import EditorBuffer, TabPage, Window

# Tests exercise the patched `Document` behaviors explicitly: the patch
# install is no longer an import side effect of `vith.thai` (docs/bugs/
# 075.md), so the suite installs it up-front (idempotent).
install_prompt_toolkit_thai_patches()


@pytest.fixture
def editor(tmp_path):
    return Editor(config_directory=str(tmp_path), output=DummyOutput(), input=DummyInput())


@pytest.fixture
def editor_buffer(editor):
    return EditorBuffer(editor)


@pytest.fixture
def window(editor_buffer):
    return Window(editor_buffer)


@pytest.fixture
def tab_page(window):
    return TabPage(window)
