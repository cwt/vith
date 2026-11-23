"""
Tests for `vith/io/backends.py` (P5: `codecs.open` deprecation removal,
`FileIO`/`GZipFileIO` behaviour).
"""

import io
import warnings

from vith.io.backends import FileIO, GZipFileIO


def test_fileio_write_read_utf8_roundtrip(tmp_path):
    f = FileIO()
    p = str(tmp_path / 'file.txt')

    f.write(p, 'héllo wörld', 'utf-8')
    text, encoding = f.read(p)

    assert text == 'héllo wörld'
    assert encoding == 'utf-8'


def test_fileio_latin1_fallback(tmp_path):
    p = tmp_path / 'latin1.txt'
    # A byte that is not valid UTF-8: falls back to latin-1 (which accepts
    # any byte sequence).
    p.write_bytes(b'caf\xe9')

    text, encoding = FileIO().read(str(p))

    assert text == 'café'
    assert encoding == 'latin-1'


def test_fileio_no_deprecation_warning(tmp_path):
    # P5: `codecs.open` is deprecated in Python 3.14+; the C-buffered
    # `io.open` must be used instead, so no DeprecationWarning may be
    # emitted while reading or writing.
    p = str(tmp_path / 'deprecation.txt')
    f = FileIO()

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always')
        f.write(p, 'plain text', 'utf-8')
        f.read(p)

    deprecations = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert deprecations == [], (
        'FileIO read/write must not emit DeprecationWarnings: %r' % deprecations
    )


