# AILang stabilization plan — ADAPT as the real-world gate

Status: active

This plan turns the Incognitiv/A.D.A.P.T. repository into AILang's primary real-world integration workload. The goal is not to make ADAPT green by weakening diagnostics or editing around compiler defects. The goal is to make AILang trustworthy enough that ADAPT failures have a clear, actionable meaning.

## Branch policy

- Continue stabilization on `agent/unbounded-field-semantics`.
- Do not create per-fix branches.
- Do not promote this branch to `main` while the hard gate or the ADAPT gate is red.
- After promotion, ordinary work should happen on `main`.
- A future `bootstrap/selfhost` branch is allowed only when self-hosting work actually starts.

## Phase 0 — test truthfulness

Before using any test result as evidence, the harness itself must report failures correctly.

### Required

- JIT CLI propagates the AILang program's `main()` exit status to the shell.
- Benchmark result extraction uses an explicit machine-readable result contract rather than "last integer on stdout".
- Test runners distinguish process success from application-level test counters.
- ADAPT integration tests fail when an embedded test reports failures even if the process returns zero.

### Gate

A deliberately failing AILang program must fail JIT, LLVM AOT, C-AOT, shell scripts and CI consistently.

## Phase 1 — local type inference correctness

### Required

- Untyped assignment can inherit the known return type of a function call.
- Field access through such a locally inferred object preserves the declared field type.
- Inference reaches a fixpoint without silently widening established types.
- Conflicting return arms remain errors.

### Gate

Dedicated parser/type-inference regressions pass and the ADAPT server no longer acquires an incorrect `i32` narrowing from incomplete local type propagation.

## Phase 2 — cross-module return inference

Current ADAPT QSP tests expose a second boundary: imported function signatures are not always available early enough for unannotated return inference.

### Required

- imported function return types participate in inference;
- imported record/class field types participate in inference;
- `def main():` can infer an integer result when all return arms are imported integer expressions or integer literals;
- inference remains deterministic regardless of import order.

### Gate

The four known QSP inference failures pass without adding explicit return annotations solely as a workaround.

## Phase 3 — ownership diagnostics

The ADAPT corpus currently produces a large number of ownership warnings, especially around `str_array_push(owned_string)`.

### Required

Classify every recurring warning family as one of:

1. real ownership defect in the program;
2. false positive in AILang diagnostics;
3. ownership transfer operation whose contract is not represented in the analyzer.

Then fix the correct layer. Do not blanket-disable the warning.

Diagnostics originating in imported files must retain real source locations; `Line 0` is not acceptable for a stable gate.

### Gate

ADAPT's checked subset is either clean or each intentionally excluded/generated/platform-specific file is explicitly classified with a documented reason.

## Phase 4 — backend parity on real programs

Primary lanes:

1. AILang JIT
2. AILang LLVM AOT
3. AILang C-AOT

C23/GCC/Clang reference programs are comparison baselines, not AILang's definition of success.

### Required workloads

- QRAX SHA-256
- QRAX packet/semantic core
- semantic store
- dialogue suite
- persistence/restart workload
- ADAPT HTTP server

### Gate

All three AILang execution modes agree on observable results and exit status for the selected workloads.

## Phase 5 — ADAPT application gate

Build and run the real ADAPT server from AILang source.

### Required smoke sequence

1. compile `tools/adapt_serve.ail`;
2. start the native executable against a temporary state directory;
3. `GET /api/adapt/status` returns valid connected-state JSON;
4. `POST /api/adapt/chat` returns a valid dialogue response;
5. write state/learning data;
6. stop cleanly;
7. restart from the same SQLite state;
8. verify persisted state and event counters;
9. run a QRAX operation after restart.

### Gate

The full sequence passes on the supported host without manual intervention.

## Phase 6 — memory and ownership runtime gate

### Required

- investigate the QRAX SHA-256 C-AOT `POSSIBLE LEAK` report;
- distinguish intentional process-lifetime/global allocations from leaked owned values;
- ensure normal server shutdown runs cleanup and does not produce unexplained live allocations;
- add focused regressions for every compiler/runtime leak fixed.

### Gate

No unexplained live owned allocation remains in the selected ADAPT workloads.

## Phase 7 — compile-time scalability

Large ADAPT builds currently show a major disparity between the C backend and LLVM AOT.

### Required

Profile the compiler by phase on `tools/adapt_serve.ail` and record:

- import/parse time;
- diagnostics/prepass time;
- IR/code generation time;
- LLVM optimization time;
- native link time;
- peak memory where practical.

Optimize the dominant stages instead of merely increasing CI timeouts.

### Gate

A real ADAPT build completes within an explicitly recorded and reproducible budget on the reference environment. The budget may initially be conservative; regressions against it must be visible.

## Phase 8 — Golden Gate integration

Add ADAPT as a separate, named real-world layer rather than mixing it into unit tests.

The gate should be reproducible from a pinned ADAPT commit and should report:

- AILang commit;
- ADAPT commit;
- backend;
- compile duration;
- runtime result;
- exit status;
- persistence/restart result;
- memory result.

### Gate

Existing AILang hard gate is green and ADAPT real-world gate is green.

## Phase 9 — promote and simplify repository topology

Only after all release gates are green:

- promote the stabilized linear head to `main`;
- close superseded PRs;
- remove redundant historical/trigger branches after preserving any unique regression tests or useful code;
- continue normal development on `main`.

Target topology:

```text
main
└── bootstrap/selfhost   # only when self-hosting actually begins
```

## Phase 10 — self-hosting/bootstrap work

This phase is deliberately postponed until the current compiler is a trustworthy Stage 0 seed.

The intended long-term target is stronger than ordinary self-hosting: Stage 1+ should not depend on Python, LLVM IR or a C23 translation path. That requires a native backend/object-emission strategy and its own bootstrap plan; it must not be mixed into stabilization work.

## Promotion criteria for `main`

Promotion is allowed only when all of the following are true:

- core pytest/regression suite is green;
- hard/golden gate is green;
- JIT/AOT failure semantics are truthful;
- benchmark harness is deterministic;
- selected bundled `.ail` examples are clean or explicitly classified;
- ADAPT application gate passes end-to-end;
- no unexplained ownership/runtime leak remains in the selected gate workloads;
- compile-time regression budget is recorded and met.
