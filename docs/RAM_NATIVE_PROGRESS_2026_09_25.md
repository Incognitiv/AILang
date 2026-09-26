# Memory-native build and correctness repairs — 2026-09-25

Implementation commit: `4371428b84cdd73bacf345746320d6c37e85bc3a`.
Branch: `agent/ram-first-audit-2026-09-25`, existing draft PR #6.
This extends, rather than supersedes the evidence boundaries of, the earlier
RAM-first audit. It is not a claim that all compiler/runtime issues are solved.

## Working opt-in memory-native adapter

On Linux with memfd/procfs and the project's LLVM dependencies:

```bash
python tools/memory_build.py program.ail -o program --opt 3
./program
# Save available generated IR only on compilation/link/publication failure:
python tools/memory_build.py program.ail -o program --dump-ir-on-error failure.ll
```

The new path uses the existing AILang `compile_to_ir_fast` frontend, then
`compiler.memory_native.compile_ir_object`, then
`compiler.memory_link.link_object_in_memory`, then `publish_executable`.
IR remains a string, the LLVM module stays in process memory, the object is
returned as bytes, and the linker reads/writes private Linux memfd descriptors.
No normal `.ll`, `.o`, or intermediate executable is materialized by this adapter.
Main-source/output/diagnostic path collisions are rejected before compilation.
The completed ELF image is published through a same-directory temporary file,
file fsync, and atomic replacement. It is installed with owner-only mode 0700.
Failure before publication does not replace an existing destination.

The new adapter is explicit and experimental: normal `ailang.py` defaults,
C backend, JIT, and existing AOT paths remain unchanged. Initial support is
native Linux only, not Windows/macOS/FreeBSD or cross-compilation. PGO,
linker plugins, arbitrary linker switches, and shared-library output are not
implemented here. Only `-lNAME`, joined `-LPATH`, and `-pthread` are accepted;
unknown switches fail instead of silently reverting to file intermediates.

This is not a sandbox for an untrusted linker or its input libraries. Inputs,
compiler libraries, CRT objects, and system libraries still need loading.
memfd is pageable memory; swap, OS caches, external toolchain behavior and
unrelated process I/O are outside a strict physical-no-disk-write guarantee.
The 256 MiB executable limit checks the finished output before reading it into
Python; it is **not** a global compiler/linker peak-memory limit.

The memory path keeps LLVM verification, optimization and the existing AILang
frontend/codegen semantics. It does not make untrusted AILang native execution
safe by itself, or independently certify the full frontend with Lean.

## Correctness repairs

### Dependency-aware parsed-module reuse

ModuleCache now records direct import edges and the exact child generation
incorporated into each parent. Freshness traverses the reachable graph iteratively,
checking a diamond dependency once per query and avoiding Python recursion limits.
A changed/deleted/invalidated/rebuilt child, or a retargeted import symlink, makes
its parent stale. An unchanged closure requires metadata probes but no source-byte
rereads. Changing global search paths explicitly clears the loader cache.

Not yet solved: metadata-preserving edits, read/stat races, newly shadowing modules,
all changes to import-resolution context, mutable cached ASTs, parallel session
isolation, content-addressed source snapshots and bounded whole-project caches.
A depth-1100 cache graph test is not a million-line compiler scalability test.

### Bounded immutable literal facts

`source/string_literals.py` reuses UTF-8 bytes/NUL checks for short immutable
compiler literals through a 128-entry LRU. Values longer than 4096 characters
bypass retention. Existing comparison, indexing and static-length helpers use
this shared cache. Edited literals have separate keys; invalid Unicode is not
silently replaced, and embedded NUL does not acquire a false C-string length.

This is **compile-time** reuse, not a changed runtime string ABI. The native
`char_at`, `substr`, UTF-8 mutation, ownership and alias-analysis issues have not
been universally repaired by adding this helper. No bounds guard is removed.

A local hot-call microbenchmark shows the tradeoff: the helper costs roughly
71–79 ns for 1–64 ASCII characters versus 57–67 ns for direct encoding, but about
84 ns versus 132 ns for 1024 ASCII characters, and about 82 ns versus 1632 ns
for a reused 1088-character non-ASCII literal. These are isolated Python helper
measurements, not end-to-end compilation or native string performance numbers.
All cases, including the short-string regression, are retained in the evidence.

### A real benchmark entrypoint and nonempty evidence

