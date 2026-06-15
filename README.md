# AILang

Active direction: Python-hosted AILang compiler with explicit backends and
strict verification gates. The previous self-hosted AILang-in-AILang work is
kept under `archived/` for history/reference (excluded from clean release
packages by default).

Current public line: AILang `1.8.0`. Treat `1.8.x` as the final release line
for now: future work should land as maintenance, validation, optimization,
integration, or bug-fix updates unless a real product requirement proves that a
new numbered release line is necessary.

## Repository Layout

```text
AILang/
  ailang.py                 # Main CLI entrypoint
  source/                   # Active compiler/runtime code
    lexer/
    parser/
    diagnostics/
    compiler/
    transpiler/
    codegen/
    runtime/
  tools/                    # Utility tooling (quickcheck, regression, session benchmark)
  verifier/                 # Strict quality/security verifier
  benchmarks/               # Cross-language benchmark suite
  tests/                    # Active test tree (growing)
  archived/                 # Retired code, old self-hosted history
```

## Quick Start

```bash
python ailang.py program.ail --check
python ailang.py program.ail --backend=c -o program
python ailang.py program.ail --jit-json
python ailang.py program.ail --jit-json --jit-opt=3 --jit-dump-ir=out/generated/jit/program_o3.ll
python tools/jit_opt_sweep.py program.ail --dump-dir out/generated/jit
```

## Function Syntax

AILang accepts both `def`-style and type-prefix function declarations.

```ailang
def main():
    print("hello")
end

def add(a: int, b: int): int
    return a + b
end

int add2(int a, int b):
    return a + b
end

void main():
    print("hello")
end

float half(float x):
    return x / 2.0
end

double twice(double x):
    return x * 2.0
end

quad widen(quad x):
    return x
end
```

## Whitespace And Blocks

Indentation in normal AILang source is cosmetic. The compiler does not use
Python-style `INDENT`/`DEDENT` tokens, and changing indentation does not open or
close a block. Use indentation for readability only.

Blocks are delimited by explicit language tokens such as `then`, declaration
headers, and `end`. Newlines separate statements where the grammar expects
them, but they are not an indentation-sensitive block structure.

UI DSL examples are also indented by convention; nesting is still expressed by
block headers and explicit `end` markers.

For type-prefix declarations, the return type comes before the function name.
Parameters may use either `name: type` or `type name`. Source-level `quad` is
the 128-bit floating type; internally it is represented as `f128`. `void
main():` is an AILang no-return/no-argv entrypoint; native C output still uses
a standards-compatible `int main(void)` wrapper and returns zero.

## Verification

Fast local check:

```bash
python tools/quickcheck.py
```

Strict gate:

```bash
python -m verifier.cli -d source --preset strict
```

Regression corpus (compile/runtime + leak signals):

```bash
python tools/regression_check.py
```

Clean packaging zip (without caches/build artifacts):

```bash
python tools/make_clean_zip.py
# optional lighter package:
python tools/make_clean_zip.py --exclude-archived
# include packaged binaries in release zip:
python tools/make_clean_zip.py --exclude-archived \
  --include-path out/package/pyinstaller/dist \
  --include-path out/package/nuitka
```

`make_clean_zip.py` preserves executable mode bits for packaged compiler
binaries (`ailangc`, `ailang.bin`, etc.) inside the zip metadata.

Build distributables:

```bash
# Recommended baseline: Python package artifacts
python tools/package_ailang.py --mode package

# Optional executable builds
python tools/package_ailang.py --mode nuitka --onefile
python tools/package_ailang.py --mode pyinstaller --onefile
python tools/package_ailang.py --mode cython --onefile  # experiment only
```

Split release artifacts by target intent (`source`, `windows-x64`, `linux-x64`)
and emit SHA256 checksums:

```bash
python tools/split_release_artifacts.py --require-targets
```

Packaged-binary smoke test:

```bash
python tools/package_smoke.py
python tools/package_extract_smoke.py --platform auto
python tools/package_matrix_report.py
python tools/release_manifest.py
```