def test_fileio_uses_io_open(tmp_path, monkeypatch):
    # Explicitly: reads/writes go through `io.open`, not `codecs.open`.
    p = str(tmp_path / 'tracked.txt')
    f = FileIO()

    calls = []
    original = io.open

    def tracking_open(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr('vith.io.backends.io.open', tracking_open)

    f.write(p, 'data', 'utf-8')
    assert f.read(p) == ('data', 'utf-8')

    assert [args[1] for args, _kw in calls] == ['w']
    assert [kw.get('encoding') for _args, kw in calls] == ['utf-8']


def test_gzip_roundtrip(tmp_path):
    p = str(tmp_path / 'data.txt.gz')
    g = GZipFileIO()

    g.write(p, 'compressed content', 'utf-8')
    text, encoding = g.read(p)

    assert text == 'compressed content'
    assert encoding == 'utf-8'


def test_gzip_can_open_location():
    g = GZipFileIO()
    assert g.can_open_location('/tmp/some-file.txt.gz') is True
    assert g.can_open_location('/tmp/some-file.txt') is False


def test_directory_io_handles_tilde(editor):
    editor.load_initial_files(['~'])
    eb = editor.window_arrangement.active_editor_buffer
    assert eb.in_file_explorer_mode is True


def test_gzip_can_open_location_expands_tilde(monkeypatch, tmp_path):
    # 113 item 6: `GZipFileIO.can_open_location` delegates to `super()` and
    # `_expand_location` consistently for `~/` paths.
    monkeypatch.setenv('HOME', str(tmp_path))
    gz_dir = tmp_path / 'dir.gz'
    gz_dir.mkdir()

    g = GZipFileIO()
    assert g.can_open_location('~/archive.txt.gz') is True
    assert g.can_open_location('~/dir.gz') is False


def test_auto_decode_primary_and_fallback_encodings():
    # 115 item 6: `_auto_decode` tries `PRIMARY_ENCODING` ('utf-8') then
    # falls back to `FALLBACK_ENCODING` ('latin-1').
    from vith.io.backends import ENCODINGS, FALLBACK_ENCODING, PRIMARY_ENCODING, _auto_decode

    assert ENCODINGS == (PRIMARY_ENCODING, FALLBACK_ENCODING) == ('utf-8', 'latin-1')
    assert _auto_decode('สวัสดี'.encode('utf-8')) == ('สวัสดี', 'utf-8')
    assert _auto_decode(b'\xff\xfe') == ('\xff\xfe', 'latin-1')


def test_directory_io_unreadable_raises_oserror(editor, tmp_path, monkeypatch):
    # 110 item 8: `DirectoryIO.read` propagates `OSError` to `EditorBuffer._read`,
    # which surfaces an error message in the editor instead of crashing.
    import os

    from vith.editor_buffer import EditorBuffer

    target_dir = tmp_path / 'restricted'
    target_dir.mkdir()

    def failing_scandir(_path):
        raise PermissionError('Permission denied')

    monkeypatch.setattr(os, 'scandir', failing_scandir)
    editor.message = None
    eb = EditorBuffer(editor, str(target_dir))
    assert eb.in_file_explorer_mode is True
    assert editor.message is not None and 'Permission denied' in editor.message


def test_expand_env_vars_and_location_preserve_urls(monkeypatch, tmp_path):
    import os

    from vith.io.backends import expand_env_vars, expand_location

    monkeypatch.setenv('VITH_TEST_ROOT', str(tmp_path))

    # `$VAR` and `${VAR}` expand on every platform.
    expected = os.path.join(str(tmp_path), 'alacritty.toml')
    assert expand_env_vars(os.path.join('$VITH_TEST_ROOT', 'alacritty.toml')) == expected
    assert expand_env_vars(os.path.join('${VITH_TEST_ROOT}', 'alacritty.toml')) == expected
    assert expand_location(os.path.join('$VITH_TEST_ROOT', 'alacritty.toml')) == expected

    # URLs must never undergo env-var or tilde expansion.
    assert expand_env_vars('https://example.com/$VITH_TEST_ROOT') == (
        'https://example.com/$VITH_TEST_ROOT'
    )
    assert expand_location('http://example.com/%VITH_TEST_ROOT%/a') == (
        'http://example.com/%VITH_TEST_ROOT%/a'
    )

    # On Windows, `%VAR%` (case-insensitive) expands too.
    if os.name == 'nt':
        assert expand_env_vars(r'%VITH_TEST_ROOT%\alacritty.toml') == expected
        assert expand_env_vars(r'%vith_test_root%\alacritty.toml') == expected
        assert expand_location(r'%VITH_TEST_ROOT%\alacritty.toml') == expected


def test_load_initial_files_and_ex_commands_expand_env_vars(editor, tmp_path, monkeypatch):
    import os

    from prompt_toolkit.completion import CompleteEvent
    from prompt_toolkit.document import Document

    from vith.commands.completer import create_command_completer
    from vith.commands.handler import expand_command_filenames, handle_command
    from vith.rc_file import run_rc_file

    sub = tmp_path / 'alacritty'
    sub.mkdir()
    cfg = sub / 'alacritty.toml'
    cfg.write_bytes(b'size = 14.0\n')

    rc = tmp_path / 'vith.toml'
    rc.write_bytes(b'tabstop = 4\n')

    monkeypatch.setenv('VITH_TEST_APPDATA', str(tmp_path))

    path_expr = (
        r'%VITH_TEST_APPDATA%\alacritty\alacritty.toml'
        if os.name == 'nt'
        else '$VITH_TEST_APPDATA/alacritty/alacritty.toml'
    )
    rc_expr = (
        r'%VITH_TEST_APPDATA%\vith.toml' if os.name == 'nt' else '$VITH_TEST_APPDATA/vith.toml'
    )

    # 1. CLI `load_initial_files` opens the real file and stores the expanded path.
    editor.load_initial_files([path_expr])
    eb = editor.window_arrangement.active_editor_buffer
    assert eb is not None
    assert eb.is_new is False
    assert eb.location == str(cfg)
    assert eb.buffer.text == 'size = 14.0'

    # Opening the expanded path again reuses the existing buffer (no duplicate).
    editor.window_arrangement.open_buffer(str(cfg), show_in_current_window=True)
    assert len(editor.window_arrangement.editor_buffers) == 1

    # 2. Ex `:e` expands env vars without triggering `%` E499 or corrupting `%VAR%`.
    editor.window_arrangement.open_buffer(None, show_in_current_window=True)
    handle_command(editor, f'e {path_expr}')
    active = editor.window_arrangement.active_editor_buffer
    assert active is not None
    assert active.location == str(cfg)
    assert active.buffer.text == 'size = 14.0'

    # 3. `:w` overwrite guard and `:cd` honor env-var paths.
    out_expr = r'%VITH_TEST_APPDATA%\out.txt' if os.name == 'nt' else '$VITH_TEST_APPDATA/out.txt'
    handle_command(editor, f'w {out_expr}')
    assert (tmp_path / 'out.txt').read_text(encoding='utf-8') == 'size = 14.0\n'
    editor.message = None
    handle_command(editor, f'w {out_expr}')
    assert editor.message == 'File exists (add ! to override)'

    orig_cwd = os.getcwd()
    try:
        dir_expr = (
            r'%VITH_TEST_APPDATA%\alacritty' if os.name == 'nt' else '$VITH_TEST_APPDATA/alacritty'
        )
        handle_command(editor, f'cd {dir_expr}')
        assert os.path.realpath(os.getcwd()) == os.path.realpath(str(sub))
    finally:
        os.chdir(orig_cwd)

    # 4. `run_rc_file` (`vith -u` / `:so`) expands env vars.
    assert run_rc_file(editor, rc_expr) is True
    assert editor.tabstop == 4

    # 5. Path completion completes files inside env-var directories.
    completer = create_command_completer(editor)
    prefix = (
        r'e %VITH_TEST_APPDATA%\alacritty\al'
        if os.name == 'nt'
        else 'e $VITH_TEST_APPDATA/alacritty/al'
    )
    comps = list(completer.get_completions(Document(prefix, len(prefix)), CompleteEvent()))
    assert any(c.display_text == 'alacritty.toml' for c in comps)

    # 6. On Windows, shell commands preserve `%VAR%` while still expanding trailing `%`.
    if os.name == 'nt':
        cmd_str, err = expand_command_filenames(
            r'echo %VITH_TEST_APPDATA% %', editor, shell_escape=True
        )
        assert err is None
        assert cmd_str is not None
        assert cmd_str.startswith('echo %VITH_TEST_APPDATA% ')
        assert 'alacritty.toml' in cmd_str
