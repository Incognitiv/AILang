# RAM-first audit and initial cache repair — 2026-09-25

## Status and scope

This is a targeted source audit of compilation, JIT execution, module caching,
verification caching, and gate orchestration. It is **not** a completed audit of
every repository file, a runtime performance certification, or a Golden Gate PASS.

The default `main` ref was `6d49c38b274b0b415e12d4a2c3fe76da828285c0`.
The repair is based on the newer development ref
`agent/unbounded-field-semantics` at
`f6e1d4ba306c880d4c7a2ce1e6e71ba80b5ec78a`.
Its tree is `2e51ca6ec988bd543172b9853dd5964a95c0821a`, identical to the
Stage 5 expression branch at `14e269e33013ea1562aca158b4e97ea9c2c0bbe4`.
No existing branch is consolidated or force-updated by this repair.

## Main conclusion

AILang is not a disk-executed language. The active fast JIT already builds LLVM
IR and ORC LLJIT code in memory, and its repeat mode compiles once before
repeated execution. Moving intermediates to RAM can reduce build/startup work;
it does not, by itself, make an already compiled numerical loop faster.

The more useful target is **read once per source snapshot, parse once per
identity, validate once per complete context, and reuse bounded immutable
results**. The existing cache invalidation rules must be repaired before adding
a long-lived compiler service or a stronger cache.

## Audited paths and findings

### F1 — Module cache freshness fails open [high; partially repaired]

`source/compiler/modules.py`, original `ModuleCache` (lines 31–86), considered
a file stale only when its floating-point mtime increased. Missing timestamps
and stat errors were treated as fresh. Old timestamp evidence could also survive
a replacement when recording the new timestamp failed.

The added tests reproduce backwards timestamp changes, a changed size with
restored mtime, replacement by a same-size/same-mtime file, deletion, missing
metadata, unreadable metadata, failed metadata replacement, and a mismatched
module path.

**Implemented:** move `ModuleCache` into `source/compiler/module_cache.py` and
re-export it through the original import path. Compare named device, inode,
size, mtime-nanoseconds, and ctime-nanoseconds fields. Missing or failed metadata
is stale; replacing an entry first clears prior evidence. Preserve the existing
module/loading operations and the legacy `mtimes` mapping.

**Not solved by this patch:** metadata is not a content hash; same-metadata
in-place edits on some filesystems, read/stat races, source snapshots, and
transitive dependency invalidation remain separate work. Filesystem stat and
path canonicalization still occur. This is a correctness repair, not a
zero-filesystem-I/O or performance claim.

### F2 — Cached parents bypass dependency revalidation [high; open]

`ModuleLoader.load_module` returns a cached module before `_load_file` traverses
its imports. `_load_file` merges dependency declarations into the parent.
Changing a dependency need not invalidate an unchanged cached parent.

The singleton loader also has mutable `current_file`, search paths, module
objects, and AST nodes. Sharing these objects across parallel compilation
requests is not a safe basis for a daemon.

**Next:** per-session source snapshots, a dependency graph, reverse invalidation,
and immutable cached parse products; include import-resolution context in keys.
Bind freshness to the bytes actually parsed, not a later stat result.

### F3 — Verification result keys omit verification context [high when cache enabled; open]

`verifier/cache.py` hashes only code and preset.
`verifier/core.py::_run_verification` returns that cached result before executing
the requested checks. The key omits `check_imports`, source identity, tool and
checker versions, configuration, and imported dependencies. This can reuse a
result produced under a different verification context.

The CLI's result cache is **off by default**. The audited Golden Gate strict
commands request import checking and do **not** pass `--cache`. This finding
must not be presented as evidence that every default gate run is bypassed.

**Next:** refuse reuse when the complete context is not available. Start by
bypassing results for dependency-sensitive checks until dependency fingerprints
exist. Add a cache schema/version, tool/rule/config fingerprints, explicit input
identity, and tests that change each key dimension. A persistent cache is an
optimization, not an authority that may override required checks.

The JSON implementation also assumes a dictionary and numeric timestamp after
decoding. Valid but wrong-shaped JSON is not covered by its current exception
handling. Writes truncate the destination directly, rather than publishing an
atomic replacement. Validate schema, treat corruption as a miss, and publish
same-directory temporary files atomically. Do not add a RAM result tier on top
of the current incomplete key.

### F4 — Intermediate paths collide across simultaneous builds [high; open]

