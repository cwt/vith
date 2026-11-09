import gzip
import io
import os
import urllib.request

from .base import EditorIO

__all__ = (
    'FileIO',
    'GZipFileIO',
    'DirectoryIO',
    'HttpIO',
)


# Primary text encoding tried first on read; `FALLBACK_ENCODING` (`latin-1`)
# is a 1:1 byte-to-codepoint fallback that never raises `UnicodeDecodeError`.
# `ENCODINGS` is retained as the canonical `(primary, fallback)` tuple; see
# docs/bugs/070.md and docs/bugs/074.md for write-side UTF-8 promotion.
PRIMARY_ENCODING = 'utf-8'
FALLBACK_ENCODING = 'latin-1'
ENCODINGS = (PRIMARY_ENCODING, FALLBACK_ENCODING)


def _expand_location(location: str) -> str:
    """Expand leading `~` user-home prefixes for local filesystem locations."""
    return os.path.expanduser(location)


class FileIO(EditorIO):
    """
    I/O backend for the native file system.
    """

    def can_open_location(self, location):
        # We can handle all local files.
        expanded = _expand_location(location)
        return '://' not in expanded and not os.path.isdir(expanded)

    def exists(self, location):
        return os.path.exists(_expand_location(location))

    def read(self, location):
        """
        Read file from disk.
        """
        location = _expand_location(location)

        with open(location, 'rb') as f:
            data = f.read()
        return _auto_decode(data)

    def write(self, location, text, encoding='utf-8'):
        """
        Write file to disk.
        """
        location = _expand_location(location)

        with io.open(location, 'w', encoding=encoding) as f:
            f.write(text)


class GZipFileIO(FileIO):
    """
    I/O backend for gzip files.

    It is possible to edit this file as if it were not compressed.
    The read and write call will decompress and compress transparently.
    """

    def can_open_location(self, location):
        return super().can_open_location(location) and _expand_location(location).endswith('.gz')

    def read(self, location):
        location = _expand_location(location)

        with gzip.open(location, 'rb') as f:
            data = f.read()
        return _auto_decode(data)

    def write(self, location, text, encoding='utf-8'):
        """
        Write file to disk.
        """
        location = _expand_location(location)

        with gzip.open(location, 'wb') as f:
            f.write(text.encode(encoding))


class DirectoryIO(EditorIO):
    """
    Create a textual listing of the directory content.
    """

    def can_open_location(self, location):
        # We can handle all local directories.
        expanded = _expand_location(location)
        return '://' not in expanded and os.path.isdir(expanded)

    def exists(self, location):
        return os.path.isdir(_expand_location(location))

    def read(self, directory):
        directory = _expand_location(directory)
        # Read content.
        content = sorted(os.listdir(directory))
        directories = []
        files = []

        for f in content:
            if os.path.isdir(os.path.join(directory, f)):
                directories.append(f)
            else:
                files.append(f)

        # Construct output.
        result = []
        result.append('" ==================================\n')
        result.append('" Directory Listing\n')
        result.append('"    %s\n' % os.path.abspath(directory))
        result.append('"    Quick help: -: go up dir\n')
        result.append('" ==================================\n')
        result.append('../\n')
        result.append('./\n')

        for d in directories:
            result.append('%s/\n' % d)

        for f in files:
            result.append('%s\n' % f)

        return ''.join(result), 'utf-8'

    def write(self, location, text, encoding='utf-8'):
        raise NotImplementedError('Cannot write to directory.')

    def isdir(self, location):
        return True


class HttpIO(EditorIO):
    """
    I/O backend that reads from HTTP.
    """

    def can_open_location(self, location):
        # True when the location is an http:// or https:// URL.
        return location.startswith(('http://', 'https://'))

    def exists(self, location):
        # HTTP existence is not probed (no HEAD request); treat the
        # location as potentially existing so the read path is attempted
        # instead of silently blanking the buffer.
        return True

    def read(self, location):
        # Do Http request.
        with urllib.request.urlopen(location) as response:
            data = response.read()

        # Return decoded.
        return _auto_decode(data)

    def write(self, location, text, encoding='utf-8'):
        raise NotImplementedError('Cannot write to HTTP.')


def _auto_decode(data: bytes) -> tuple[str, str]:
    """
    Decode bytes as UTF-8, falling back to `latin-1` (which maps every byte
    0x00..0xFF without raising). Returns a `(text, encoding)` tuple.
    """
    assert isinstance(data, bytes)

    try:
        return data.decode(PRIMARY_ENCODING), PRIMARY_ENCODING
    except UnicodeDecodeError:
        return data.decode(FALLBACK_ENCODING), FALLBACK_ENCODING
