from abc import ABCMeta, abstractmethod

__all__ = ('EditorIO', 'find_backend')


def find_backend(backends, location):
    """First I/O backend in `backends` that can open `location` (docs/bugs/148.md)."""
    return next((backend for backend in backends if backend.can_open_location(location)), None)


class EditorIO(metaclass=ABCMeta):
    """
    The I/O interface for editor buffers.

    It is for instance possible to pass an FTP I/O backend to EditorBuffer, to
    read/write immediately from an FTP server. Or a GZIP backend for files
    ending with .gz.
    """

    @abstractmethod
    def can_open_location(self, location):
        """
        Return True when this I/O implementation is able to handle this `location`.
        """
        return False

    @abstractmethod
    def exists(self, location):
        """
        Return whether this location exists in this storage..
        (If not, this is a new file.)
        """
        return True

    @abstractmethod
    def read(self, location):
        """
        Read file for storage. Returns (text, encoding tuple.)
        Can raise IOError.
        """

    @abstractmethod
    def write(self, location, data, encoding='utf-8'):
        """
        Write file to storage.
        Can raise IOError.
        """

    def append(self, location, data, encoding='utf-8'):
        """
        Append `data` to `location` (docs/bugs/166.md).

        Default implementation reads existing content (if any) and rewrites
        `old_text + data`. Backends that support native O(payload) append
        (such as `FileIO`) override this method.
        """
        old_text = ''
        if self.exists(location):
            old_text, _encoding = self.read(location)
        self.write(location, old_text + data, encoding=encoding)

    def isdir(self, location):
        """
        Return whether this location is a directory.
        """
        return False
