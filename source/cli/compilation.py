"""CLI backend compilation helpers."""

from __future__ import annotations

import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from cli.cinclude_diagnostics import (
    collect_cinclude_include_dirs,
    emit_cinclude_backend_warning,
)
from cli.llvm_diagnostics import (
    _detect_llvm_link_flags,
    _first_nonempty_line,
    _format_llvm_failure_diagnostics,
)
from pgo.c_backend import c_pgo_compile_flags
from pgo.llvm_ir import (
    LLVMProfileMergeError,
    default_ailang_clang_target,
    llvm_pgo_generate_flags,
    llvm_pgo_use_flags_with_tool,
)
from pgo.llvm_toolchain import resolve_llvm_tool, same_llvm_root_tool
from pgo.paths import default_pgo_output_dir as _pgo_default_output_dir
from pgo.paths import sanitize_stem as _pgo_sanitize_stem
from pgo.paths import source_identity_tag as _pgo_source_identity_tag
from target_info import normalize_os_name, os_from_platform, target_matches

LLVM_OPT_TIMEOUT_SECONDS = 30
LLVM_CLANG_TIMEOUT_SECONDS = 120
LLVM_LLC_TIMEOUT_SECONDS = 120
LLVM_LINK_TIMEOUT_SECONDS = 120
CBACKEND_COMPILE_TIMEOUT_SECONDS = 60
REPO_ROOT = Path(__file__).resolve().parents[2]
GENERATED_ROOT = REPO_ROOT / "out" / "generated"
MINGW_TARGET_TRIPLE = "x86_64-w64-windows-gnu"


def _normalize_mingw_vararg_symbols(ir_code: str) -> str:
    """Match clang's MinGW lowering for libc vararg entry points."""
    if MINGW_TARGET_TRIPLE not in ir_code:
        return ir_code
    return ir_code.replace('@"printf"', '@"__mingw_printf"').replace(
        "@printf(", "@__mingw_printf("
    )


def _split_ailang_link_flags(raw_flags: str) -> list[str]:
    """Split one #link payload into subprocess-safe argv tokens."""
    raw_flags = (raw_flags or "").strip()
    if not raw_flags:
        return []
    try:
        flags = shlex.split(raw_flags, posix=True)
    except ValueError as exc:
        raise ValueError(f"invalid #link flags {raw_flags!r}: {exc}") from exc
    bad = [flag for flag in flags if "\x00" in flag or "\n" in flag or "\r" in flag]
    if bad:
        raise ValueError(f"invalid #link flag contains control character: {bad[0]!r}")
    return flags


def _split_targeted_link_payload(raw: str) -> tuple[str | None, str]:
    """Split optional target prefix from a #link payload."""
    payload = raw.strip()
    if not payload or payload[0] in {'"', "-"}:
        return None, payload
    parts = payload.split(None, 1)
    if len(parts) != 2:
        return None, payload
    target, remainder = parts
    if any(ch in target for ch in '/\\.:"<>'):
        return None, payload
    return normalize_os_name(target), remainder.strip()


def _extract_ailang_link_flags(text: str, *, target_os: str | None = None) -> list[str]:
    """Extract explicit AILang link flags from source/C/LLVM text.

    Supported forms:
      #link "-luser32 -lgdi32"
      #link windows "-luser32 -lgdi32"
      /* AILANG_LINK: -luser32 -lgdi32 */
      ; AILANG_LINK: -luser32 -lgdi32
    """
    flags: list[str] = []
    current_os = target_os or os_from_platform()
    for line in (text or "").splitlines():
        stripped = line.strip()
        raw = ""
        directive_target = None
        if stripped.startswith("#link"):
            raw = stripped[len("#link") :].strip()
            directive_target, raw = _split_targeted_link_payload(raw)
            if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
                raw = raw[1:-1]
        elif "AILANG_LINK:" in stripped:
            raw = stripped.split("AILANG_LINK:", 1)[1].strip()
            if "*/" in raw:
                raw = raw.split("*/", 1)[0].strip()
        if raw and target_matches(directive_target, current_os):
            flags.extend(_split_ailang_link_flags(raw))
    return flags


def _merge_link_flags(*groups: list[str]) -> list[str]:
    """Merge link flags preserving first occurrence order."""
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for flag in group:
            if flag in seen:
                continue
            merged.append(flag)
            seen.add(flag)
    return merged


