"""Shared Pygments filetype-name canonicalization (docs/bugs/228.md)."""

from __future__ import annotations

from functools import lru_cache


@lru_cache(maxsize=1)
def _pygments_alias_map() -> dict[str, str]:
    """Map lexer aliases to canonical aliases without importing lexer modules."""
    mapping: dict[str, str] = {}
    try:
        from pygments.lexers._mapping import LEXERS

        for _cls, (_mod, _name, aliases, _fnames, _mimes) in LEXERS.items():
            if aliases:
                canonical = aliases[0].lower()
                for alias in aliases:
                    mapping.setdefault(alias.lower(), canonical)
    except Exception:
        pass
    return mapping


@lru_cache(maxsize=512)
def canonical_filetype(name: str) -> str:
    """Resolve a filetype name or alias to its primary canonical alias."""
    lowered = name.strip().lower()
    if not lowered:
        return lowered
    canonical = _pygments_alias_map().get(lowered)
    if canonical is not None:
        return canonical
    return lowered


def is_known_filetype(name: str) -> bool:
    """Whether `name` is a known Pygments lexer alias."""
    return name.strip().lower() in _pygments_alias_map()


__all__ = ('canonical_filetype', 'is_known_filetype')
