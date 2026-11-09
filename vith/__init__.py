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
_FALLBACK_VERSION = "0.12.0"
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


# Backward-compat shims for pre-reorg flat imports (`vith.thai_words`,
# `vith.thai_classify`, ... moved to the `vith.thai` package in rev 278 with a
# 100% compat promise). Kept intentionally per docs/bugs/112.md item 4:
# no in-repo consumers, but external configs may still use them. Lazy via
# `__getattr__` so there is no import cost unless accessed.
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
