"""
Fast JIT Compilation for AILang using LLVM ORC JIT
Uses llvmlite's MCJIT engine for in-memory compilation and execution
"""

import argparse
import ctypes
import ctypes.util
import json
import os
import re
import sys
import tempfile
import time
from parser.parser import Parser
from typing import Any, Optional

from lexer.scan import tokenize
from llvmlite import binding
from runtime.unsafe_registry import prompt_unsafe

from .codegen import CodeGen
from .fast_jit_runtime import install_profile_crash_atexit, run_jit_main
from .fast_jit_unsafe_scan import scan_for_unsafe
from .fast_jit_worker import JIT_WORKER_MARKER, run_jit_worker_subprocess

# Initialize LLVM native target (same as compiler.py)
binding.initialize_native_target()
binding.initialize_native_asmprinter()

DEFAULT_JIT_OPT_LEVEL = 3


def _normalize_jit_opt(optimize: bool = True, jit_opt: Optional[int] = None) -> int:
    """Resolve legacy optimize=True/False and explicit --jit-opt into 0..3."""
    if jit_opt is None:
        return DEFAULT_JIT_OPT_LEVEL if optimize else 0
    try:
        return max(0, min(3, int(jit_opt)))
    except (TypeError, ValueError):
        return DEFAULT_JIT_OPT_LEVEL if optimize else 0