`source/cli/compilation.py::_intermediate_artifact_path` derives the path from
source path, stage, and suffix. Concurrent compilations of the same source share
C/LLVM/object intermediate filenames even when their options differ.

This is a static collision finding; concurrent native corruption was not
executed in this environment.

**Next:** a private workspace per compilation, with deliberate opt-in retained
dumps. Publish final artifacts atomically and define same-output concurrency
policy. Keep PGO profiles and persistent object-cache identities distinct.
Test two simultaneous builds of one source with different options.

### F5 — Avoidable roundtrips and optimization work [performance candidate; open]

`compile_via_c` discards the source string returned by `transpile_file`, then
opens the generated C file again to inspect runtime/link markers. Preserve that
returned string for scanning.

`compile_to_native` similarly rereads LLVM text after materialization. Reuse
the original IR only when an external optimizer has not changed it; otherwise
scan the actual optimizer result or retain structured dependency information.

The AOT path can run external `opt` and then optimizing clang. Measure this
pipeline before removing a pass: the clang and llc fallback paths are different.
IR text roundtrips and optimizer CPU time are not storage latency alone.

Do not blindly replace native compiler inputs with stdin: relative includes,
diagnostics, ccache, PGO identities, toolchain compatibility, and failure dumps
must retain their semantics. A private temporary workspace is an acceptable
boundary when a tool genuinely requires filenames.

### F6 — Verifier parallelism multiplies work [performance candidate; open]

`verifier/cli.py` can run up to eight file workers by default, while
`verifier/core.py` can launch up to eight tool jobs for each file. This permits
up to 64 simultaneous tool tasks, not necessarily 64 active native processes.
The optimum depends on CPU, memory, tool startup, and workload.

The code already reuses a verifier instance per worker; preserve that.
Introduce one measured global process/thread and memory budget. Batch external
tools where their diagnostics remain equivalent, and let internal checkers
share an immutable Python AST/source snapshot instead of independently
reopening/reparsing a changing file.

`verify_code` creates a temporary file before looking up the result cache.
A valid context-aware cache lookup should precede materialization. A shared
snapshot is also needed so all checkers evaluate the same source generation.

### F7 — Existing JIT measurements need careful interpretation [measurement; open]

`source/codegen/fast_jit.py::_fast_jit_repeat_file_inprocess` already reports
`compile_ms` separately from `runs_ms` and retains the JIT tracker while reusing
the callable. The source-file read occurs before `compile_ms` starts.

When output capture is enabled, each timed iteration includes `_run_jit_once`,
which creates a temporary output file, redirects stdout, flushes native output,
and reads/decodes the captured text. That is not an isolated compute-kernel
measurement. Preserve output/checksum correctness checks, but label the scope
and measure output-free kernels separately.

Repeated `main` calls in one process can also retain program globals or external
state. A repeat benchmark must define state reset; do not equate it with a
fresh-process AOT run without checking workload equivalence.

## Validation performed for this repair

The exact original `source/compiler/modules.py` was reconstructed from the
GitHub connector and its Git blob identity was checked:

```text
UTF-8 size: 15701 bytes
Git blob SHA-1: 55664e0f7c5d502bdc48e58b41d01cc50cab7b0d
```

The baseline harness extracts the original `ModuleCache` class with Python's
AST module, without altering its implementation or importing the rest of the
compiler. The same focused pytest file is then run against that class and
against the new independently importable cache module.

```text
Original ModuleCache: 8 failed, 8 passed
Patched ModuleCache: 16 passed
```

The tests use real temporary source files; stat failure and forbidden content
rereads are explicitly injected. One test asserts that a cache lookup/freshness
check does not reopen source bytes. It does not assert zero metadata calls.

Additional checks:
- All remaining original class/function ASTs, including `Module` and
  `ModuleLoader`, are unchanged.
- Changed Python files parse with the Python 3.10 grammar.
- The compiler package initializer matches its original Git blob.

Local environment: Linux, Python 3.13.5, pytest 9.0.2.
The project gate instead selects Python 3.11 and pinned dependencies.
The complete repository/native toolchain was not materialized locally;
pinned Black/isort/Ruff/mypy, full pytest, C/LLVM/JIT execution matrices,
leak checks, and Lean were **not run** in this validation.
The tests establish the scoped cache behavior, not end-to-end compiler safety.

## Repair sequence and acceptance conditions

### Phase 0 — Preserve correctness before increasing reuse

