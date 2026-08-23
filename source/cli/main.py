#!/usr/bin/env python3
"""
AILang Compiler - entry-point launcher.

Adds source/ to sys.path so flat-package imports (parser, lexer, transpiler,
codegen, diagnostics, runtime, compiler, tools) resolve, then runs the CLI.

Compilation Flow:
  1. Prepass (diagnostics) - catches errors/warnings early
  2. If clean, proceed to compilation (JIT or AOT)
  3. If errors, stop and show them

Compilation Modes:
  --mode=hosted      (default) Full libc, file I/O, SQLite
  --mode=freestanding         No libc, bare metal / kernel / EFI

Backends:
  --backend=llvm     LLVM IR -> native (default)
  --backend=c        Transpile to C -> GCC/Clang (often 20% faster!)

Native toolchains:
  --native-toolchain=auto     Backend-specific default
  --native-toolchain=clang    clang/LLVM native driver
  --native-toolchain=gcc      GCC for C backend
  --native-toolchain=llc-gcc  LLVM IR -> llc object -> GCC link
  --debug-info                Keep DWARF metadata in LLVM native output

Diagnostics:
  --check            Run diagnostics only (no compilation)
  --fix              Auto-fix trivial issues
  --no-prepass       Skip prepass diagnostics (faster, less safe)
  -W                 Treat warnings as errors
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import json

from cli.cinclude_diagnostics import emit_cinclude_backend_warning
from cli.emit_c_output import maybe_emit_c_source

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

from cli import commands as _commands
from cli.pgo_options import (
    parse_pgo_cli_options,
    run_llvm_pgo_probe_cli,
    validate_pgo_cli_options,
    wants_llvm_pgo_probe,
)
from runtime.modes import CompilationContext, CompilationMode
from runtime.phases import enable_profiling, get_profile
from runtime.unsafe_registry import UnsafeMode, UnsafeRegistry, set_registry

# Heavy imports (codegen, fast_jit, transpiler_c, llvmlite) are deferred
# to point-of-use. codegen and fast_jit transitively load llvmlite, which
# triggers a native LLVM DLL load (~30 ms) and llvmlite Python init
# (~20 ms). Paths that don't need them -- --check, --analyze, --fix,
# --check-json, --backend=c, --list-builtins -- save ~50 ms of fixed
# cost per invocation. JIT and AOT(LLVM) pay the same cost as before,
# just on first use instead of import time.
DIAGNOSTICS_AVAILABLE = _commands.DIAGNOSTICS_AVAILABLE
_resolve_tool = _commands._resolve_tool
run_prepass = _commands.run_prepass
run_diagnostics = _commands.run_diagnostics
run_diagnostics_on_error = _commands.run_diagnostics_on_error
run_diagnostics_json = _commands.run_diagnostics_json
compile_via_c = _commands.compile_via_c
compile_to_native = _commands.compile_to_native
default_emit_llvm_output_path = _commands.default_emit_llvm_output_path
default_pgo_output_dir = _commands.default_pgo_output_dir
report_checks = _commands.report_checks
report_ffi = _commands.report_ffi
report_format = _commands.report_format
report_optimizer = _commands.report_optimizer
report_effect_policy = _commands.report_effect_policy
report_runtime_needs = _commands.report_runtime_needs
run_static_analysis = _commands.run_static_analysis
run_effect_policy_gate = _commands.run_effect_policy_gate
_print_builtins = _commands._print_builtins
DEFAULT_VERSION = "1.8.0"


def _read_project_version() -> str:
    """Read canonical version, falling back to DEFAULT_VERSION."""
    try:
        import version as _version

        raw = getattr(_version, "__version__", "")
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    except ImportError:
        pass

    # Fallback for source-only trees where version.py might be unavailable.
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        project = data.get("project", {})
        version = project.get("version")
        if isinstance(version, str) and version.strip():
            return version.strip()
    except (OSError, tomllib.TOMLDecodeError):
        pass
    return DEFAULT_VERSION


def main():
    """Main entry point"""
    if "__jit_worker__" in sys.argv or "--_jit-worker" in sys.argv:
        from codegen.fast_jit import jit_worker_cli

        sys.exit(jit_worker_cli(sys.argv[1:]))
    # --profile-phases: enable compile-pipeline phase timing. Distinct from
    # --profile (which instruments the user's *runtime* code). The report
    # prints at process exit via atexit so it's captured regardless of
    # which compile path runs.
    if "--profile-phases" in sys.argv:
        enable_profiling()
        import atexit

        def _print_phase_report() -> None:
            prof = get_profile()
            if prof is not None:
                print()
                print(prof.report())

        atexit.register(_print_phase_report)
    if "--version" in sys.argv:
        print(f"AILang Compiler v{_read_project_version()}")
        sys.exit(0)
    if wants_llvm_pgo_probe(sys.argv):
        sys.exit(run_llvm_pgo_probe_cli(sys.argv))
    show_help = "--help" in sys.argv or "-h" in sys.argv
    if len(sys.argv) < 2 or show_help:

        print(f"AILang Compiler v{_read_project_version()} - JIT and AOT Compilation")
        print()
        print("Usage:")
        print(
            "  python ailang.py <file.ail>                    - JIT compile and execute"
        )
        print(
            "  python ailang.py <file.ail> -o <exe>           - AOT compile to executable (-O3)"
        )
        print(
            "  python ailang.py <file.ail> -o <exe> -O<0-3>   - AOT with optimization level"
        )
        print(
            "  python ailang.py <file.ail> --backend=c -o out - Transpile to C, then compile"
        )
        print(
            "  python ailang.py <file.ail> --emit-c -o out.c   - Emit generated C source only"
        )
        print("  python ailang.py <file.ail> --check            - Run diagnostics only")
        print()
        print("Examples:")
        print("  python ailang.py factorial.ail                 # JIT mode")
        print("  python ailang.py fibonacci.ail -o fib.exe      # AOT with -O3")
        print("  python ailang.py program.ail -o prog.exe -O2   # AOT with -O2")
        print("  python ailang.py kernel.ail -o kernel --mode=freestanding")
        print(
            "  python ailang.py bench.ail --backend=c -o bench # Transpile to C (fastest)"
        )
        print("  python ailang.py lib.ail --emit-c -o lib.c      # Generate C source")
        print(
            "  python ailang.py app.ail --backend=llvm --native-toolchain=clang -o app"
        )
        print("  python ailang.py app.ail --backend=c --native-toolchain=gcc -o app")
        print("  python ailang.py code.ail --check              # Check for errors")
        print(
            "  python ailang.py code.ail --report-checks      # Explain inserted/elided safety checks"
        )
        print(
            "  python ailang.py code.ail --format-report      # Explain direct writer vs formatter fallback"
        )
        print(
            "  python ailang.py code.ail --optimizer-report   # Explain scalarization/materialization decisions"
        )
        print(
            "  python ailang.py code.ail --runtime-needs      # Show runtime helper families and C size"
        )
        print(
            "  python ailang.py code.ail --ffi-report         # Show C/FFI includes, links, externs"
        )
        print(
            "  python ailang.py code.ail --effect-policy      # Show effect/capability policy violations"
        )
        print(
            "  python ailang.py code.ail --fix               # Auto-fix trivial issues"
        )
        print(
            "  python ailang.py code.ail --analyze           # Static analysis + race detection"
        )
        print()
        print("Backends:")
        print("  --backend=llvm  LLVM IR -> native (default)")
        print("  --backend=c     Transpile to C -> GCC/Clang (often faster!)")
        print("  --backend-llvm  Alias for --backend=llvm --native-toolchain=clang")
        print("  --backend-gnu   Alias for --backend=c --native-toolchain=gcc")
        print()
        print("Native toolchains:")
        print("  --native-toolchain=auto     Default: backend-specific best path")
        print("  --native-toolchain=clang    Use clang/LLVM native driver")
        print("  --native-toolchain=gcc      Use GCC for C backend")
        print("  --native-toolchain=llc-gcc  LLVM IR -> llc object -> gcc link")
        print("  --debug-info                Keep DWARF metadata in LLVM native output")
        print()
        print("Diagnostics:")
        print("  --check         Run diagnostics only (no compilation)")
        print(
            "  --fix           Auto-fix trivial issues (True->true, elif->elsif, etc)"
        )
        print("  --analyze       Static analysis (null flow + data race detection)")
        print(
            "  --report-checks Emit per-expression check decisions (overflow check explain mode)"
        )
        print("  --report-checks-json Same as --report-checks but JSON output")
        print(
            "  --format-report Emit formatting specialization decisions (direct vs fallback)"
        )
        print("  --format-report-json Same as --format-report but JSON output")
        print(
            "  --optimizer-report Emit scalarization/materialization optimizer decisions"
        )
        print("  --optimizer-report-json Same as --optimizer-report but JSON output")
        print(
            "  --runtime-needs Emit runtime helper family report (C backend planning)"
        )
        print("  --runtime-needs-json Same as --runtime-needs but JSON output")
        print("  --ffi-report    Emit C/FFI surface report")
        print("  --ffi-report-json Same as --ffi-report but JSON output")
        print(
            "  --emit-header   Generate a C header for records/unions/@export functions"
        )
        print("  --emit-abi-headers Generate C headers from abi header blocks")
        print("  --emit-cabi-headers Compatibility alias for --emit-abi-headers")
        print("  --emit-c        Generate C source without compiling/linking")
        print("  --effect-policy Emit effect/capability policy report")
        print("  --effect-policy-json Same as --effect-policy but JSON output")
        print("  --diagnose      Show hints on error (default)")
        print("  --no-diagnose   Disable hints on error")
        print()
        print("Compilation modes:")
        print("  --mode=hosted       Full stdlib: libc, file I/O, SQLite (default)")
        print("  --mode=freestanding No libc, bare metal / kernel / EFI mode")
        print()
        print("Unsafe operations:")
        print("  (default)           Ask for confirmation on each 'unsafe' keyword")
        print("  --assume-unsafe     Auto-approve all unsafe operations")
        print("  --deny-unsafe       Reject all unsafe operations (for auditing)")
        print()
        print("Optimization levels:")
        print("  -O0: No optimization (fastest compile)")
        print("  -O1: Basic optimization")
        print("  -O2: Standard optimization (good balance)")
        print("  -O3: Aggressive optimization (best performance)")
        print("  -Os: Size optimization (smaller output, where supported)")
        print("  --jit-opt=0..3: Select JIT LLVM optimization pipeline level")
        print("  --jit-dump-ir=PATH: Write optimized JIT IR for AOT/JIT comparison")
        print("  --pgo-generate[=DIR]: C backend PGO instrumentation build")
        print("  --pgo-use[=DIR]: C backend PGO optimized build")
        print("  --llvm-pgo-generate[=DIR]: Hosted LLVM IR PGO instrumentation build")
        print("  --llvm-pgo-use[=DIR]: Hosted LLVM IR PGO optimized build")
        print()
        print("Information:")
        print("  --list-builtins     Show all built-in functions and keywords")
        print("  --version           Show version information")
        print("  --llvm-pgo-probe    Probe hosted LLVM IR PGO toolchain support")
        sys.exit(0 if show_help else 1)
    # Handle --list-builtins
    if "--list-builtins" in sys.argv:

        _print_builtins()
        sys.exit(0)
    # Find source file (first argument that doesn't start with -)
    report_checks_mode = "--report-checks" in sys.argv
    report_checks_json = "--report-checks-json" in sys.argv
    report_format_mode = "--format-report" in sys.argv
    report_format_json = "--format-report-json" in sys.argv
    report_optimizer_mode = "--optimizer-report" in sys.argv
    report_optimizer_json = "--optimizer-report-json" in sys.argv
    runtime_needs_mode = "--runtime-needs" in sys.argv
    runtime_needs_json = "--runtime-needs-json" in sys.argv
    ffi_report_mode = "--ffi-report" in sys.argv
    ffi_report_json = "--ffi-report-json" in sys.argv
    effect_policy_mode = "--effect-policy" in sys.argv
    effect_policy_json = "--effect-policy-json" in sys.argv

    source_file = None
    for arg in sys.argv[1:]:

        if not arg.startswith("-") and arg.endswith(".ail"):

            source_file = arg
            break
    if source_file is None:

        print("Error: No .ail source file specified")
        sys.exit(1)
    if not Path(source_file).exists():

        print(f"Error: File not found: {source_file}")
        sys.exit(1)
    # Check for compilation mode
    for arg in sys.argv:

        if arg.startswith("--mode="):

            mode_name = arg.split("=")[1]
            if mode_name == "freestanding":

                CompilationContext.set_mode(CompilationMode.FREESTANDING)
                print("Mode: freestanding (no libc)")
            elif mode_name == "hosted":

                CompilationContext.set_mode(CompilationMode.HOSTED)
            else:

                print(
                    f"Error: Unknown mode '{mode_name}'. Use 'hosted' or 'freestanding'."
                )
                sys.exit(1)
    # Configure unsafe operation handling
    unsafe_mode = UnsafeMode.INTERACTIVE  # Default: ask user
    if "--assume-unsafe" in sys.argv:

        unsafe_mode = UnsafeMode.ASSUME_YES
    elif "--deny-unsafe" in sys.argv:

        unsafe_mode = UnsafeMode.DENY_ALL
    elif CompilationContext.get_mode() == CompilationMode.FREESTANDING:

        # Freestanding mode implies unsafe is expected (kernel development)
        unsafe_mode = UnsafeMode.FREESTANDING
    set_registry(UnsafeRegistry(mode=unsafe_mode))
    # Check for --emit-llvm option (save LLVM IR to file)
    if "--emit-llvm" in sys.argv:
        # Lazy parser/lexer import: only needed for direct LLVM IR emission.
        from parser.parser import Parser

        # Lazy: codegen drags in llvmlite; only load when --emit-llvm is selected.
        from codegen.codegen import CodeGen
        from lexer.scan import tokenize

        output_ll = None
        for i, arg in enumerate(sys.argv):

            if arg == "-o" and i + 1 < len(sys.argv):

                output_ll = sys.argv[i + 1]
                break
        if not output_ll:
            output_ll = str(default_emit_llvm_output_path(source_file))
        # Generate LLVM IR
        with open(source_file, "r", encoding="utf-8") as f:

            source_code = f.read()
        tokens = tokenize(source_code)
        parser = Parser(tokens)
        ast = parser.parse_program()
        emit_cinclude_backend_warning(source_file, "LLVM IR emission")
        codegen = CodeGen()
        # --emit-llvm always emits DWARF (cheap, useful for inspection).
        # --profile additionally enables runtime instrumentation.
        codegen.debug_info_enabled = True
        codegen.profile_enabled = "--profile" in sys.argv
        codegen.generate(ast, source_file=source_file)
        with open(output_ll, "w", encoding="utf-8") as f:

            f.write(str(codegen.module))
        print(f"LLVM IR written to: {output_ll}")
        sys.exit(0)
    emit_c_exit = maybe_emit_c_source(sys.argv, source_file)
    if emit_c_exit is not None:
        sys.exit(emit_c_exit)
    if "--emit-abi-headers" in sys.argv or "--emit-cabi-headers" in sys.argv:
        from cli.header_generation import write_cabi_headers

        output_dir = None
        for i, arg in enumerate(sys.argv):
            if arg == "-o" and i + 1 < len(sys.argv):
                output_dir = sys.argv[i + 1]
                break
        ok = write_cabi_headers(source_file, output_dir or "out/generated/abi")
        sys.exit(0 if ok else 1)
    if "--emit-header" in sys.argv:
        from cli.header_generation import default_header_output_path, write_c_header

        output_header = None
        for i, arg in enumerate(sys.argv):

            if arg == "-o" and i + 1 < len(sys.argv):

                output_header = sys.argv[i + 1]
                break
        if not output_header:
            output_header = str(default_header_output_path(source_file))
        ok = write_c_header(source_file, output_header)
        sys.exit(0 if ok else 1)
    # Check for backend option
    use_c_backend = False
    native_toolchain = "auto"
    skip_prepass = False
    warnings_as_errors = False
    i = 0
    while i < len(sys.argv):
        arg = sys.argv[i]

        if arg == "--backend=c":

            use_c_backend = True
        elif arg == "--backend=llvm":

            use_c_backend = False
        elif arg == "--backend-gnu":

            use_c_backend = True
            native_toolchain = "gcc"
        elif arg == "--backend-llvm":

            use_c_backend = False
            native_toolchain = "clang"
        elif arg.startswith("--native-toolchain=") or arg.startswith("--toolchain="):

            native_toolchain = arg.split("=", 1)[1]
        elif arg in {"--native-toolchain", "--toolchain"}:

            if i + 1 >= len(sys.argv):

                print(f"Error: Missing value after {arg}")
                sys.exit(1)
            native_toolchain = sys.argv[i + 1]
            i += 1
        elif arg == "--no-prepass":

            skip_prepass = True
        elif arg == "-W":

            warnings_as_errors = True
        i += 1
    if report_checks_json:
        # Keep JSON output machine-parseable by skipping prepass chatter.
        skip_prepass = True
    if report_format_json:
        # Keep JSON output machine-parseable by skipping prepass chatter.
        skip_prepass = True
    if report_optimizer_json:
        # Keep JSON output machine-parseable by skipping prepass chatter.
        skip_prepass = True
    if runtime_needs_json:
        # Keep JSON output machine-parseable by skipping prepass chatter.
        skip_prepass = True
    if ffi_report_json:
        # Keep JSON output machine-parseable by skipping prepass chatter.
        skip_prepass = True
    if effect_policy_json:
        # Keep JSON output machine-parseable by skipping prepass chatter.
        skip_prepass = True
    # Runtime profiling is opt-in because it adds noticeable overhead.
    profile_enabled = "--profile" in sys.argv
    # Release AOT omits DWARF by default to match C/C23 benchmark baselines.
    debug_info_enabled = "--debug-info" in sys.argv
    # --profile-flame=path also writes folded-stack data for flamegraph.pl.
    flame_path = ""
    for arg in sys.argv:
        if arg.startswith("--profile-flame="):

            flame_path = arg.split("=", 1)[1]
            profile_enabled = True
            break
    # --profile-sample[=Hz]: spawn a background sampler at the given rate.
    # Default is 1000 Hz (1 ms interval); --profile-sample=500 for slower.
    # Implies --profile.
    sample_hz = 0
    for arg in sys.argv:

        if arg == "--profile-sample":

            sample_hz = 1000
            profile_enabled = True
            break
        if arg.startswith("--profile-sample="):

            try:

                sample_hz = int(arg.split("=", 1)[1])
            except ValueError:

                sample_hz = 1000
            profile_enabled = True
            break
    # JIT benchmark mode flags (used by benchmarks/run_benchmarks.py)
    jit_repeat_count = 1
    jit_warmup_count = 0
    jit_capture_output = False
    jit_emit_json = False
    jit_opt_level = 3
    jit_dump_ir_path = ""
    i = 0
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg.startswith("--jit-repeat="):
            try:
                jit_repeat_count = max(1, int(arg.split("=", 1)[1]))
            except ValueError:
                jit_repeat_count = 1
            i += 1
            continue
        if arg == "--jit-repeat" and i + 1 < len(sys.argv):
            try:
                jit_repeat_count = max(1, int(sys.argv[i + 1]))
            except ValueError:
                jit_repeat_count = 1
            i += 2
            continue
        elif arg.startswith("--jit-warmup="):
            try:
                jit_warmup_count = max(0, int(arg.split("=", 1)[1]))
            except ValueError:
                jit_warmup_count = 0
            i += 1
            continue
        elif arg == "--jit-warmup" and i + 1 < len(sys.argv):
            try:
                jit_warmup_count = max(0, int(sys.argv[i + 1]))
            except ValueError:
                jit_warmup_count = 0
            i += 2
            continue
        elif arg == "--jit-capture-output":
            jit_capture_output = True
            i += 1
            continue
        elif arg == "--jit-json":
            jit_emit_json = True
            i += 1
            continue
        elif arg.startswith("--jit-opt="):
            try:
                jit_opt_level = max(0, min(3, int(arg.split("=", 1)[1])))
            except ValueError:
                jit_opt_level = 3
            i += 1
            continue
        elif arg == "--jit-opt" and i + 1 < len(sys.argv):
            try:
                jit_opt_level = max(0, min(3, int(sys.argv[i + 1])))
            except ValueError:
                jit_opt_level = 3
            i += 2
            continue
        elif arg.startswith("--jit-dump-ir="):
            jit_dump_ir_path = arg.split("=", 1)[1]
            i += 1
            continue
        elif arg == "--jit-dump-ir" and i + 1 < len(sys.argv):
            jit_dump_ir_path = sys.argv[i + 1]
            i += 2
            continue
        i += 1
    # JSON diagnostics mode (for editor integration)
    if "--check-json" in sys.argv:

        diagnostics_json = run_diagnostics_json(source_file)
        print(json.dumps(diagnostics_json))
        has_errors = any(
            d["severity"] == "error" for d in diagnostics_json.get("diagnostics", [])
        )
        sys.exit(0 if not has_errors else 1)
    # Check for diagnostics-only mode
    if "--check" in sys.argv:

        issues = run_diagnostics(source_file, fix_mode=False)
        sys.exit(0 if issues == 0 else 1)
    # Check for auto-fix mode
    if "--fix" in sys.argv:

        issues = run_diagnostics(source_file, fix_mode=True)
        sys.exit(0 if issues == 0 else 1)
    # Check for static analysis mode
    if "--analyze" in sys.argv:

        exit_code = run_static_analysis(source_file, warnings_as_errors)
        sys.exit(exit_code)
    # === PREPASS: Run diagnostics before compilation ===
    if not skip_prepass:

        if not run_prepass(source_file, warnings_as_errors):

            sys.exit(1)  # Errors found, stop
    if report_checks_mode or report_checks_json:
        ok = report_checks(source_file, as_json=report_checks_json)
        sys.exit(0 if ok else 1)
    if report_format_mode or report_format_json:
        ok = report_format(source_file, as_json=report_format_json)
        sys.exit(0 if ok else 1)
    if report_optimizer_mode or report_optimizer_json:
        ok = report_optimizer(source_file, as_json=report_optimizer_json)
        sys.exit(0 if ok else 1)
    if runtime_needs_mode or runtime_needs_json:
        ok = report_runtime_needs(source_file, as_json=runtime_needs_json)
        sys.exit(0 if ok else 1)
    if ffi_report_mode or ffi_report_json:
        ok = report_ffi(source_file, as_json=ffi_report_json)
        sys.exit(0 if ok else 1)
    if effect_policy_mode or effect_policy_json:
        ok = report_effect_policy(source_file, as_json=effect_policy_json)
        sys.exit(0 if ok else 1)
    if skip_prepass:
        # Keep effect/mode policy gating enabled when prepass is disabled.
        if not run_effect_policy_gate(source_file):
            sys.exit(1)
    # Check for AOT compilation mode
    if "-o" in sys.argv:

        output_idx = sys.argv.index("-o") + 1
        if output_idx >= len(sys.argv):

            print("Error: Missing output filename after -o")
            sys.exit(1)
        output_exe = sys.argv[output_idx]
        # Check for optimization level
        opt_level = 3  # Default to -O3
        if "-O0" in sys.argv:

            opt_level = 0
        elif "-O1" in sys.argv:

            opt_level = 1
        elif "-O2" in sys.argv:

            opt_level = 2
        elif "-O3" in sys.argv:

            opt_level = 3
        elif "-Os" in sys.argv:
            opt_level = "Os"
        pgo_options = parse_pgo_cli_options(
            sys.argv,
            source_file=source_file,
            default_dir=default_pgo_output_dir,
        )
        pgo_error = validate_pgo_cli_options(pgo_options, use_c_backend=use_c_backend)
        if pgo_error:
            print(f"Error: {pgo_error}")
            sys.exit(1)
        # Use C backend or LLVM backend
        if use_c_backend:

            success = compile_via_c(
                source_file,
                output_exe,
                opt_level,
                profile_enabled=profile_enabled,
                pgo_generate_dir=pgo_options.c_generate_dir,
                pgo_use_dir=pgo_options.c_use_dir,
                native_toolchain=native_toolchain,
            )
        else:

            # Compile to native executable via LLVM
            success = compile_to_native(
                source_file,
                output_exe,
                opt_level,
                pgo_generate_dir=pgo_options.c_generate_dir,
                pgo_use_dir=pgo_options.c_use_dir,
                llvm_pgo_generate_dir=pgo_options.llvm_generate_dir,
                llvm_pgo_use_dir=pgo_options.llvm_use_dir,
                native_toolchain=native_toolchain,
                debug_info=debug_info_enabled or profile_enabled,
            )
        sys.exit(0 if success else 1)
    else:

        # JIT mode - fast_jit is the only supported path. The legacy
        # compile_ail_file branch was removed when its package-level
        # function was deleted. Lazy import: fast_jit drags in llvmlite
        # which loads native LLVM (~30ms); avoid that for non-JIT paths.
        try:
            emit_cinclude_backend_warning(source_file, "JIT")
            if (
                jit_repeat_count > 1
                or jit_warmup_count > 0
                or jit_emit_json
                or jit_capture_output
            ):
                from codegen.fast_jit import fast_jit_repeat_file
            else:
                from codegen.fast_jit import fast_jit_file
        except ImportError:

            print("Error: fast_jit module unavailable; JIT mode is unsupported")
            sys.exit(1)
        print("--- FAST JIT Execution (LLVM ORC) ---")
        try:

            if (
                jit_repeat_count > 1
                or jit_warmup_count > 0
                or jit_emit_json
                or jit_capture_output
            ):
                result = fast_jit_repeat_file(
                    source_file,
                    run_count=jit_repeat_count,
                    warmup_count=jit_warmup_count,
                    optimize=True,
                    jit_opt=jit_opt_level,
                    profile=profile_enabled,
                    flame_path=flame_path,
                    sample_hz=sample_hz,
                    capture_output=jit_capture_output,
                    subprocess_mode=jit_emit_json,
                    dump_ir_path=jit_dump_ir_path,
                )
                if jit_emit_json:
                    print(f"JIT_WARM_RESULT={json.dumps(result)}")
                    if result.get("status") != "ok":
                        sys.exit(1)
                elif result["status"] != "ok":
                    if result.get("note"):
                        print(f"JIT repeat failed: {result['note']}")
                    sys.exit(1)
            else:
                fast_jit_file(
                    source_file,
                    optimize=True,
                    jit_opt=jit_opt_level,
                    profile=profile_enabled,
                    flame_path=flame_path,
                    sample_hz=sample_hz,
                    dump_ir_path=jit_dump_ir_path,
                )
            # fast_jit_file already prints the inner program's exit code.
            sys.exit(0)
        except (OSError, RuntimeError, ValueError) as e:

            print(f"Error: {e}")
            sys.exit(1)


if __name__ == "__main__":

    main()