def _write_ir_dump(path: str, ir_code: str) -> None:
    """Write optimized JIT IR for AOT/JIT parity inspection."""
    if not path:
        return
    abs_path = os.path.abspath(path)
    parent = os.path.dirname(abs_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(abs_path, "w", encoding="utf-8") as f:
        f.write(ir_code)


def create_execution_engine() -> Any:
    """
    Create an LLVM execution engine with ORC JIT
    This enables instant in-memory compilation and execution
    """
    # Create target machine with native CPU features (enables AVX2, AVX-512, etc.)
    target = binding.Target.from_default_triple()
    cpu_name = binding.get_host_cpu_name()
    features = binding.get_host_cpu_features().flatten()
    target_machine = target.create_target_machine(
        cpu=cpu_name, features=features, opt=2
    )

    # Create execution engine with optimization
    backing_mod = binding.parse_assembly("")
    engine = binding.create_mcjit_compiler(backing_mod, target_machine)

    return engine


def _flush_c_stdout() -> None:
    """Best-effort flush of C-backed stdout on all platforms."""
    candidates: list[str]
    if os.name == "nt":
        candidates = [
            "msvcrt",
            "ucrtbase",
            "api-ms-win-crt-runtime-l1-1-0",
            "libc",
        ]
    else:
        candidates = ["c", "libc.so.6", "libc.dylib", "libc-2.39.so"]

    for lib_name in candidates:
        try:
            ctypes.CDLL(lib_name).fflush(None)
            return
        except (OSError, AttributeError, TypeError):
            continue


def _extract_result_int(text: str) -> Optional[int]:
    tokens = re.findall(r"[-+]?\d+", text.replace("\r", "\n"))
    if not tokens:
        return None
    return int(tokens[-1])


def _run_jit_once(
    cfunc: Any,
    source_file: str,
    *,
    profile: bool = False,
    flame_path: str = "",
    sample_hz: int = 0,
    capture_stdout: bool = False,
    trap_crash: bool = True,
) -> tuple[int, Optional[str]]:
    """Execute one JIT `main()` call.

    When capture_stdout is True, all program output is captured and returned.
    """
    if not capture_stdout:
        if not trap_crash and not profile and sample_hz <= 0 and not flame_path:
            return int(cfunc()), None
        return (
            int(
                run_jit_main(
                    cfunc,
                    source_file=source_file,
                    profile=profile,
                    flame_path=flame_path,
                    sample_hz=sample_hz,
                )
            ),
            None,
        )

    with tempfile.TemporaryFile(mode="w+b") as capture:
        saved_stdout = os.dup(1)
        os.dup2(capture.fileno(), 1)
        try:
            if not trap_crash and not profile and sample_hz <= 0 and not flame_path:
                result = int(cfunc())
            else:
                result = int(
                    run_jit_main(
                        cfunc,
                        source_file=source_file,
                        profile=profile,
                        flame_path=flame_path,
                        sample_hz=sample_hz,
                    )
                )
            _flush_c_stdout()
            capture.seek(0)
            raw = capture.read()
            output = raw.decode("utf-8", errors="replace")
            return result, output
        finally:
            os.dup2(saved_stdout, 1)
            os.close(saved_stdout)


def _build_jit_callable(
    source_code: str,
    optimize: bool = True,
    jit_opt: Optional[int] = None,
    source_file: str = "",
    profile: bool = False,
    flame_path: str = "",
    sample_hz: int = 0,
    dump_ir_path: str = "",
) -> tuple[Optional[Any], Optional[Any], str]:
    """Build a JIT-compiled `main` callable and return it with the LLJIT tracker."""
    if flame_path or sample_hz > 0:
        profile = True

    # 1. Tokenize
    tokens = tokenize(source_code)

    # 2. Parse
    parser = Parser(tokens)
    ast = parser.parse_program()

    # 2.5. Scan for unsafe operations and prompt user
    scan_for_unsafe(ast)
    if not prompt_unsafe():
        return None, None, "denied"

    # 2.7. If profiling, install the JIT-callable thunks BEFORE codegen so
    # that by the time the linker resolves __ailang_prof_enter/exit the
    # symbols are already registered with llvmlite's binding layer.
    if profile:
        from runtime import profiler

        profiler.install()
        profiler.reset()
        # On Windows, ctypes thunks on the stack can prevent the SEH-to-
        # OSError translation, so an access violation in JIT'd code may
        # terminate the process before our try/except sees anything. The
        # profiler's call_stack survives in Python memory regardless, so
        # an atexit hook is the most reliable way to get the AILang stack
        # in front of the user when the program crashes mid-call.
        install_profile_crash_atexit(source_file)

    # 3. Generate LLVM IR
    codegen = CodeGen()
    codegen.profile_enabled = profile
    ir_code = codegen.generate(ast, source_file=source_file)

    # Hand the codegen-built source map to the profiler so blame and crash
    # output can render `func @ file:line` instead of bare names.
    if profile:
        from runtime import profiler as _profiler

        _profiler.set_source_map(codegen.source_map)

    # 4. Create target machine with native CPU features
    target = binding.Target.from_default_triple()
    cpu_name = binding.get_host_cpu_name()
    features = binding.get_host_cpu_features().flatten()
    opt_level = _normalize_jit_opt(optimize=optimize, jit_opt=jit_opt)
    target_machine = target.create_target_machine(
        cpu=cpu_name, features=features, opt=opt_level
    )

    # 5. Apply LLVM optimization passes. LLJIT does not optimize IR internally,
    # so benchmark/server mode must run an explicit O-level pipeline first.
    llvm_module = binding.parse_assembly(ir_code)
    llvm_module.verify()

    if opt_level > 0:
        # Pre-pass: Run SROA on each function to promote allocas to SSA registers
        # This MUST happen before inlining to avoid stacksave/stackrestore overhead
        # when functions with stack allocations are inlined into loops
        fpm = binding.create_new_function_pass_manager()
        fpm.add_sroa_pass()  # Promote allocas to SSA registers
        fpm.add_instruction_combine_pass()  # Clean up redundant instructions
        pto_pre = binding.PipelineTuningOptions(speed_level=opt_level)
        pb_pre = binding.create_pass_builder(target_machine, pto_pre)
        for func in llvm_module.functions:
            if not func.is_declaration:
                fpm.run(func, pb_pre)

        # Main pass: Full module optimization (includes inlining, vectorization, etc.)
        pto = binding.PipelineTuningOptions(speed_level=opt_level)
        # NOTE: Do NOT set inlining_threshold - it breaks SIMD code generation
        pb = binding.create_pass_builder(target_machine, pto)
        mpm = pb.getModulePassManager()
        mpm.run(llvm_module, pb)

    ir_code = str(llvm_module)  # Optimized IR for O1-O3; verified IR for O0.
    _write_ir_dump(dump_ir_path, ir_code)

    # 6. Use LLJIT (ORC JIT v2) - faster compilation than MCJIT
    lljit = binding.create_lljit_compiler(target_machine)

    lib_builder = binding.JITLibraryBuilder()
    lib_builder.add_ir(ir_code)
    lib_builder.add_current_process()  # Link C library symbols (printf, malloc, etc.)
    lib_builder.export_symbol("main")

    # Wire profile callbacks into the ORC linker. Must happen on lib_builder
    # (per-link), not via binding.add_symbol (MCJIT-only), or LLJIT will fail
    # to resolve __ailang_prof_enter / __ailang_prof_exit at materialize time.
    if profile:
        from runtime import profiler

        for sym_name, sym_addr in profiler.jit_symbols().items():
            lib_builder.import_symbol(sym_name, sym_addr)

    # Load CRT library on Windows (snprintf, strtok_s, etc.).
    _load_crt_library()

    # Load SQLite if needed
    _load_sqlite_library()

    tracker = lib_builder.link(lljit, "ailang_module")

    # 7. Get main function and execute
    try:
        func_addr = tracker["main"]
        cfunc = ctypes.CFUNCTYPE(ctypes.c_int64)(func_addr)
        return cfunc, tracker, "ok"
    except KeyError:
        # No main function - might be a library
        from parser.ast import Library

        is_library = any(isinstance(node, Library) for node in ast)
        if not is_library:
            print("Warning: No main() function found")
            return None, tracker, "no_main"

        return None, tracker, "library"


def fast_jit_compile(
    source_code: str,
    optimize: bool = True,
    jit_opt: Optional[int] = None,
    source_file: str = "",
    profile: bool = False,
    flame_path: str = "",
    sample_hz: int = 0,
    dump_ir_path: str = "",
) -> int:
    """
    Fast JIT compilation using LLJIT (ORC JIT v2).

    Pipeline: Source → LLVM IR → Optimize → LLJIT → Execute (ALL IN MEMORY!)

    Args:
        source_code: AILang source code string
        optimize: Whether to apply LLVM optimization passes (HIGHLY recommended)
        source_file: Path to source file (for resolving imports)
        profile: When True, install the prof callbacks, instrument every
            user function with entry/exit hooks, and print a blame report
            after main() returns. Adds noticeable overhead — debug only.
        flame_path: When non-empty, write Brendan Gregg's "folded-stack"
            output to this path on clean exit. Implies profile=True.
        sample_hz: When >0, also start a background sampler at this rate
            (Hz). Implies profile=True. The deterministic blame and the
            sampled hotspots both print after a clean run.

    Returns:
        exit_code: Return value from main()
    """
    cfunc, _tracker, status = _build_jit_callable(
        source_code,
        optimize=optimize,
        jit_opt=jit_opt,
        source_file=source_file,
        profile=profile,
        flame_path=flame_path,
        sample_hz=sample_hz,
        dump_ir_path=dump_ir_path,
    )
    if cfunc is None:
        return 1 if status == "denied" else 0

    return int(
        run_jit_main(
            cfunc,
            source_file,
            profile=profile,
            flame_path=flame_path,
            sample_hz=sample_hz,
        )
    )


_CRT_STATE: dict[str, bool] = {"loaded": False}


def _load_crt_library() -> None:
    """Load Windows CRT library for JIT (snprintf, strtok_s, etc.)."""
    if _CRT_STATE["loaded"]:
        return
    _CRT_STATE["loaded"] = True

    if sys.platform != "win32":
        return

    # On Windows, load msvcrt.dll for _snprintf, sprintf, strtok_s, etc.
    # ucrtbase.dll makes these inline wrappers, not real exported symbols.
    for lib_name in ("msvcrt", "ucrtbase"):
        _try_load_lib(lib_name + ".dll")


def _try_load_lib(path: str) -> bool:
    """Attempt LLVM library load; swallow OS/runtime errors quietly."""
    try:
        binding.load_library_permanently(path)
        return True
    except (OSError, RuntimeError):
        return False


def _load_sqlite_library() -> None:
    """Attempt to load SQLite library for JIT mode."""
    # Windows ships sqlite under several names depending on toolchain
    # (sqlite3.dll, libsqlite3.dll, MSYS2's libsqlite3-0.dll). Probe each.
    for name in ("sqlite3", "libsqlite3", "libsqlite3-0"):
        sqlite_lib = ctypes.util.find_library(name)
        if sqlite_lib and _try_load_lib(sqlite_lib):
            return

    # Fallback: try common paths on Linux
    if sys.platform != "win32":
        common_paths = [
            "/usr/lib/x86_64-linux-gnu/libsqlite3.so.0",
            "/usr/lib/libsqlite3.so",
            "/usr/local/lib/libsqlite3.so",
        ]
        for path in common_paths:
            if _try_load_lib(path):
                return
    # SQLite not available - that's okay for non-SQLite programs


def fast_jit_repeat_file(
    filename: str,
    run_count: int,
    warmup_count: int = 0,
    optimize: bool = True,
    jit_opt: Optional[int] = None,
    profile: bool = False,
    flame_path: str = "",
    sample_hz: int = 0,
    capture_output: bool = False,
    subprocess_mode: bool = False,
    dump_ir_path: str = "",
) -> dict[str, Any]:
    if subprocess_mode:
        return run_jit_worker_subprocess(
            filename,
            run_count=run_count,
            warmup_count=warmup_count,
            optimize=optimize,
            jit_opt=_normalize_jit_opt(optimize=optimize, jit_opt=jit_opt),
            profile=profile,
            flame_path=flame_path,
            sample_hz=sample_hz,
            capture_output=capture_output,
            dump_ir_path=dump_ir_path,
        )

    return _fast_jit_repeat_file_inprocess(
        filename,
        run_count=run_count,
        warmup_count=warmup_count,
        optimize=optimize,
        jit_opt=jit_opt,
        profile=profile,
        flame_path=flame_path,
        sample_hz=sample_hz,
        capture_output=capture_output,
        dump_ir_path=dump_ir_path,
    )


def _fast_jit_repeat_file_inprocess(
    filename: str,
    run_count: int,
    warmup_count: int = 0,
    optimize: bool = True,
    jit_opt: Optional[int] = None,
    profile: bool = False,
    flame_path: str = "",
    sample_hz: int = 0,
    capture_output: bool = False,
    dump_ir_path: str = "",
) -> dict[str, Any]:
    """Compile once and execute the same JIT'd main repeatedly.

    Returns a measurement payload:
      - status: "ok" or "fail"
      - compile_ms: one-time compilation latency
      - runs_ms: wall-clock milliseconds for measured runs only
      - checksum: parsed numeric output from program output (if captured)
      - note: optional failure reason
    """
    with open(filename, "r", encoding="utf-8") as f:
        source_code = f.read()

    jit_opt_level = _normalize_jit_opt(optimize=optimize, jit_opt=jit_opt)
    compile_start = time.perf_counter()
    source_abspath = os.path.abspath(filename)
    main_callable, _tracker, status = _build_jit_callable(
        source_code,
        optimize=optimize,
        jit_opt=jit_opt_level,
        source_file=source_abspath,
        profile=profile,
        flame_path=flame_path,
        sample_hz=sample_hz,
        dump_ir_path=dump_ir_path,
    )
    compile_ms = (time.perf_counter() - compile_start) * 1000.0

    if main_callable is None:
        if status == "denied":
            return {
                "status": "fail",
                "jit_opt": jit_opt_level,
                "compile_ms": compile_ms,
                "runs_ms": [],
                "checksum": None,
                "note": "JIT unsafe prompt denied execution.",
            }
        if status == "no_main":
            return {
                "status": "fail",
                "jit_opt": jit_opt_level,
                "compile_ms": compile_ms,
                "runs_ms": [],
                "checksum": None,
                "note": "No main() function found.",
            }
        return {
            "status": "fail",
            "jit_opt": jit_opt_level,
            "compile_ms": compile_ms,
            "runs_ms": [],
            "checksum": None,
            "note": "JIT build did not produce an executable main().",
        }

    total_iterations = run_count + warmup_count
    runs_ms: list[float] = []
    outputs: list[int] = []

    for idx in range(total_iterations):
        run_start = time.perf_counter()
        try:
            result, captured = _run_jit_once(
                main_callable,
                source_abspath,
                profile=profile,
                flame_path=flame_path,
                sample_hz=sample_hz,
                capture_stdout=capture_output,
                trap_crash=False,
            )
            _ = result
        except Exception as exc:
            return {
                "status": "fail",
                "jit_opt": jit_opt_level,
                "compile_ms": compile_ms,
                "runs_ms": runs_ms,
                "checksum": None,
                "note": f"JIT execution failed (warm iteration {idx}): {exc}",
            }
        run_ms = (time.perf_counter() - run_start) * 1000.0
        if idx >= warmup_count:
            runs_ms.append(run_ms)
            if capture_output:
                value = _extract_result_int(captured or "")
                if value is None:
                    return {
                        "status": "fail",
                        "jit_opt": jit_opt_level,
                        "compile_ms": compile_ms,
                        "runs_ms": runs_ms,
                        "checksum": None,
                        "note": "No integer output parsed in measured run.",
                    }
                outputs.append(value)

    if capture_output and outputs:
        if len(set(outputs)) > 1:
            return {
                "status": "fail",
                "jit_opt": jit_opt_level,
                "compile_ms": compile_ms,
                "runs_ms": runs_ms,
                "checksum": outputs[0],
                "note": f"Non-deterministic output ({outputs[:3]}...).",
            }
        return {
            "status": "ok",
            "jit_opt": jit_opt_level,
            "compile_ms": compile_ms,
            "runs_ms": runs_ms,
            "checksum": outputs[0],
            "note": None,
        }

    return {
        "status": "ok",
        "jit_opt": jit_opt_level,
        "compile_ms": compile_ms,
        "runs_ms": runs_ms,
        "checksum": None,
        "note": None,
    }


def jit_worker_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--source-file", required=True)
    parser.add_argument("--run-count", type=int, default=1)
    parser.add_argument("--warmup-count", type=int, default=0)
    parser.add_argument("--optimize", action="store_true")
    parser.add_argument("--jit-opt", type=int, default=DEFAULT_JIT_OPT_LEVEL)
    parser.add_argument("--profile", action="store_true")
    parser.add_argument("--flame-path", default="")
    parser.add_argument("--sample-hz", type=int, default=0)
    parser.add_argument("--capture-output", action="store_true")
    parser.add_argument("--jit-dump-ir", default="")
    raw_args = list(argv or sys.argv[1:])
    filtered_args = [
        a for a in raw_args if a not in {"__jit_worker__", "--_jit-worker"}
    ]
    parsed = parser.parse_args(filtered_args)

    result = _fast_jit_repeat_file_inprocess(
        parsed.source_file,
        run_count=max(1, int(parsed.run_count)),
        warmup_count=max(0, int(parsed.warmup_count)),
        optimize=bool(parsed.optimize),
        jit_opt=max(0, min(3, int(parsed.jit_opt))),
        profile=bool(parsed.profile),
        flame_path=str(parsed.flame_path or ""),
        sample_hz=max(0, int(parsed.sample_hz)),
        capture_output=bool(parsed.capture_output),
        dump_ir_path=str(parsed.jit_dump_ir or ""),
    )
    print(JIT_WORKER_MARKER + json.dumps(result))
    return 0 if result.get("status") == "ok" else 1


def fast_jit_file(
    filename: str,
    optimize: bool = True,
    jit_opt: Optional[int] = None,
    profile: bool = False,
    flame_path: str = "",
    sample_hz: int = 0,
    dump_ir_path: str = "",
) -> int:
    """
    Fast JIT compile and execute a .ail file

    This is the FAST path - no file I/O after parsing!

    Args:
        filename: Path to .ail source file
        optimize: Whether to optimize (default: True)
        profile: Enable function entry/exit instrumentation and print a
            blame-style report after the run (default: False).
        flame_path: When non-empty, write folded-stack data to this path.
        sample_hz: When >0, also start a background sampler at this rate.

    Returns:
        Exit code from main()
    """
    # Read source
    with open(filename, "r", encoding="utf-8") as f:
        source_code = f.read()

    print(f"=== Fast JIT Compiling {filename} ===\n")

    # Compile and execute with source file path for imports
    result = fast_jit_compile(
        source_code,
        optimize,
        jit_opt=jit_opt,
        source_file=os.path.abspath(filename),
        profile=profile,
        flame_path=flame_path,
        sample_hz=sample_hz,
        dump_ir_path=dump_ir_path,
    )

    print(f"\n=== Program exited with code: {result} ===")

    return result


def compile_to_ir_fast(
    source_code: str, source_file: str = "", debug: bool = True
) -> str:
    """
    Fast path to generate LLVM IR only (no execution)
    Used by AOT compilation path.

    debug=True (the default for AOT) emits DWARF metadata so the resulting
    binary can be debugged with gdb/lldb and profiled with `perf record`
    showing AILang function names. Sets debug_info_enabled, NOT
    profile_enabled — the latter would inject calls to Python-side profile
    thunks that don't exist in an AOT-linked binary, breaking the link.
    """
    tokens = tokenize(source_code)
    parser = Parser(tokens)
    ast = parser.parse_program()
    codegen = CodeGen()
    if debug:
        codegen.debug_info_enabled = True
    return codegen.generate(ast, source_file=source_file)
