#!/usr/bin/env python3
# mypy: disable-error-code="import-not-found,import-untyped,misc"
"""
Demo/benchmark: using `quez` for cold text, not for the active editor buffer.

`quez.CompressedDeque` is a compressed LIFO/FIFO store. That makes it a good
candidate for data that is not on the keystroke path:

* full-document undo/redo snapshots,
* inactive buffers,
* chunked cold storage for later materialization.

It is *not* a replacement for `prompt_toolkit.Document`/`Buffer`: it has no
random access, line indexing, slicing, or middle insertion.

Run with:
    python tools/bench_quez_cold_text.py
    python tools/bench_quez_cold_text.py --sizes-mb 1,5,20 --snapshots 20
    python tools/bench_quez_cold_text.py --compressor zlib-fast

Install the optional benchmark dependency with:
    python -m pip install quez
    python -m pip install python-lzo   # for --compressor lzo
"""

from __future__ import annotations

import argparse
import gc
import random
import statistics
import sys
import time
from collections import deque
from typing import Any, Callable

try:
    import quez
    from quez import CompressedDeque, NullCompressor, ZlibCompressor
    from quez.compressors import Compressor
except ImportError:
    print("quez is not installed. Install it with: python -m pip install quez")
    raise SystemExit(2)

try:
    from quez import LzoCompressor as _LzoCompressor
except ImportError:
    _LzoCompressor = None

try:
    from quez import ZstdCompressor as _ZstdCompressor
except ImportError:
    _ZstdCompressor = None


_MB = 1024 * 1024

_SOURCE_WORDS = (
    "alpha",
    "beta",
    "gamma",
    "delta",
    "epsilon",
    "zeta",
    "eta",
    "theta",
    "iota",
    "kappa",
    "lambda",
    "mu",
    "nu",
    "xi",
    "omicron",
    "pi",
    "rho",
    "sigma",
    "tau",
    "upsilon",
    "phi",
    "chi",
    "psi",
    "omega",
)

_THAI_WORDS = (
    "การ",
    "เดินทาง",
    "ใน",
    "ที่ดิน",
    "ของ",
    "ผู้ใหญ่",
    "บ้าน",
    "และ",
    "การพัฒนา",
    "โปรแกรม",
    "ภาษาไทย",
    "เทอร์มินัล",
    "ข้อมูล",
    "ระบบ",
    "การทำงาน",
    "ความเร็ว",
    "หน่วยความจำ",
    "ไฟล์",
    "ข้อความ",
    "ตัวแก้ไข",
    "การค้นหา",
    "การแทนที่",
    "บรรทัด",
    "คอลัมน์",
    "ตัวอักษร",
    "คำ",
    "ประโยค",
    "เอกสาร",
    "โครงสร้าง",
    "หน่วย",
    "การบันทึก",
    "ประวัติ",
    "ย้อนกลับ",
    "ทำซ้ำ",
    "หน่วยความจำสำรอง",
    "การบีบอัด",
    "ข้อมูลเย็น",
    "ประสิทธิภาพ",
    "ความหน่วง",
    "การทดสอบ",
)


class ColdTextStore:
    """
    Bounded LIFO store for cold text snapshots.

    This is the shape a future vith integration would use for undo/redo or
    inactive buffers. It deliberately exposes only `push`/`pop`; it is not a
    text buffer.
    """

    def __init__(self, max_snapshots: int, compressor: Compressor) -> None:
        self._snapshots = CompressedDeque(maxsize=max_snapshots, compressor=compressor)

    def push(self, text: str) -> None:
        self._snapshots.append(text)

    def pop(self) -> str:
        return self._snapshots.pop()

    @property
    def stats(self) -> dict[str, Any]:
        return self._snapshots.stats


def build_source_text(target_bytes: int, seed: int = 42) -> str:
    """Generate varied Python-like source text close to `target_bytes`."""
    rng = random.Random(seed)
    parts: list[str] = []
    total = 0
    index = 0
    while total < target_bytes:
        name = rng.choice(_SOURCE_WORDS)
        arg = rng.randrange(1000)
        value = rng.randrange(1000)
        line = (
            f"def {name}_{index}(arg_{arg}, value_{value}):\n"
            f"    result = arg_{arg} * {rng.randrange(1000)} + value_{value}\n"
            f"    return result  # {rng.choice(_SOURCE_WORDS)}\n"
        )
        parts.append(line)
        total += len(line)
        index += 1
    return "".join(parts)[:target_bytes]


def build_thai_text(target_bytes: int, seed: int = 42) -> str:
    """Generate varied Thai text close to `target_bytes` in UTF-8."""
    rng = random.Random(seed)
    parts: list[str] = []
    total = 0
    index = 0
    while total < target_bytes:
        line = (
            f"{rng.choice(_THAI_WORDS)}{rng.choice(_THAI_WORDS)}"
            f"{rng.choice(_THAI_WORDS)} {rng.choice(_THAI_WORDS)}"
            f"{rng.choice(_THAI_WORDS)} ({index})\n"
        )
        parts.append(line)
        total += len(line.encode("utf-8"))
        index += 1
    return "".join(parts)


