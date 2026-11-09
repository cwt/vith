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

    def failing_listdir(_path):
        raise PermissionError('Permission denied')

    monkeypatch.setattr(os, 'listdir', failing_listdir)
    editor.message = None
    eb = EditorBuffer(editor, str(target_dir))
    assert eb.in_file_explorer_mode is True
    assert editor.message is not None and 'Permission denied' in editor.message