The previous CI run `36148339366` returned success. Its full pytest really ran
(690 passed, 11 skipped, 4 xfailed), as did the backend differential and other
reported probes. However, `ailang_execution_matrix` exited zero in 0.057720669 s
with empty stdout/stderr and neither expected execution-matrix report in the
uploaded artifact. The exact upstream `benchmarks/run_benchmarks.py` ended after
`main()` without invoking it. It also lacked `re`, `Iterable` and `_median` imports.

The runner now invokes main, imports its actual dependencies, rejects invalid
repeat counts, materializes implementation iterables once, checks JSON shape,
requires the requested number of finite nonnegative timing samples, requires
integer checksums when checking output, and validates the whole requested
case-by-implementation matrix. Missing rows are failures, not implicit success.
Tests execute the actual runner entrypoint with mocked external boundaries, and
an additional CI test executes the real `--help` subprocess without those mocks.
No gate thresholds were relaxed and no existing tests were removed or xfailed.

## Executed local validation

Local environment: Linux, Python 3.13.5, pytest 9.0.2, llvmlite 0.47.0,
clang at `/usr/local/swift/usr/bin/clang`. This differs from pinned project CI.
Only the relevant source subset was materialized locally; files reconstructed
from GitHub were checked against their upstream Git blob hashes before editing.

104 focused tests passed. These include 28 memory-build, resource-lifetime
and publication tests, with actual LLVM object emission and native linking
in the positive execution probes. Coverage includes O0–O3 executables returning
42, concurrent independent links, library flags, invalid IR/objects, missing
tools, foreign targets, byte limits, descriptor cleanup, output-path checks and
atomic publication failure. Other tests cover module-cache freshness/dependencies,
literal facts, benchmark evidence, and runner orchestration with mocked boundaries.

Three real-repository integration probes were executed by CI for `4371428`,
not locally: benchmark `--help`, AILang source-to-memory-native-executable
execution, and parser/loader transitive module reload. All three passed and
are included in that CI run, not in the 104 local successes. All focused
Python sources parse with the Python 3.10 grammar. The native tests do not rely
on C source glue: test object generation starts from small LLVM IR programs.

A separate 12-sample-per-mode alternating link-transport experiment used the
same already-compiled small object. Median temporary-file linking: 20.989 ms;
median memfd linking: 20.805 ms. This difference is within the observed noise,
not evidence of a large speedup. The experiment excludes AILang frontend work,
final publication, program execution and cold-storage control. Raw samples
are in `link-transport-microbenchmark.json` in the accompanying evidence bundle.

## Still required before broad adoption

Run the unmasked Golden Gate and inspect real execution-matrix rows, not just
the workflow color. Extend native-memory tests to imports, strings, BigInt,
FFI and large workloads before making this the default path. Add memory/time
budgets for frontend, optimizer and linker, per-phase I/O/process/memory metrics,
and explicit portability adapters without implicit disk spill.

For very large projects, schedule module/group work under a memory budget and
release obsolete source/token/AST/IR representations. RAM-only intermediates
need not mean keeping every representation of every module alive at once.
Incremental reuse needs complete dependency/context identities before it can
safely replace whole-module analysis.

Runtime strings require a separate checked representation design (owned data,
byte length, capacity, lifetime/alias information), not a global pointer-to-length
cache. Same-width non-NUL mutation under unique ownership can retain a known
byte length; insertion, UTF-8 width changes, immutable copying and FFI mutation
have different proof and copying requirements. SIMD remains orthogonal: it can
accelerate suitable computations inside a memory-first pipeline.

Original verifier-result-cache context/atomicity issues, legacy AOT intermediate
collisions, C returned-text rereads and general runtime string optimization remain
open. This patch must not be described as completion of the entire repair plan.


## First full CI result and typed-resource follow-up

Golden Gate run `36152930821` for implementation `4371428` finished with:
- full pytest: 775 passed, 11 skipped, 4 xfailed;
- real execution matrix: all 20 rows passed, with three samples per row;
- backend differential: 21 cases, zero failures;
- hosted/freestanding C: 20/10 passing compilations;
- source strict: 328/329 passed; only `memory_native.py` failed with two mypy errors;
- verifier strict, repository hygiene and equivalent-workload gate: passed.

Thus the entire gate was **FAIL**, not PASS. The existing local llvmlite stub
modeled PipelineTuningOptions construction but omitted its context-manager
protocol. The follow-up declares the actual __enter__/__exit__/close interface,
adds a runtime resource-lifetime regression, and does not suppress mypy or
change any acceptance threshold. Resource-management code is also factored to
reduce nesting, and output/diagnostic collisions and timeout cleanup are tested.
The follow-up requires its own CI result; the earlier 775 successes apply to
4371428, not automatically to a later commit.