def build_text(kind: str, size_mb: float, seed: int = 42) -> str:
    target_bytes = int(size_mb * _MB)
    if kind == "source":
        return build_source_text(target_bytes, seed=seed)
    if kind == "thai":
        return build_thai_text(target_bytes, seed=seed)
    raise ValueError(f"unknown text kind: {kind!r}")


def make_compressor(name: str) -> Compressor:
    if name == "zlib":
        return ZlibCompressor()
    if name == "zlib-fast":
        return ZlibCompressor(level=1)
    if name == "zlib-best":
        return ZlibCompressor(level=9)
    if name == "lzo":
        if _LzoCompressor is None:
            raise ValueError(
                "python-lzo is not installed; install it with: "
                "LZO_DIR=/opt/homebrew CFLAGS='-I/opt/homebrew/include' "
                "LDFLAGS='-L/opt/homebrew/lib' python -m pip install python-lzo"
            )
        return _LzoCompressor()
    if name == "zstd":
        if _ZstdCompressor is None:
            raise ValueError("zstd is not available; use Python 3.14+ or install zstandard")
        return _ZstdCompressor(level=1)
    if name == "null":
        return NullCompressor()
    raise ValueError(f"unknown compressor: {name!r}")


def measure_ns(repeats: int, operation: Callable[[], int]) -> float:
    """Return the median of `operation()` results in milliseconds."""
    samples: list[int] = []
    for _ in range(repeats):
        gc.collect()
        samples.append(operation())
    return statistics.median(samples) / 1_000_000


def mb(num_bytes: int | float) -> float:
    return num_bytes / _MB


def compressed_mb(stats: dict[str, Any]) -> float:
    return mb(int(stats["compressed_size_bytes"]))


def raw_mb(stats: dict[str, Any]) -> float:
    return mb(int(stats["raw_size_bytes"]))


def bench_snapshot_roundtrip(
    sizes_mb: list[float],
    kinds: list[str],
    compressor: Compressor,
    compressor_name: str,
    repeats: int,
    seed: int,
) -> None:
    print("=== 1. Cold snapshot round-trip (one full-document snapshot) ===")
    print(f"compressor: {compressor_name}")
    print(
        f"{'kind':7s} {'req MB':>7s} {'utf8 MB':>8s} {'str MB':>8s} "
        f"{'push ms':>9s} {'pop ms':>8s} {'comp MB':>9s} {'vs str':>7s}"
    )
    for kind in kinds:
        for requested_mb in sizes_mb:
            text = build_text(kind, requested_mb, seed=seed)
            text_bytes = len(text.encode("utf-8"))
            stores: list[ColdTextStore] = []

            def push_once() -> int:
                store = ColdTextStore(2, compressor)
                start = time.perf_counter_ns()
                store.push(text)
                elapsed = time.perf_counter_ns() - start
                stores.append(store)
                return elapsed

            push_ms = measure_ns(repeats, push_once)
            stats = stores[0].stats

            def pop_once() -> int:
                store = ColdTextStore(2, compressor)
                store.push(text)
                start = time.perf_counter_ns()
                popped = store.pop()
                elapsed = time.perf_counter_ns() - start
                assert popped == text
                return elapsed

            pop_ms = measure_ns(repeats, pop_once)
            comp_mb = compressed_mb(stats)
            str_mb = mb(sys.getsizeof(text))
            ratio = comp_mb / str_mb if str_mb else 0.0
            print(
                f"{kind:7s} {requested_mb:7.1f} {mb(text_bytes):8.2f} "
                f"{str_mb:8.2f} {push_ms:9.2f} {pop_ms:8.2f} "
                f"{comp_mb:9.2f} {ratio:7.1%}"
            )
    print()


def bench_pickle_baseline(
    size_mb: float, compressor: Compressor, compressor_name: str, repeats: int, seed: int
) -> None:
    print("=== 1b. Quez/pickle overhead baseline (no compression) ===")
    text = build_text("source", size_mb, seed=seed)
    stores: list[ColdTextStore] = []

    def push_once() -> int:
        store = ColdTextStore(2, compressor)
        start = time.perf_counter_ns()
        store.push(text)
        elapsed = time.perf_counter_ns() - start
        stores.append(store)
        return elapsed

    push_ms = measure_ns(repeats, push_once)
    stats = stores[0].stats

    def pop_once() -> int:
        store = ColdTextStore(2, compressor)
        store.push(text)
        start = time.perf_counter_ns()
        popped = store.pop()
        elapsed = time.perf_counter_ns() - start
        assert popped == text
        return elapsed

    pop_ms = measure_ns(repeats, pop_once)
    print(
        f"{compressor_name} | {size_mb:.1f} MB source | "
        f"push {push_ms:.2f} ms | pop {pop_ms:.2f} ms | "
        f"stored {compressed_mb(stats):.2f} MB (raw {raw_mb(stats):.2f} MB)\n"
    )