def _normalize_native_toolchain(native_toolchain: str) -> str:
    """Normalize user-facing native toolchain aliases."""
    value = (native_toolchain or "auto").strip().lower().replace("_", "-")
    aliases = {
        "default": "auto",
        "llvm": "clang",
        "clang-llvm": "clang",
        "gnu": "gcc",
        "gcc-gnu": "gcc",
        "llc+gcc": "llc-gcc",
        "llc_gcc": "llc-gcc",
    }
    return aliases.get(value, value)


def _resolve_tool(name: str):
    """Resolve a toolchain executable to an absolute path via PATH lookup.



    Returns the absolute path string, or None if the tool isn't on PATH.

    Using shutil.which means subprocess.run sees a full path (no

    bandit B607 'partial executable path' warning) and we can probe

    availability without spawning a process.

    """
    return shutil.which(name)


def _default_clang_target() -> str | None:
    """Return a host-appropriate clang target override.

    On Windows, we intentionally use MinGW target triple for compatibility
    with GCC/MinGW style linking used elsewhere in this file. On non-Windows
    hosts, clang should default to the native platform triple.
    """
    return default_ailang_clang_target()


def _sanitize_stem(stem: str) -> str:
    """Return a filesystem-safe artifact stem."""
    return _pgo_sanitize_stem(stem)


def _source_identity_tag(source_file: str) -> str:
    """Stable short tag derived from absolute source path."""
    return _pgo_source_identity_tag(source_file)


def _intermediate_artifact_path(source_file: str, *, stage: str, suffix: str) -> Path:
    """Path for compiler-generated intermediates under out/generated/."""
    stem = _sanitize_stem(Path(source_file).stem)
    tag = _source_identity_tag(source_file)
    out_dir = GENERATED_ROOT / stage
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / f"{stem}_{tag}{suffix}"


def default_pgo_output_dir(source_file: str) -> Path:
    """Default profile-data directory for AOT PGO builds."""
    return _pgo_default_output_dir(source_file, GENERATED_ROOT)


def _summarize_compiler_failure(stderr: str, stdout: str) -> str:
    """Pick the actionable line from compiler output."""
    return (
        _first_nonempty_line(stderr)
        or _first_nonempty_line(stdout)
        or "compiler exited non-zero"
    )


def _pgo_compile_flags(
    *,
    pgo_generate_dir: str = "",
    pgo_use_dir: str = "",
) -> list[str]:
    """Return GCC/Clang PGO flags and create profile directories."""
    return c_pgo_compile_flags(
        pgo_generate_dir=pgo_generate_dir,
        pgo_use_dir=pgo_use_dir,
    )


def default_emit_llvm_output_path(source_file: str) -> Path:
    """Default destination for `--emit-llvm` output when -o is omitted."""
    return _intermediate_artifact_path(source_file, stage="emit_llvm", suffix=".ll")


def default_emit_c_output_path(source_file: str) -> Path:
    """Default destination for `--emit-c` output when -o is omitted."""
    return _intermediate_artifact_path(source_file, stage="emit_c", suffix=".c")


