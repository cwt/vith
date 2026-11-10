"""
Tests for CLI entry points (`vith.__main__`, `vith.entry_points.run_vith`)
and OS process title utilities (`vith.process`).
"""

from __future__ import annotations

import ctypes
import os
import runpy
import sys
from typing import Any
from unittest.mock import MagicMock

import pytest

import vith.entry_points.run_vith as run_vith
from vith.process import set_process_name

# ---------------------------------------------------------------------------
# Tests for vith/__main__.py
# ---------------------------------------------------------------------------


def test_vith_main_executes_run(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[bool] = []

    def fake_run() -> None:
        calls.append(True)

    monkeypatch.delitem(sys.modules, "vith.__main__", raising=False)
    monkeypatch.setattr(run_vith, "run", fake_run)
    runpy.run_module("vith.__main__", run_name="__main__")
    assert calls == [True]


def test_vith_module_executes_run(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[bool] = []

    def fake_run() -> None:
        calls.append(True)

    monkeypatch.delitem(sys.modules, "vith", raising=False)
    monkeypatch.setattr(run_vith, "run", fake_run)
    runpy.run_module("vith", run_name="__main__")
    assert calls == [True]


# ---------------------------------------------------------------------------
# Tests for vith/entry_points/run_vith.py
# ---------------------------------------------------------------------------


def test_run_vith_module_main(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[bool] = []

    def fake_editor_run(self: Any) -> None:
        called.append(True)

    monkeypatch.setattr(sys, "argv", ["vith"])
    monkeypatch.setattr("vith.editor.Editor.run", fake_editor_run)
    monkeypatch.delitem(sys.modules, "vith.entry_points.run_vith", raising=False)
    runpy.run_module("vith.entry_points.run_vith", run_name="__main__")
    assert called == [True]


def test_run_mutual_exclusion_flags(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(sys, "argv", ["vith", "-p", "-o", "file.txt"])
    with pytest.raises(SystemExit) as exc_info:
        run_vith.run()

    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert "Error: -p, -o and -O are mutually exclusive." in captured.err


def test_run_custom_vithrc(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_editor = MagicMock()
    mock_editor_cls = MagicMock(return_value=mock_editor)
    rc_calls: list[tuple[Any, str]] = []

    def fake_rc_file(editor: Any, path: str) -> None:
        rc_calls.append((editor, path))

    monkeypatch.setattr(run_vith, "Editor", mock_editor_cls)
    monkeypatch.setattr(run_vith, "run_rc_file", fake_rc_file)
    monkeypatch.setattr(sys, "argv", ["vith", "-u", "/tmp/custom_rc", "doc.txt"])

    run_vith.run()

    assert rc_calls == [(mock_editor, "/tmp/custom_rc")]
    mock_editor.load_initial_files.assert_called_once_with(
        ["doc.txt"], in_tab_pages=False, hsplit=False, vsplit=False
    )
    mock_editor.run.assert_called_once()


def test_run_default_vithrc(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_editor = MagicMock()
    mock_editor_cls = MagicMock(return_value=mock_editor)
    rc_calls: list[tuple[Any, str]] = []

    def fake_rc_file(editor: Any, path: str) -> None:
        rc_calls.append((editor, path))

    default_vithrc = os.path.expanduser("~/.vithrc")

    def fake_exists(path: str) -> bool:
        if path == default_vithrc:
            return True
        return False

    monkeypatch.setattr(run_vith, "Editor", mock_editor_cls)
    monkeypatch.setattr(run_vith, "run_rc_file", fake_rc_file)
    monkeypatch.setattr(os.path, "exists", fake_exists)
    monkeypatch.setattr(sys, "argv", ["vith"])

    run_vith.run()

    assert rc_calls == [(mock_editor, default_vithrc)]
    mock_editor.load_initial_files.assert_called_once_with(
        [], in_tab_pages=False, hsplit=False, vsplit=False
    )
    mock_editor.run.assert_called_once()


def test_run_legacy_pyvimrc(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_editor = MagicMock()
    mock_editor_cls = MagicMock(return_value=mock_editor)
    rc_calls: list[tuple[Any, str]] = []

    def fake_rc_file(editor: Any, path: str) -> None:
        rc_calls.append((editor, path))

    default_vithrc = os.path.expanduser("~/.vithrc")
    legacy_pyvimrc = os.path.expanduser("~/.pyvimrc")

    def fake_exists(path: str) -> bool:
        if path == default_vithrc:
            return False
        if path == legacy_pyvimrc:
            return True
        return False

    monkeypatch.setattr(run_vith, "Editor", mock_editor_cls)
    monkeypatch.setattr(run_vith, "run_rc_file", fake_rc_file)
    monkeypatch.setattr(os.path, "exists", fake_exists)
    monkeypatch.setattr(sys, "argv", ["vith"])

    run_vith.run()

    assert rc_calls == [(mock_editor, legacy_pyvimrc)]
    mock_editor.run.assert_called_once()


def test_run_no_rc_files(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_editor = MagicMock()
    mock_editor_cls = MagicMock(return_value=mock_editor)
    rc_calls: list[tuple[Any, str]] = []

    def fake_rc_file(editor: Any, path: str) -> None:
        rc_calls.append((editor, path))

    def fake_exists(path: str) -> bool:
        return False

    monkeypatch.setattr(run_vith, "Editor", mock_editor_cls)
    monkeypatch.setattr(run_vith, "run_rc_file", fake_rc_file)
    monkeypatch.setattr(os.path, "exists", fake_exists)
    monkeypatch.setattr(sys, "argv", ["vith"])

    run_vith.run()

    assert rc_calls == []
    mock_editor.run.assert_called_once()


@pytest.mark.parametrize(
    "flag,expected_kwargs",
    [
        ("-p", {"in_tab_pages": True, "hsplit": False, "vsplit": False}),
        ("-o", {"in_tab_pages": False, "hsplit": True, "vsplit": False}),
        ("-O", {"in_tab_pages": False, "hsplit": False, "vsplit": True}),
    ],
)
def test_run_layout_flags(
    flag: str,
    expected_kwargs: dict[str, bool],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_editor = MagicMock()
    mock_editor_cls = MagicMock(return_value=mock_editor)

    def fake_exists(path: str) -> bool:
        return False

    monkeypatch.setattr(run_vith, "Editor", mock_editor_cls)
    monkeypatch.setattr(os.path, "exists", fake_exists)
    monkeypatch.setattr(sys, "argv", ["vith", flag, "a.py", "b.py"])

    run_vith.run()

    mock_editor.load_initial_files.assert_called_once_with(
        ["a.py", "b.py"],
        in_tab_pages=expected_kwargs["in_tab_pages"],
        hsplit=expected_kwargs["hsplit"],
        vsplit=expected_kwargs["vsplit"],
    )
    mock_editor.run.assert_called_once()


# ---------------------------------------------------------------------------
# Tests for vith/process.py
# ---------------------------------------------------------------------------


def test_set_process_name_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[int, bytes, int, int, int]] = []

    def fake_prctl(cmd: int, name: bytes, a: int, b: int, c: int) -> int:
        calls.append((cmd, name, a, b, c))
        return 0

    mock_libc = MagicMock()
    mock_libc.prctl = fake_prctl

    def fake_cdll(path: Any) -> Any:
        return mock_libc

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(ctypes, "CDLL", fake_cdll)

    # 1. Normal name
    set_process_name("vith")
    assert calls[0] == (15, b"vith\x00", 0, 0, 0)

    # 2. Long name (truncated to 15 bytes + null terminator)
    set_process_name("a_very_long_custom_name_for_process")
    assert calls[1] == (15, b"a_very_long_cus\x00", 0, 0, 0)
    assert len(calls[1][1]) == 16


def test_set_process_name_linux_exception_handled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_cdll_error(path: Any) -> Any:
        raise OSError("prctl failed")

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(ctypes, "CDLL", fake_cdll_error)

    # Should not raise
    set_process_name("vith")


def test_set_process_name_darwin(monkeypatch: pytest.MonkeyPatch) -> None:
    buf = ctypes.create_string_buffer(b"original_long_argv0_name")
    p_buf = ctypes.cast(buf, ctypes.c_char_p)
    argv = (ctypes.c_char_p * 2)(p_buf, None)
    argv_ptr = ctypes.cast(argv, ctypes.POINTER(ctypes.c_char_p))

    mock_ns = MagicMock()
    mock_ns.return_value.contents = argv_ptr

    mock_libc = MagicMock()
    mock_libc._NSGetArgv = mock_ns
    mock_libc.memset = ctypes.CDLL(None).memset
    mock_libc.memcpy = ctypes.CDLL(None).memcpy

    def fake_cdll(path: Any) -> Any:
        return mock_libc

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(ctypes, "CDLL", fake_cdll)

    set_process_name("vith")

    # Name replaced in memory
    assert buf.raw.startswith(b"vith\x00")


def test_set_process_name_darwin_empty_argv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    argv = (ctypes.c_char_p * 1)(None)
    argv_ptr = ctypes.cast(argv, ctypes.POINTER(ctypes.c_char_p))

    mock_ns = MagicMock()
    mock_ns.return_value.contents = argv_ptr

    mock_libc = MagicMock()
    mock_libc._NSGetArgv = mock_ns

    def fake_cdll(path: Any) -> Any:
        return mock_libc

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(ctypes, "CDLL", fake_cdll)

    # Should not crash when argv[0] is None
    set_process_name("vith")


def test_set_process_name_darwin_exception_handled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_cdll_error(path: Any) -> Any:
        raise OSError("CDLL failed")

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(ctypes, "CDLL", fake_cdll_error)

    # Should not raise
    set_process_name("vith")


def test_set_process_name_unsupported_platform(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    # Should safely no-op
    set_process_name("vith")
