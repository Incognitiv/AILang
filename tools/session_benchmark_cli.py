from __future__ import annotations

try:
    from .session_benchmark_common import *
except ImportError:
    from session_benchmark_common import *

try:
    from .session_benchmark_core import *
except ImportError:
    from session_benchmark_core import *

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Session benchmark/safety harness")
    sub = p.add_subparsers(dest="cmd", required=True)

    cap = sub.add_parser("capture", help="Capture one labeled session snapshot")
    cap.add_argument("--label", required=True, help="Session label (folder name)")
    cap.add_argument("--runs", type=int, default=3, help="Measured benchmark runs")
    cap.add_argument("--warmup", type=int, default=1, help="Warmup runs")
    cap.add_argument(
        "--case",
        action="append",
        default=[],
        choices=[
            "loop_hash",
            "fib_mix",
            "file_io",
            "dict_ops",
            "records_bench",
            "format_print",
            "format_str_int",
            "format_hex",
            "format_interp",
            "fixed_array_sum",
            "slice_sum",
            "recursive_traversal",
        ],
        help="Optional case filter (repeatable).",
    )
    cap.add_argument(
        "--impl",
        action="append",
        default=[],
        choices=[
            "ailang_jit",
            "ailang_jit_warm",
            "ailang_aot",
            "c23",
            "rust",
            "python",
        ],
        help="Optional impl filter (repeatable).",
    )
    cap.add_argument(
        "--check-leaks",
        action="store_true",
        help="Enable leak budget checks using leak columns from benchmark runner.",
    )
    cap.add_argument(
        "--sample-memory",
        action="store_true",
        help="Capture peak RSS with psutil sampling during benchmark runs.",
    )
    cap.add_argument(
        "--leak-threshold",
        type=int,
        default=0,
        help="Allowed max live bytes when --check-leaks is enabled. (default: 0)",
    )

    cmp_p = sub.add_parser("compare", help="Compare two captured sessions")
    cmp_p.add_argument("--before", required=True, help="Before session label")
    cmp_p.add_argument("--after", required=True, help="After session label")
    cmp_p.add_argument(
        "--output",
        default=None,
        help="Output markdown path (default: benchmarks/sessions/compare_<before>_vs_<after>.md)",
    )
    return p.parse_args()
def main() -> int:
    args = parse_args()
    if args.cmd == "capture":
        return _collect_capture(
            label=args.label,
            runs=args.runs,
            warmup=args.warmup,
            cases=args.case,
            impls=args.impl,
            sample_memory=args.sample_memory,
            check_leaks=args.check_leaks,
            leak_threshold=args.leak_threshold,
        )
    output = (
        Path(args.output)
        if args.output
        else SESSION_ROOT / f"compare_{args.before}_vs_{args.after}.md"
    )
    return _compare_sessions(args.before, args.after, output)

__all__ = [name for name in globals() if not name.startswith("__")]