def compile_via_c(
    source_file: str,
    output_exe: str,
    opt_level: int | str = 3,
    profile_enabled: bool = False,
    pgo_generate_dir: str = "",
    pgo_use_dir: str = "",
    native_toolchain: str = "auto",
) -> bool:
    """Compile AILang source to native executable via C transpilation.



    This path often produces faster code than LLVM for certain workloads,

    especially when GCC's optimizer can do better inlining.

    """
    try:
        from transpiler.core import transpile_file as transpile_to_c
    except ImportError:
        print("Error: C transpiler not available")
        return False
    opt_label = f"-{opt_level}" if isinstance(opt_level, str) else f"-O{opt_level}"
    print(f"Compiling {source_file} via C backend with {opt_label}...")
    # Step 1: Transpile to C
    print("  [1/3] Transpiling to C...")
    c_file = _intermediate_artifact_path(source_file, stage="c_backend", suffix=".c")
    try:
        # Return value is the C source string, but transpile_to_c
        # already wrote it to disk  -  we read it back below to scan
        # for runtime feature markers.
        transpile_to_c(source_file, str(c_file), profile_enabled=profile_enabled)
        print(f"        Generated {c_file}")
    except (OSError, ValueError, RuntimeError) as e:
        print(f"Error: Transpilation failed: {e}")
        return False
    # Step 2: Compile with GCC or Clang
    print(f"  [2/3] Compiling with C compiler ({opt_label})...")
    # Detect runtime features by scanning the generated C for marker
    # includes. The C transpiler's per-feature emit functions only
    # emit their headers when the feature is actually used, so the
    # presence of a marker like <winsock2.h> means we must add the
    # corresponding link flags. The #pragma comment(lib,...) inside
    # the C is MSVC-only; MinGW/GCC needs explicit -l flags.
    auto_link_flags: list[str] = []
    try:
        with open(str(c_file), encoding="utf-8") as _cf:
            _c_src = _cf.read()
    except OSError:
        _c_src = ""
    try:
        explicit_link_flags = _extract_ailang_link_flags(_c_src)
    except ValueError as exc:
        print(f"Error: {exc}")
        return False
    if "winsock2.h" in _c_src and sys.platform.startswith("win"):
        auto_link_flags.append("-lws2_32")
    # SQLite: <sqlite3.h> is included by str_array_join_fn etc. when the
    # program uses sql_open/sql_exec. Auto-link -lsqlite3 in that case.
    if "sqlite3.h" in _c_src or "sqlite3_open" in _c_src:
        auto_link_flags.append("-lsqlite3")
    # Threading: pthread.h is included by the threading runtime when
    # spawn/join is used. Linux/macOS need -lpthread; Win32 API is
    # auto-linked by mingw/clang.
    if "pthread.h" in _c_src and not sys.platform.startswith("win"):
        auto_link_flags.append("-lpthread")
    link_flags = _merge_link_flags(explicit_link_flags, ["-lm"], auto_link_flags)
    include_dirs = collect_cinclude_include_dirs(source_file)
    # Try GCC first (often produces faster code for this workload).
    # Each entry is (display_name, full_path_or_None)  -  full paths
    # come from shutil.which so subprocess.run sees absolute,
    # validated executables (no bandit B607 partial-path warning).
    ccache_path = _resolve_tool("ccache")
    try:
        pgo_flags = _pgo_compile_flags(
            pgo_generate_dir=pgo_generate_dir,
            pgo_use_dir=pgo_use_dir,
        )
    except ValueError as exc:
        print(f"Error: {exc}")
        return False
    native_toolchain = _normalize_native_toolchain(native_toolchain)
    if native_toolchain == "auto":
        compiler_specs = [
            ("gcc", _resolve_tool("gcc")),
            ("clang", resolve_llvm_tool("clang")),
        ]
    elif native_toolchain == "gcc":
        compiler_specs = [("gcc", _resolve_tool("gcc"))]
    elif native_toolchain == "clang":
        compiler_specs = [("clang", resolve_llvm_tool("clang"))]
    else:
        print(
            "Error: --native-toolchain for --backend=c must be "
            "auto, gcc/gnu, or clang/llvm"
        )
        return False

    def _compiler_args(exe_path: str) -> tuple[list[str], str]:

        # AILang's emitted C uses C23 features: `nullptr`, `typeof` as a
        # standard, etc. Pass -std=c23 (gcc 14+, clang 18+) and fall back
        # to gnu23 if the strict mode rejects MinGW-specific extensions
        # like winsock. -fpermissive on g++ would help; for C the right
        # knob is -std=gnu23 which keeps C23 semantics + GNU extensions.
        optimization_flags = [
            f"-{opt_level}" if isinstance(opt_level, str) else f"-O{opt_level}"
        ]
        if isinstance(opt_level, str) and opt_level == "Os":
            optimization_flags.extend(["-fdata-sections", "-ffunction-sections"])
        cmd = [
            exe_path,
            "-std=gnu23",
            *optimization_flags,
            "-march=native",
            *pgo_flags,
            *(f"-I{include_dir}" for include_dir in include_dirs),
            str(c_file),
            "-o",
            output_exe,
            *link_flags,
        ]
        if isinstance(opt_level, str) and opt_level == "Os":
            cmd.insert(3, "-Wl,--gc-sections")
        if ccache_path and not pgo_flags:
            return [ccache_path, *cmd], f"ccache+{Path(exe_path).name}"
        return cmd, Path(exe_path).name

    from runtime.phases import Phase

    compiled = False
    compiler_failures: list[tuple[str, str]] = []
    for display_name, exe_path in compiler_specs:
        if exe_path is None:
            continue
        cmd, effective_compiler = _compiler_args(exe_path)
        try:
            with Phase(f"c_backend.{display_name}_compile"):
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=CBACKEND_COMPILE_TIMEOUT_SECONDS,
                    check=False,
                )
            if result.returncode == 0:
                print(f"        Created {output_exe} ({effective_compiler})")
                compiled = True
                break
            detail = _summarize_compiler_failure(result.stderr, result.stdout)
            compiler_failures.append((display_name, detail))
            print(f"        {display_name} failed: {detail[:120]}")
        except subprocess.TimeoutExpired:
            compiler_failures.append((display_name, "timed out"))
            print(f"        {display_name} timed out")
            continue
    if not compiled:
        if compiler_failures:
            print("Error: C backend compilation failed")
            for name, detail in compiler_failures:
                print(f"  {name}: {detail[:200]}")
        else:
            tried = ", ".join(name for name, _path in compiler_specs)
            print(f"Error: No C compiler found (tried {tried})")
        return False
    # Step 3: Success!
    print("  [3/3] Success!")
    print(f"\nCompiled executable: {output_exe}")
    print(f"Run with: ./{output_exe}")
    return True