def bench_undo_burst(
    size_mb: float,
    snapshot_count: int,
    compressor: Compressor,
    compressor_name: str,
    seed: int,
) -> None:
    print("=== 2. Undo-burst simulation ===")
    base = build_text("source", size_mb, seed=seed)
    snapshots = [base + f"\n# snapshot {i}\n" for i in range(snapshot_count)]
    raw_snapshot_bytes = sum(sys.getsizeof(snapshot) for snapshot in snapshots)

    store = ColdTextStore(snapshot_count, compressor)
    push_samples: list[int] = []
    for snapshot in snapshots:
        start = time.perf_counter_ns()
        store.push(snapshot)
        push_samples.append(time.perf_counter_ns() - start)
    stats = store.stats

    pop_samples: list[int] = []
    popped: list[str] = []
    for _ in range(snapshot_count):
        start = time.perf_counter_ns()
        popped.append(store.pop())
        pop_samples.append(time.perf_counter_ns() - start)
    assert popped == list(reversed(snapshots))

    raw_queue: deque[str] = deque(maxlen=snapshot_count)
    raw_push_samples: list[int] = []
    for snapshot in snapshots:
        start = time.perf_counter_ns()
        raw_queue.append(snapshot)
        raw_push_samples.append(time.perf_counter_ns() - start)

    raw_pop_samples: list[int] = []
    for _ in range(snapshot_count):
        start = time.perf_counter_ns()
        raw_queue.pop()
        raw_pop_samples.append(time.perf_counter_ns() - start)

    def total_ms(samples: list[int]) -> float:
        return sum(samples) / 1_000_000

    def avg_ms(samples: list[int]) -> float:
        return statistics.mean(samples) / 1_000_000

    def max_ms(samples: list[int]) -> float:
        return max(samples) / 1_000_000

    def total_us(samples: list[int]) -> float:
        return sum(samples) / 1_000

    def avg_us(samples: list[int]) -> float:
        return statistics.mean(samples) / 1_000

    def max_us(samples: list[int]) -> float:
        return max(samples) / 1_000

    print(f"{snapshot_count} snapshots of ~{size_mb:.1f} MB | compressor: {compressor_name}")
    print(
        f"  CompressedDeque: push {total_ms(push_samples):7.2f} ms total "
        f"({avg_ms(push_samples):5.2f} avg, {max_ms(push_samples):5.2f} max) | "
        f"pop {total_ms(pop_samples):7.2f} ms total "
        f"({avg_ms(pop_samples):5.2f} avg, {max_ms(pop_samples):5.2f} max)"
    )
    print(
        f"  Compressed size: {compressed_mb(stats):7.2f} MB "
        f"(raw pickle {raw_mb(stats):7.2f} MB, raw str estimate {mb(raw_snapshot_bytes):7.2f} MB)"
    )
    print(
        f"  deque[str]:      push {total_us(raw_push_samples):7.2f} us total "
        f"({avg_us(raw_push_samples):5.2f} avg, {max_us(raw_push_samples):5.2f} max) | "
        f"pop {total_us(raw_pop_samples):7.2f} us total "
        f"({avg_us(raw_pop_samples):5.2f} avg, {max_us(raw_pop_samples):5.2f} max)"
    )
    print()


