"""Canonical benchmark case catalog."""

from __future__ import annotations

from benchmark_support import ROOT, BenchmarkCase


def define_cases() -> list[BenchmarkCase]:
    return [
        BenchmarkCase(
            name="file_io",
            display_name="File I/O Roundtrip",
            iterations=36_000,
            unit="bytes-processed",
            files={
                "ailang": ROOT / "ailang" / "file_io.ail",
                "c": ROOT / "c" / "file_io.c",
                "rust": ROOT / "rust" / "file_io.rs",
                "python": ROOT / "python" / "file_io.py",
            },
        ),
        BenchmarkCase(
            name="dict_ops",
            display_name="Dictionary Updates",
            iterations=300_000,
            unit="updates",
            files={
                "ailang": ROOT / "ailang" / "dict_ops.ail",
                "c": ROOT / "c" / "dict_ops.c",
                "rust": ROOT / "rust" / "dict_ops.rs",
                "python": ROOT / "python" / "dict_ops.py",
            },
        ),
        BenchmarkCase(
            name="records_bench",
            display_name="Record Field Access",
            iterations=4_000_000,
            unit="updates",
            files={
                "ailang": ROOT / "ailang" / "records_bench.ail",
                "c": ROOT / "c" / "records_bench.c",
                "rust": ROOT / "rust" / "records_bench.rs",
                "python": ROOT / "python" / "records_bench.py",
            },
        ),
        BenchmarkCase(
            name="format_print",
            display_name="Print Formatting",
            iterations=100,
            unit="print-calls",
            files={
                "ailang": ROOT / "ailang" / "format_print.ail",
                "c": ROOT / "c" / "format_print.c",
                "rust": ROOT / "rust" / "format_print.rs",
                "python": ROOT / "python" / "format_print.py",
            },
        ),
        BenchmarkCase(
            name="format_str_int",
            display_name="str(int) Formatting",
            iterations=400_000,
            unit="conversions",
            files={
                "ailang": ROOT / "ailang" / "format_str_int.ail",
                "c": ROOT / "c" / "format_str_int.c",
                "rust": ROOT / "rust" / "format_str_int.rs",
                "python": ROOT / "python" / "format_str_int.py",
            },
        ),
        BenchmarkCase(
            name="format_hex",
            display_name="hex() Formatting",
            iterations=400_000,
            unit="conversions",
            files={
                "ailang": ROOT / "ailang" / "format_hex.ail",
                "c": ROOT / "c" / "format_hex.c",
                "rust": ROOT / "rust" / "format_hex.rs",
                "python": ROOT / "python" / "format_hex.py",
            },
        ),
        BenchmarkCase(
            name="format_interp",
            display_name="Interpolation Formatting",
            iterations=1_000,
            unit="conversions",
            files={
                "ailang": ROOT / "ailang" / "format_interp.ail",
                "c": ROOT / "c" / "format_interp.c",
                "rust": ROOT / "rust" / "format_interp.rs",
                "python": ROOT / "python" / "format_interp.py",
            },
        ),
        BenchmarkCase(
            name="fixed_array_sum",
            display_name="Fixed Array Sum",
            iterations=2_000_000,
            unit="element-adds",
            files={
                "ailang": ROOT / "ailang" / "fixed_array_sum.ail",
                "c": ROOT / "c" / "fixed_array_sum.c",
                "rust": ROOT / "rust" / "fixed_array_sum.rs",
                "python": ROOT / "python" / "fixed_array_sum.py",
            },
        ),
        BenchmarkCase(
            name="slice_sum",
            display_name="Slice/View Sum",
            iterations=2_000_000,
            unit="element-adds",
            files={
                "ailang": ROOT / "ailang" / "slice_sum.ail",
                "c": ROOT / "c" / "slice_sum.c",
                "rust": ROOT / "rust" / "slice_sum.rs",
                "python": ROOT / "python" / "slice_sum.py",
            },
        ),
        BenchmarkCase(
            name="recursive_traversal",
            display_name="Recursive Traversal",
            iterations=2_178_309,
            unit="calls",
            files={
                "ailang": ROOT / "ailang" / "recursive_traversal.ail",
                "c": ROOT / "c" / "recursive_traversal.c",
                "rust": ROOT / "rust" / "recursive_traversal.rs",
                "python": ROOT / "python" / "recursive_traversal.py",
            },
        ),
        BenchmarkCase(
            name="loop_hash",
            display_name="Loop Hash Mix",
            iterations=12_000_000,
            unit="operations",
            files={
                "ailang": ROOT / "ailang" / "loop_hash.ail",
                "c": ROOT / "c" / "loop_hash.c",
                "rust": ROOT / "rust" / "loop_hash.rs",
                "python": ROOT / "python" / "loop_hash.py",
            },
        ),
        BenchmarkCase(
            name="fib_mix",
            display_name="Fibonacci Mix",
            iterations=8_000_000,
            unit="iterations",
            files={
                "ailang": ROOT / "ailang" / "fib_mix.ail",
                "c": ROOT / "c" / "fib_mix.c",
                "rust": ROOT / "rust" / "fib_mix.rs",
                "python": ROOT / "python" / "fib_mix.py",
            },
        ),
    ]
