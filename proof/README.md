# AILang Lean proof model

This directory contains a deliberately small Lean 4 model of AILang invariants.
It is not an embedded Lean runtime and it does not replace the AILang compiler.
The purpose is to formalize semantic rules independently and make Lean reject an
inconsistent specification before those rules are relied on by the Python
frontend or any backend.

The first proof surface mirrors two current compiler rules:

- return/void/inference shape rules from `source/parser/return_type_inference.py`;
- the fixed-width signed/unsigned join algorithm used by return inference.

The proof project is intentionally dependency-light: Lean + Std only, pinned by
`lean-toolchain`.  CI builds the project, runs the executable report, and also
runs independent Lean artifact checking with `leanchecker`/`nanoda` while
forbidding `sorry`.

## What is proved in v1

- `void` is structurally not a value-return type;
- inferred `void` implies that no value return exists;
- an inferred non-void result implies no bare return and total value-or-throw
  control-flow according to the summary supplied by the frontend analysis;
- mixing value and bare returns is rejected;
- explicit `void` returning a value is rejected;
- the fixed-integer join is idempotent and commutative for every supported
  `i/u8..8192` pair;
- whenever the fixed-integer join produces a type, that type can represent both
  inputs according to the signedness/width model;
- `i32 + u32 -> i64`, `i64 + u64 -> i128`, while `i8192 + u8192` has no fixed
  lossless join because the next required signed width would exceed 8192.

## Trust boundary

These theorems prove properties of the Lean model.  They do **not yet prove that
the Python implementation is extensionally identical to the model**.  The next
useful step is a generated conformance bridge: emit the compiler's return/join
facts for a finite corpus and compare them with the executable Lean model, then
gradually move from sampled conformance toward a verified translation or a much
smaller formally specified compiler core.

## Local use

Install Lean via `elan`, then run:

```text
cd proof
lake build
lake exe ailang-proof-report
```

`lean` is the compiler/elaborator, `lake` is the build tool, and `elan` selects
the toolchain declared in `lean-toolchain`.
