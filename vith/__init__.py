"""
vith: A pure-Python Vi clone with Thai & Lao script support.
"""

from __future__ import annotations

import re
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

# In a development checkout (e.g. repo root), read directly from pyproject.toml
# so editing the version requires touching only pyproject.toml:
_pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
if _pyproject.is_file():
    _m = re.search(
        r'(?m)^version\s*=\s*["\']([^"\']+)["\']', _pyproject.read_text(encoding="utf-8")
    )
    __version__ = _m.group(1) if _m else "0.9.1"
else:
    try:
        __version__ = version("vith")
    except PackageNotFoundError:
        __version__ = "0.9.1"

__all__ = ("__version__",)
