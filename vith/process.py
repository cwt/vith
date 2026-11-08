"""
Operating system process title utilities for vith.
"""

from __future__ import annotations

import sys

__all__ = ('set_process_name',)


def set_process_name(name: str = 'vith') -> None:
    """
    Set the operating system process title/name so that ps, top, htop,
    and multiplexers (tmux, szn) querying the foreground process see `name`
    instead of "Python". Uses standard-library ctypes with zero dependencies.
    """
    # 1. macOS (Darwin): rewrite argv[0] in memory via _NSGetArgv
    if sys.platform == 'darwin':
        try:
            import ctypes

            libc = ctypes.CDLL(None)
            if hasattr(libc, '_NSGetArgv'):
                # char ***_NSGetArgv(void)
                libc._NSGetArgv.restype = ctypes.POINTER(ctypes.POINTER(ctypes.c_char_p))
                argv_str = libc._NSGetArgv().contents
                if argv_str and argv_str[0]:
                    old_len = len(argv_str[0])
                    libc._NSGetArgv.restype = ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))
                    libc.memset.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_size_t]
                    libc.memcpy.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t]
                    raw_argv0 = libc._NSGetArgv().contents[0]
                    new_bytes = name.encode('utf-8') + b'\0'
                    libc.memset(raw_argv0, 0, old_len)
                    libc.memcpy(raw_argv0, new_bytes, min(len(new_bytes), old_len))
        except Exception:
            pass

    # 2. Linux: set comm via prctl(PR_SET_NAME)
    elif sys.platform.startswith('linux'):
        try:
            import ctypes

            libc = ctypes.CDLL(None)
            if hasattr(libc, 'prctl'):
                # PR_SET_NAME = 15 (max 16 bytes including null terminator)
                name_bytes = name[:15].encode('utf-8') + b'\0'
                libc.prctl.argtypes = [
                    ctypes.c_int,
                    ctypes.c_char_p,
                    ctypes.c_ulong,
                    ctypes.c_ulong,
                    ctypes.c_ulong,
                ]
                libc.prctl(15, name_bytes, 0, 0, 0)
        except Exception:
            pass
