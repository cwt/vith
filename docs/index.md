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
timestamp: "2026-11-09T16:26:05Z"
---

# vith Documentation Bundle

This directory contains the [Open Knowledge Format (OKF v0.2)](https://github.com/GoogleCloudPlatform/open-knowledge-format/blob/main/SPEC.md) documentation bundle for `vith`.

## Architecture & Design Plans

* [Vith Re-brand & Modernization Plan](vith_rebrand_plan.md) — **Executed**: new identity `vith` (วิธ), BSD 3-clause kept with stacked copyright, `setup.py` → `pyproject.toml` (PEP 621), `README.rst` → `README.md`, user-data migration with graceful fallback, docs sweep and gate re-verification. All gates re-verified (current suite: 200+ tests, mypy 56 files @3.10, ruff clean).
* [Thai & Lao Script Support Plan](thai_support_plan.md) — **Implemented**: root-cause analysis (`prompt_toolkit` vs `vith`), cell-cluster vs syllable granularity ([`../vith/thai.py`](../vith/thai.py), [`../vith/key_bindings.py`](../vith/key_bindings.py)), `SARA AM` (`U+0E33`/`U+0EB3`) differential renderer neighbor refresh (ported from `szn` `71ba808`), version-gated `prompt_toolkit` integration, and unit test suite ([`../tests/test_thai.py`](../tests/test_thai.py)).
* [Speed, Latency & Python 3.15 Stability Plan](speed_latency_plan.md) — **Implemented (rev 161–162)**: reproducible Python 3.15.2 benchmarks ([`../tools/bench_plans.py`](../tools/bench_plans.py)), Python 3.14+ `asyncio.get_event_loop()` startup crash fix, streaming/cached `PygmentsLexer`, lazy colorscheme loading, singleton word completion caching (incl. jedi 0.20 dual-API support), O(1) error line index, `io.open` & dirty-flag cleanups, mypy baseline ([`../mypy.ini`](../mypy.ini)); deviations & follow-ups logged in §1.4.

## Audit & Findings

* [Findings Bundle](bugs/index.md) — **123 machine-verified findings across 7 audit rounds and 2 addenda (all 123 closed)**:
  * **Round 7 (2026-11-09, upstream triage — [116](bugs/116.md)–[123](bugs/123.md), `fix_status: fixed`)**: 8 worth-fixing upstream `prompt-toolkit/pyvim` issues fixed one-by-one with regression tests (274 passed, `mypy .` 57 files clean, ruff clean): `ZZ`/`ZQ` quit ([116](bugs/116.md)), `:w!` force-write ([117](bugs/117.md)), `>>`/`<<` vs `tabstop` ([118](bugs/118.md)), `Ctrl-[` exit + counted `i`/`a` ([119](bugs/119.md)/[120](bugs/120.md)), extensionless highlight ([121](bugs/121.md)), `*` history ([122](bugs/122.md)), `:s` ReDoS guard ([123](bugs/123.md)).
  * **Round 6 (2026-11-09, hg rev 283+ — [110](bugs/110.md)–[115](bugs/115.md), `fix_status: fixed`)**: 6 deep-audit inventories covering wrong-logic bugs ([110](bugs/110.md)), robustness and `None`-safety ([111](bugs/111.md)), dead code ([112](bugs/112.md)), duplicate logic ([113](bugs/113.md)), unoptimized algorithms and memory growth ([114](bugs/114.md)), and low readability ([115](bugs/115.md)). All items resolved with regression tests (247 passed, `mypy .` 57 files clean, ruff clean).
  * **Round 5 (2026-11-09, hg rev 256 / `v0.11.1` — [093](bugs/093.md)–[109](bugs/109.md), `fix_status: fixed`)**: 17 machine-confirmed findings from a fresh full-project re-audit of the fully-fixed tree (4 parallel auditors + dynamic probes). Two documented fixes were regressed — [093](bugs/093.md) (091 previewer still leaks `ignore_case`; HIGH) and [102](bugs/102.md) (090 lazy-import hoist never applied) — plus Vim-semantics defects, an incomplete [081](bugs/081.md) gate fix, renderer/test/tooling gaps and cleanup inventories. All 17 closed in rev 258–275. Gates: 217 passed / 1 skipped, `mypy .` 56 files, ruff clean.
  * **Round 4 addendum — stem-scan & visual-block audit (2026-11-08–2026-11-09, hg rev 270+ — fixed)**: 13 machine-verified findings ([080](bugs/080.md)–[092](bugs/092.md)) — full-repo re-scan against all prior report classes at 4 parallel auditors + confirmatory E2E harness (HIGH: `:b <pattern>` crash with untitled buffers) plus [092](bugs/092.md) (visual block over lines with SARA AM overshoots mid-line).
  * **Round 3 addendum (2026-11-08, user-reported field bugs — fixed in rev 267–269)**: 4 machine-verified findings ([076](bugs/076.md)–[079](bugs/079.md)) — Thai visual selection fixes (line-relative selection crash, block-column mark counting) and extrapolation audit (`j`/`k` visual-column drift, block `I`/`A` cluster splitting).
  * **Round 3 (2026-11-08, hg rev 87de3a3 — fixed in rev 253–265)**: 13 machine-verified findings ([063](bugs/063.md)–[075](bugs/075.md)) — 9 defects (`type: bug`: cell-cluster motion overrides, `TextObject` rollback + fragile Thai patch internals, legacy lexer fallback, unguarded None-invariants, quit/`:b` semantics, `__main__` import side-effect, reporter invalidation, startup/IO lows, grouped Vim/cold-text deviations) and 4 improvements (`type: improvement`: `O(n)` keystroke work + per-frame allocations, duplicated word-motion walks, dead code/example, readability). Originally filed as 18 findings (063–080) at hg rev 251, re-validated against the tip, merged/renumbered on 2026-11-08, then fixed finding-by-finding (179 tests, mypy/ruff clean — mapping + fix revs in [`bugs/index.md`](bugs/index.md)).
  * **Round 2 (2026-11-07, hg rev 191–233 — closed in rev 194–233)**: 25 machine-verified findings ([038](bugs/038.md)–[062](bugs/062.md)) — 18 defects (`type: bug`, including high-priority crashes and Thai/Lao Visual mode, inclusive motion, word-completion, and right-margin combining-mark bugs) and 7 improvements (`type: improvement`: performance, dead code, duplication, readability/test hygiene, and plan/benchmark drift).
  * **Round 1 (2026-11-06, hg rev 164 — closed in rev 167–195)**: 37 machine-verified findings ([001](bugs/001.md)–[037](bugs/037.md)) — defects 001–016 and improvements 017–037, all fixed with recorded hg revisions.

## Meta

* [Bundle Update Log](log.md) — Chronological log of documentation bundle additions and updates.

## Name-mapping note

These documents were written under the `pyvim` name. Since the 2026-11 rebrand, all live code paths are `vith/…`; the `CHANGELOG` appendix preserves the `pyvim` history verbatim, and the `LICENSE` carries the stacked copyright (Slenders 2015 + Suttipongsakul 2026).