Finish F2/F3, isolate intermediate workspaces (F4), and add negative tests.
Required cases include dependency edits, unchanged timestamps, permission
errors, source changes during a request, configuration/toolchain changes,
corrupt cache entries, import-resolution changes, and concurrent requests.
There must be no reuse of an unproven result.

### Phase 1 — Remove demonstrably redundant work

Fix the returned-C-string roundtrip; make source/AST/IR ownership explicit.
Measure read/decode, lex/parse, semantic validation, IR generation, LLVM
optimization, native emission/linking, and actual execution separately.
Count source/intermediate reads and bytes, subprocess starts, cache hits/misses,
and retained/peak memory. Integrate with existing phase/session benchmark
tooling rather than replacing it.

### Phase 2 — Add bounded in-memory compilation sessions

Use `SourceSnapshot -> parsed AST -> typed/checked IR -> backend product`.
Keys must include exact source and transitive dependency identities, compiler
and proof-model versions, target/ABI/CPU features, optimization flags, safety
mode, and checker configuration where relevant.

A cache must have a byte budget, eviction, and explicit lifetime ownership.
Do not evict native JIT code while a callable still references its tracker.
A one-shot CLI should remain available. A daemon is optional and comes only
after session isolation works; a RAM disk is not the architecture.

Persist only source inputs, requested dumps, durable outputs, profiles, and
validated optional cache entries. RAM-first does not mean losing source files,
proof evidence, debugging artifacts, or recovery capability.

### Phase 3 — Optimize generated programs using runtime profiles

Profile allocation count/bytes, copies, array layout, dispatch, BigInt/string
algorithms, cache misses, and checks in hot loops. Consider arenas or
specialization only with lifetime/escape proofs and workload evidence.
Do not remove bounds/overflow/ownership checks merely to improve benchmark
numbers. A Python-hosted compiler does not imply Python-speed native output.

### Phase 4 — Require the real gates and honest performance comparisons

Run with the project's pinned environment:

```bash
python -m pytest -q tests/test_module_cache_freshness.py
python tools/golden_gate.py
python proof/check_python_conformance.py
python proof/check_ir_certificate.py
```

The Lean workflow also builds and independently checks the proof library with
an axiom audit; the Python bridge scripts are not substitutes for that build.

Use existing session capture/comparison and report, separately:
- fresh process with warm OS page cache;
- already-running compilation session with unchanged inputs;
- one changed leaf dependency and one changed public interface;
- large dependency graph under a defined cache memory limit;
- AOT program runtime and warmed JIT kernel runtime.

A genuine cold-storage experiment needs explicit cache-control methodology;
starting a fresh process is not enough. Compare identical work, inputs,
outputs, backend options, safety mode, machine, and toolchain. Report medians
and tail/variance, memory, and correctness; do not infer overall speedup from
nanosecond-versus-millisecond hardware ratios.

## Verification and proof boundaries

`verifier/` checks Python implementation quality. Compiler semantic checks,
LLVM structural verification, selected Typed IR/Lean certificates, native
runtime guards, FFI ownership, and process isolation are different layers.
Passing one layer does not prove all of the others.

The current Golden Gate invokes strict source/verifier checks, regression
tests, C compilation, backend differential tests, execution matrices, and
equivalent-workload checks. The Lean workflow is separate and path-filtered;
compiler-cache-only changes do not automatically match its proof paths.
This repair does not weaken any gate, change language semantics, replace the
pure-AIL BigInt implementation, or add a native C runtime.

## Primary references

Repository statements above refer to the pinned development commit, not an
unversioned README. Primary inspected files: `README.md`,
`source/compiler/modules.py`, `source/compiler/compiler.py`,
`source/codegen/fast_jit.py`, `source/cli/compilation.py`,
`verifier/cache.py`, `verifier/core.py`, `verifier/cli.py`,
`tools/golden_gate.py`, `pyproject.toml`, and both `.github/workflows` files.

External context:
- Linux kernel page-cache documentation:
  https://docs.kernel.org/mm/page_cache.html
- Intel Memory Latency Checker (latency depends on cache/NUMA and load):
  https://www.intel.com/content/www/us/en/developer/articles/tool/intelr-memory-latency-checker.html
- Lenovo's specific NVMe memory-tiering workload reports 100–200 microsecond
  read latency, not hundreds of milliseconds; it is not a universal SSD figure:
  https://lenovopress.lenovo.com/lp2518-optimizing-vcf-private-cloud-economics-with-nvme-memory-tiering-on-thinkagile-vx