def compile_to_native(
    source_file: str,
    output_exe: str,
    opt_level: int | str = 3,
    pgo_generate_dir: str = "",
    pgo_use_dir: str = "",
    llvm_pgo_generate_dir: str = "",
    llvm_pgo_use_dir: str = "",
    native_toolchain: str = "auto",
    debug_info: bool = False,
):
    """Compile AILang source to native executable with LLVM optimization"""
    opt_level_label = (
        f"-{opt_level}" if isinstance(opt_level, str) else f"-O{opt_level}"
    )
    # LLVM's CLI `opt` and `llc` accept numeric -O0..-O3. For size
    # optimization, keep -O2 there and let clang handle -Os when
    # available in the final native step.
    llc_opt_level = "-O2" if opt_level == "Os" else opt_level_label
    print(f"Compiling {source_file} to {output_exe} with {opt_level_label}...")
    emit_cinclude_backend_warning(source_file, "LLVM AOT")
    # Step 1: Generate LLVM IR via the fast path (the only IR path now  -
    # the legacy compile_to_ir was removed when its package-level
    # compile_ail_file wrapper was deleted). source_file is passed so
    # debug builds can record the actual source path. Release AOT keeps
    # debug metadata off by default so benchmarking and binary size match
    # C/C23 release builds instead of silently carrying DWARF sections.
    try:
        from codegen.fast_jit import compile_to_ir_fast
    except ImportError:
        print("Error: fast_jit module unavailable; cannot generate LLVM IR")
        return False
    print("  [1/4] Generating LLVM IR...")
    with open(source_file, "r", encoding="utf-8") as f:
        source_code = f.read()
    ir_code = compile_to_ir_fast(
        source_code,
        source_file=source_file,
        debug=debug_info,
    )
    # Fix target triple for MinGW compatibility
    ir_code = ir_code.replace("x86_64-pc-windows-msvc", "x86_64-w64-windows-gnu")
    ir_code = _normalize_mingw_vararg_symbols(ir_code)
    # Step 2: Skip AILang-side optimization. The requested -O level
    # (or -Os where supported) at step 4 reaches the same LLVM
    # optimization fixed point, so this path is redundant for AOT.
    # fixed point, so running our own PassBuilder here is redundant work.
    # Empirical check (perf_jit_driver/compare_preopt.py): no measurable
    # runtime difference on numeric or string-heavy workloads, and AOT
    # compile time drops by ~10-20%. The JIT path in fast_jit.py uses
    # its own inlined pass pipeline (LLJIT does codegen but no optimization
    # on its own) so this only affects AOT.
    print(
        f"  [2/4] Skipping AILang-side optimization "
        f"({opt_level_label} will handle it)"
    )
    # Save optimized IR to file
    ll_file = _intermediate_artifact_path(source_file, stage="llvm_aot", suffix=".ll")
    with open(ll_file, "w", encoding="utf-8") as f:
        f.write(ir_code)
    print(f"        Saved IR to {ll_file}")
    # Step 3 (optional): run external LLVM 'opt'. LLVM-family tools go
    # through the coherent resolver so Windows builds do not mix MSYS2
    # MinGW clang/profdata/compiler-rt with a generic LLVM installation.
    native_toolchain = _normalize_native_toolchain(native_toolchain)
    if native_toolchain == "gcc":
        native_toolchain = "llc-gcc"
    if native_toolchain not in {"auto", "clang", "llc-gcc"}:
        print(
            "Error: --native-toolchain for --backend=llvm must be "
            "auto, clang/llvm, or llc-gcc/gnu"
        )
        return False

    clang_path = (
        resolve_llvm_tool("clang") if native_toolchain in {"auto", "clang"} else None
    )
    llc_path = (
        resolve_llvm_tool("llc")
        if native_toolchain == "llc-gcc"
        else (
            same_llvm_root_tool(clang_path, "llc")
            if clang_path
            else resolve_llvm_tool("llc")
        )
    )
    opt_anchor = clang_path or llc_path
    opt_available = False
    optimized_ll = ll_file.with_name(ll_file.stem + "_opt.ll")
    opt_path = (
        same_llvm_root_tool(opt_anchor, "opt")
        if opt_anchor
        else resolve_llvm_tool("opt")
    )
    if opt_path:
        try:
            result = subprocess.run(
                [
                    opt_path,
                    opt_level_label,
                    "-S",
                    str(ll_file),
                    "-o",
                    str(optimized_ll),
                ],
                capture_output=True,
                text=True,
                timeout=LLVM_OPT_TIMEOUT_SECONDS,
                check=False,
            )
            if result.returncode == 0:
                print("  [3/4] Additional optimization with external opt...")
                ll_file = optimized_ll
                opt_available = True
        except subprocess.TimeoutExpired:
            pass  # External opt timed out; we already optimized internally
    # Step 4: Compile to native executable
    step_num = 4 if opt_available else 3
    total_steps = 4 if opt_available else 3
    print(f"  [{step_num}/{total_steps}] Compiling to native executable...")
    # Detect runtime features by scanning the IR. The LLVM emitter declares
    # external symbols like `@WSAStartup` only when the corresponding
    # builtin is used; finding one means we need to add the matching
    # link flag. Same approach as compile_via_c (which scans the .c for
    # <winsock2.h>)  -  keeps link-flag injection out of the IR generator.
    try:
        with open(str(ll_file), encoding="utf-8") as _llf:
            _ll_src = _llf.read()
    except OSError:
        _ll_src = ""
    try:
        explicit_link_flags = _merge_link_flags(
            _extract_ailang_link_flags(source_code),
            _extract_ailang_link_flags(_ll_src),
        )
    except ValueError as exc:
        print(f"Error: {exc}")
        return False
    auto_link_flags = _detect_llvm_link_flags(_ll_src)
    link_flags = _merge_link_flags(explicit_link_flags, ["-lm"], auto_link_flags)
    if pgo_generate_dir or pgo_use_dir:
        print(
            "Error: --pgo-generate/--pgo-use are C-backend PGO flags. "
            "Use --backend=c, or use --llvm-pgo-generate/--llvm-pgo-use "
            "for hosted LLVM IR PGO."
        )
        return False
    if llvm_pgo_generate_dir and llvm_pgo_use_dir:
        print("Error: --llvm-pgo-generate and --llvm-pgo-use are mutually exclusive")
        return False
    if (llvm_pgo_generate_dir or llvm_pgo_use_dir) and not clang_path:
        print(
            "Error: hosted LLVM IR PGO requires the clang native toolchain. "
            "Use --native-toolchain=clang or leave --native-toolchain=auto "
            "when clang is available."
        )
        return False
    try:
        if llvm_pgo_generate_dir:
            pgo_flags = llvm_pgo_generate_flags(llvm_pgo_generate_dir)
        elif llvm_pgo_use_dir:
            profdata_tool = (
                same_llvm_root_tool(clang_path, "llvm-profdata")
                if clang_path
                else resolve_llvm_tool("llvm-profdata")
            )
            pgo_flags = llvm_pgo_use_flags_with_tool(
                llvm_pgo_use_dir,
                llvm_profdata=profdata_tool,
            )
        else:
            pgo_flags = []
    except (OSError, ValueError, LLVMProfileMergeError) as exc:
        print(f"Error: LLVM PGO setup failed: {exc}")
        return False
    # Try clang first (pure LLVM toolchain), fall back to llc+gcc if unavailable
    clang_available = False
    clang_failed = False
    clang_failure_details = ""
    clang_timed_out = False
    if clang_path:
        clang_cmd = [
            clang_path,
            opt_level_label,
            "-march=native",
            *pgo_flags,
            str(ll_file),
            "-o",
            output_exe,
            *link_flags,
        ]
        target = _default_clang_target()
        if target is not None:
            clang_cmd[1:1] = ["-target", target]
        try:
            result = subprocess.run(
                clang_cmd,
                capture_output=True,
                text=True,
                timeout=LLVM_CLANG_TIMEOUT_SECONDS,
                check=False,
            )
            if result.returncode == 0:
                print(f"        Created {output_exe} (clang)")
                clang_available = True
            else:
                clang_failed = True
                clang_failure_details = _format_llvm_failure_diagnostics(
                    "clang",
                    stderr=result.stderr,
                    stdout=result.stdout,
                    link_flags=link_flags,
                )
                print(f"Warning: {clang_failure_details}")
                print("Warning: clang failed, using llc+gcc fallback")
        except subprocess.TimeoutExpired:
            clang_timed_out = True
            print("Warning: clang timed out, using llc+gcc fallback")
    else:
        if native_toolchain != "llc-gcc":
            print("Info: clang not found, using llc+gcc fallback")
    # Fallback: Use old llc + gcc/gcc method
    if not clang_available:
        if native_toolchain == "clang":
            if clang_failed:
                print(
                    f"Error: LLVM AOT clang toolchain failed: {clang_failure_details}"
                )
            elif clang_timed_out:
                print("Error: LLVM AOT clang toolchain timed out")
            else:
                print("Error: clang not found for --native-toolchain=clang")
            return False
        if pgo_flags:
            print(
                "Error: LLVM AOT PGO requires clang. "
                "The llc+gcc fallback cannot consume this profile flow."
            )
            return False
        print(f"  [2/4] Compiling with LLVM llc {llc_opt_level}...")
        obj_file = ll_file.with_suffix(".o")
        if llc_path is None:
            if clang_failed:
                print(
                    "Error: LLVM AOT failed: clang failed and llc fallback is unavailable "
                    "(llc not found)."
                )
                print(clang_failure_details)
            elif clang_timed_out:
                print(
                    "Error: LLVM AOT failed: clang timed out and llc fallback is unavailable "
                    "(llc not found)."
                )
            else:
                print(
                    "Error: Neither 'clang' nor 'llc' found. Please install LLVM toolchain."
                )
            return False
        # Use -mcpu=native to enable all CPU features (AVX, AVX2, AVX-512, etc.)
        # Add function-sections and data-sections for better dead code elimination.
        try:
            result = subprocess.run(
                [
                    llc_path,
                    llc_opt_level,
                    "-mcpu=native",
                    "-function-sections",
                    "-data-sections",
                    "-relocation-model=pic",
                    "-filetype=obj",
                    str(ll_file),
                    "-o",
                    str(obj_file),
                ],
                capture_output=True,
                text=True,
                timeout=LLVM_LLC_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired:
            print("Error: llc timed out while compiling LLVM IR")
            return False
        if result.returncode != 0:
            llc_failure = _format_llvm_failure_diagnostics(
                "llc",
                stderr=result.stderr,
                stdout=result.stdout,
                link_flags=link_flags,
            )
            print(f"Error: {llc_failure}")
            return False
        print(f"        Generated {obj_file}")
        # Link with gcc
        print("  [3/4] Linking executable...")
        gcc_path = _resolve_tool("gcc")
        if gcc_path is None:
            print("Error: 'gcc' not found. Please install gcc.")
            return False
        # On Windows/MinGW, LLVM generates calls to __chkstk for large stack frames
        # but MinGW provides ___chkstk_ms (different naming convention)
        # We provide our own chkstk.o stub to bridge this gap
        chkstk_obj = Path(__file__).parent / "ailang" / "chkstk.o"
        # Use -Wl,--gc-sections for dead code elimination (matches -function-sections)
        link_cmd = [
            gcc_path,
            "-O3",
            "-Wl,--gc-sections",
            str(obj_file),
            "-o",
            output_exe,
            *link_flags,
        ]
        if chkstk_obj.exists():
            link_cmd.insert(2, str(chkstk_obj))  # Add chkstk.o before output
        try:
            result = subprocess.run(
                link_cmd,
                capture_output=True,
                text=True,
                timeout=LLVM_LINK_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired:
            print("Error: linker timed out while producing native executable")
            return False
        if result.returncode == 0:
            print(f"        Created {output_exe} (linked with gcc)")
        else:
            link_failure = _format_llvm_failure_diagnostics(
                "linker (gcc)",
                stderr=result.stderr,
                stdout=result.stdout,
                link_flags=link_flags,
            )
            print(f"Error: {link_failure}")
            return False
    # Step 4: Success!
    final_step = 4 if opt_available else 3
    print(f"  [{final_step}/{final_step}] Success!")
    print(f"\nCompiled executable: {output_exe}")
    print(f"Run with: ./{output_exe}")
    return True
