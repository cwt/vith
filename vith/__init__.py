"""
vith: A pure-Python Vi clone with Thai & Lao script support.
"""

from __future__ import annotations

import re
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

# In a development checkout (e.g. repo root), read directly from pyproject.toml
# so editing the version requires touching only pyproject.toml. The fallback
# is a single constant (docs/bugs/075.md); pyproject.toml remains primary.
_FALLBACK_VERSION = "0.11.2"
_pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
if _pyproject.is_file():
    _m = re.search(
        r'(?m)^version\s*=\s*["\']([^"\']+)["\']', _pyproject.read_text(encoding="utf-8")
    )
    __version__ = _m.group(1) if _m else _FALLBACK_VERSION
else:
    try:
        __version__ = version("vith")
    except PackageNotFoundError:
        __version__ = _FALLBACK_VERSION

__all__ = ("__version__",)


def __getattr__(name: str) -> object:
    if name == "thai_words":
        from vith.thai import words

        return words
    if name == "thai_classify":
        from vith.thai import classify

        return classify
    if name == "thai_clusters":
        from vith.thai import clusters

        return clusters
    if name == "thai_patches":
        from vith.thai import patches

        return patches
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
