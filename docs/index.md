---
type: index
title: "vith Documentation Bundle"
description: "OKF v0.2 knowledge bundle root for the vith project."
status: stable
sources:
  - docs/
verified: machine-confirmed
generated:
  by: "OpenCode (Crouton Alpha)"
  at: "2026-11-08T19:20:00Z"
timestamp: "2026-11-08T19:20:00Z"
---

# vith Documentation Bundle

This directory contains the [Open Knowledge Format (OKF v0.2)](https://github.com/GoogleCloudPlatform/open-knowledge-format/blob/main/SPEC.md) documentation bundle for `vith`.

## Architecture & Design Plans

* [Vith Re-brand & Modernization Plan](vith_rebrand_plan.md) — **Executed**: new identity `vith` (วิธ), BSD 3-clause kept with stacked copyright, `setup.py` → `pyproject.toml` (PEP 621), `README.rst` → `README.md`, user-data migration with graceful fallback, docs sweep and gate re-verification. All gates re-verified post-rebrand (120 tests, mypy 46 files @3.10, ruff clean).
* [Thai & Lao Script Support Plan](thai_support_plan.md) — **Implemented**: root-cause analysis (`prompt_toolkit` vs `vith`), cell-cluster vs syllable granularity ([`../vith/thai.py`](../vith/thai.py), [`../vith/key_bindings.py`](../vith/key_bindings.py)), `SARA AM` (`U+0E33`/`U+0EB3`) differential renderer neighbor refresh (ported from `szn` `71ba808`), version-gated `prompt_toolkit` integration, and unit test suite ([`../tests/test_thai.py`](../tests/test_thai.py)).
* [Speed, Latency & Python 3.15 Stability Plan](speed_latency_plan.md) — **Implemented (rev 161–162)**: reproducible Python 3.15.2 benchmarks ([`../tools/bench_plans.py`](../tools/bench_plans.py)), Python 3.14+ `asyncio.get_event_loop()` startup crash fix, streaming/cached `PygmentsLexer`, lazy colorscheme loading, singleton word completion caching (incl. jedi 0.20 dual-API support), O(1) error line index, `io.open` & dirty-flag cleanups, mypy baseline ([`../mypy.ini`](../mypy.ini)); deviations & follow-ups logged in §1.4.

## Audit & Findings

* [Findings Bundle](bugs/index.md) — **91 machine-verified findings across 3 audit rounds and 2 addenda (all closed)**:
  * **Round 4 addendum — stem-scan audit (2026-11-08, hg rev 271 — fixed)**: 12 machine-verified findings ([080](bugs/080.md)–[091](bugs/091.md)) — full-repo re-scan against all prior report classes (selection/cursor coordinate stacks, renderer/gate families, command/buffer invariants, perf/precedence drifts) at 4 parallel auditors + confirmatory E2E harness; HIGH: `:b <pattern>` crash with untitled buffers.
  * **Round 3 (2026-11-08, hg rev 87de3a3 — fixed in rev 253–265)**: 13 machine-verified findings ([063](bugs/063.md)–[075](bugs/075.md)) — 9 defects (`type: bug`: cell-cluster motion overrides, `TextObject` rollback + fragile Thai patch internals, legacy lexer fallback, unguarded None-invariants, quit/`:b` semantics, `__main__` import side-effect, reporter invalidation, startup/IO lows, grouped Vim/cold-text deviations) and 4 improvements (`type: improvement`: `O(n)` keystroke work + per-frame allocations, duplicated word-motion walks, dead code/example, readability). Originally filed as 18 findings (063–080) at hg rev 251, re-validated against the tip, merged/renumbered on 2026-11-08, then fixed finding-by-finding (179 tests, mypy/ruff clean — mapping + fix revs in [`bugs/index.md`](bugs/index.md)).
  * **Round 2 (2026-11-07, hg rev 191–233 — closed in rev 194–233)**: 25 machine-verified findings ([038](bugs/038.md)–[062](bugs/062.md)) — 18 defects (`type: bug`, including high-priority crashes and Thai/Lao Visual mode, inclusive motion, word-completion, and right-margin combining-mark bugs) and 7 improvements (`type: improvement`: performance, dead code, duplication, readability/test hygiene, and plan/benchmark drift).
  * **Round 1 (2026-11-06, hg rev 164 — closed in rev 167–195)**: 37 machine-verified findings ([001](bugs/001.md)–[037](bugs/037.md)) — defects 001–016 and improvements 017–037, all fixed with recorded hg revisions.

## Meta

* [Bundle Update Log](log.md) — Chronological log of documentation bundle additions and updates.

## Name-mapping note

These documents were written under the `pyvim` name. Since the 2026-11 rebrand, all live code paths are `vith/…`; the `CHANGELOG` appendix preserves the `pyvim` history verbatim, and the `LICENSE` carries the stacked copyright (Slenders 2015 + Suttipongsakul 2026).
