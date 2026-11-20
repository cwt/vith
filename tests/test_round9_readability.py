"""Regressions for the Round 9 readability and import-organization inventory (docs/bugs/153.md)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import vith.thai as thai


def test_lexer_module_imports_are_grouped_above_dunder_all():
    # 153 #1: `vith/lexer.py` no longer places `from functools import lru_cache`
    # below `__all__`.
    source = Path('vith/lexer.py').read_text(encoding='utf-8')
    tree = ast.parse(source)

    all_line = None
    last_top_level_import = 0
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            last_top_level_import = max(last_top_level_import, node.lineno)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == '__all__':
                    all_line = node.lineno

    assert all_line is not None
    assert last_top_level_import < all_line


def test_completer_classes_declared_before_factory():
    # 153 #2: `BufferNameCompleter` and `ColorSchemeCompleter` sit above
    # `create_command_completer`.
    source = Path('vith/commands/completer.py').read_text(encoding='utf-8')
    tree = ast.parse(source)

    lines = {}
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef)):
            lines[node.name] = node.lineno

    factory_line = lines['create_command_completer']
    for cls_name in (
        'FiletypeCompleter',
        'BufferNameCompleter',
        'ColorSchemeCompleter',
        'CommandCompleter',
    ):
        assert lines[cls_name] < factory_line


def test_thai_public_surface_hides_compat_internals_from_dir_while_preserving_access():
    # 153 #3: `dir(vith.thai)` exposes only `__all__`, dead internal re-exports
    # are removed, and existing compat internal lookups still resolve via
    # PEP 562 `__getattr__`.
    public_names = set(dir(thai))
    assert public_names == set(thai.__all__)
    assert not any(name.startswith('_') for name in public_names)
    assert 'ptk_renderer' not in public_names

    # Active compat shims still resolve for tests/tools.
    assert thai._THAI_GATE is thai.patches._THAI_GATE
    assert thai._THAI_COMBINING_CPS is thai.classify._THAI_COMBINING_CPS
    assert thai._word_spans_for_line is thai.words._word_spans_for_line

    # Removed unused internal re-exports raise AttributeError.
    for removed in ('_map_column_across_lines', '_install_tabs_processor_patch', '_ThaiTextGate'):
        with pytest.raises(AttributeError):
            getattr(thai, removed)