## Session Benchmark + Safety Comparison

Capture session snapshots:

```bash
python tools/session_benchmark.py capture --label phase_before
python tools/session_benchmark.py capture --label phase_after
python tools/session_benchmark.py capture --label phase_after_mem --sample-memory
python tools/session_benchmark.py capture --label phase_after_checked --check-leaks
python tools/session_benchmark.py capture --label phase_after_checked_mem --sample-memory --check-leaks
```

Compare sessions:

```bash
python tools/session_benchmark.py compare --before phase_before --after phase_after
```

This captures:
- benchmark performance + output checksum parity
- regression gate vs baseline + saved regression snapshot (compile/runtime + C-backend leak bytes)
- strict verifier pass/fail summary
- god-object candidate counts and per-file flags
- optional leak-budget checks in benchmark phase (`session_benchmark` passes `--check-leaks` when configured)
- optional peak RSS sampling in benchmark phase (`session_benchmark` passes `--sample-memory` when configured)

One-command routine (recommended each session):

```bash
python tools/stabilization_routine.py
# packaging/release hardening slice:
python tools/stabilization_routine.py --full-routine
```

Routine defaults:
- benchmark performance + checksum parity
- memory sampling enabled
- leak gate enabled (`leak_threshold=0`)
- regression gate + saved regression snapshot
- strict verifier (`--preset strict`)
- god-object audit
- automatic compare report against previous routine snapshot
- automatic update of session benchmarking results
- plus release manifest + release checklist gate in routine output

## Current Backend Posture

- Stable default: C backend
- Supported with host toolchain: LLVM IR emission and LLVM AOT (needs clang or llc+gcc on host)
- Backend selection is separate from native toolchain selection:
  - `--backend=llvm` lowers AILang to LLVM IR.
  - `--backend=c` lowers AILang to C.
  - `--native-toolchain=clang` uses clang/LLVM as the native driver.
  - `--native-toolchain=gcc` uses GCC for the C backend.
  - `--native-toolchain=llc-gcc` lowers LLVM IR through `llc` and links with GCC.
- FFI/native build directives:
  - `#cinclude` emits C includes for the C backend.
  - `#link "flags"` is consumed by both C and LLVM AOT native build paths.
  - Imported modules carry their `#link` directives into the final native link.
- FFI reporting:
  - `--ffi-report` / `--ffi-report-json` lists includes, links, extern
    functions, extern vars, records/unions, and exported functions.
- Windows LLVM/PGO: prefer the coherent MSYS2 MinGW64 LLVM stack. For a shell-local setup run:

```powershell
. .\tools\activate_mingw_llvm.ps1
python ailang.py --llvm-pgo-probe
```

- Experimental: JIT (supports `--jit-opt=0..3` and `--jit-dump-ir=PATH` for AOT parity profiling)
- Experimental packaging path: Cython embed binary (not standalone)

## Compatibility Contract

- Language policy: `1.8.0` is the current final line for now. Keep new work on
  `1.8.x` unless a release review proves that the compatibility contract needs
  a new numbered line.
- Review builds should keep the `1.8.x` line and add a prerelease/build label
  outside source, such as `1.8.0-review.1` or `1.8.0-rc.1`.

## Class Cleanup Contract

- Class cleanup is driven by compiler drop plans on checked paths.
- Trivial scalar fields require no destructor work.
- Owned strings, virtualized strings, stack-lowered class locals, and eligible
  scalarized array fields are cleaned or erased by the compiler when proof is
  available.
- Raw OS/library resources such as file descriptors, sockets, SQLite handles,
  native window handles, and manually allocated pointers still need explicit
  ownership intent through a destructor, wrapper type, or checked API.
- Diagnostics warn when a class shape suggests likely-owned resources without a
  cleanup plan.

## Notes

- Keep architectural changes gated by strict verifier and regression snapshots.
- Prefer explicit compiler phases and testable service boundaries over rewrites.
- Generated intermediate compiler artifacts (`.c`, `.ll`, `.o`) default to
  `out/generated/<stage>/` (not next to source files).