def bench_chunked_store(
    size_mb: float,
    chunk_kib: int,
    compressor: Compressor,
    compressor_name: str,
    seed: int,
) -> None:
    print("=== 3. Chunked cold store (many independent compressed chunks) ===")
    text = build_text("source", size_mb, seed=seed)
    chunk_chars = chunk_kib * 1024
    chunks = [text[i : i + chunk_chars] for i in range(0, len(text), chunk_chars)]
    store = ColdTextStore(len(chunks), compressor)

    push_samples: list[int] = []
    for chunk in chunks:
        start = time.perf_counter_ns()
        store.push(chunk)
        push_samples.append(time.perf_counter_ns() - start)
    stats = store.stats

    pop_samples: list[int] = []
    popped: list[str] = []
    for _ in range(len(chunks)):
        start = time.perf_counter_ns()
        popped.append(store.pop())
        pop_samples.append(time.perf_counter_ns() - start)
    assert "".join(reversed(popped)) == text

    whole_store = ColdTextStore(1, compressor)
    start = time.perf_counter_ns()
    whole_store.push(text)
    whole_push_ms = (time.perf_counter_ns() - start) / 1_000_000
    whole_stats = whole_store.stats
    start = time.perf_counter_ns()
    whole_popped = whole_store.pop()
    whole_pop_ms = (time.perf_counter_ns() - start) / 1_000_000
    assert whole_popped == text

    def total_ms(samples: list[int]) -> float:
        return sum(samples) / 1_000_000

    def avg_ms(samples: list[int]) -> float:
        return statistics.mean(samples) / 1_000_000

    chunk_overhead = compressed_mb(stats) / compressed_mb(whole_stats) - 1.0
    print(f"{len(chunks)} chunks x {chunk_kib} KiB | compressor: {compressor_name}")
    print(
        f"  chunked: push {total_ms(push_samples):8.2f} ms total "
        f"({avg_ms(push_samples):5.3f} avg) | "
        f"pop {total_ms(pop_samples):8.2f} ms total "
        f"({avg_ms(pop_samples):5.3f} avg)"
    )
    print(
        f"  chunked compressed: {compressed_mb(stats):6.2f} MB | "
        f"whole compressed: {compressed_mb(whole_stats):6.2f} MB "
        f"(chunking overhead {chunk_overhead:+.1%})"
    )
    print(f"  whole snapshot: push {whole_push_ms:6.2f} ms | pop {whole_pop_ms:6.2f} ms")
    print()


def parse_sizes(value: str) -> list[float]:
    sizes: list[float] = []
    for part in value.split(","):
        part = part.strip()
        if part:
            sizes.append(float(part))
    if not sizes:
        raise argparse.ArgumentTypeError("at least one size is required")
    return sizes


def parse_kinds(value: str) -> list[str]:
    kinds = [part.strip() for part in value.split(",") if part.strip()]
    allowed = {"source", "thai"}
    unknown = set(kinds) - allowed
    if unknown:
        raise argparse.ArgumentTypeError(f"unknown text kind(s): {sorted(unknown)}")
    return kinds


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sizes-mb",
        type=parse_sizes,
        default=[1.0, 5.0, 20.0],
        help="comma-separated snapshot sizes in MiB (default: 1,5,20)",
    )
    parser.add_argument(
        "--kinds",
        type=parse_kinds,
        default=["source", "thai"],
        help="comma-separated text kinds: source,thai (default: both)",
    )
    parser.add_argument(
        "--compressor",
        choices=["zlib", "zlib-fast", "zlib-best", "lzo", "zstd", "null"],
        default="zlib",
        help="compressor for the main tables; zstd means level 1 (default: zlib)",
    )
    parser.add_argument(
        "--snapshots",
        type=int,
        default=20,
        help="number of snapshots in the undo-burst simulation (default: 20)",
    )
    parser.add_argument(
        "--chunk-kib",
        type=int,
        default=64,
        help="chunk size for the chunked cold store (default: 64 KiB)",
    )
    parser.add_argument(
        "--repeats",
        type=int,
        default=3,
        help="repetitions per measured operation (default: 3)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="deterministic synthetic-text seed (default: 42)",
    )
    args = parser.parse_args()

    compressor = make_compressor(args.compressor)
    print("=== quez cold-text benchmark for vith ===")
    print(
        f"Python {sys.version.split()[0]} | quez {quez.__version__} | "
        f"compressor={args.compressor} | repeats={args.repeats}"
    )
    print("Cold text only: this is not the active Document/Buffer path.\n")

    bench_snapshot_roundtrip(
        sizes_mb=args.sizes_mb,
        kinds=args.kinds,
        compressor=compressor,
        compressor_name=args.compressor,
        repeats=args.repeats,
        seed=args.seed,
    )
    bench_pickle_baseline(
        size_mb=args.sizes_mb[0],
        compressor=NullCompressor(),
        compressor_name="null",
        repeats=args.repeats,
        seed=args.seed,
    )
    bench_undo_burst(
        size_mb=min(1.0, args.sizes_mb[0]),
        snapshot_count=args.snapshots,
        compressor=compressor,
        compressor_name=args.compressor,
        seed=args.seed,
    )
    bench_chunked_store(
        size_mb=args.sizes_mb[-1],
        chunk_kib=args.chunk_kib,
        compressor=compressor,
        compressor_name=args.compressor,
        seed=args.seed,
    )

    print("Notes:")
    print("  * Text is synthetic; compression ratios are illustrative, not corpus benchmarks.")
    print("  * Compression/decompression are CPU-bound. Do them in a worker")
    print("    thread/executor or use quez.AsyncCompressedDeque, never on the")
    print("    keypress path.")
    print("  * Undo snapshots are full documents here. A production design may")
    print("    store diffs or persistent chunks instead, which changes the trade-off.")
    print("  * quez has no random access, line index, or middle insertion; it is")
    print("    suitable for cold snapshots, not for prompt_toolkit's active buffer.")


if __name__ == "__main__":
    main()
