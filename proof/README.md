# AILang Lean proof model

This directory contains a deliberately small Lean 4 model of AILang invariants.
It is not an embedded Lean runtime and it does not replace the AILang compiler.
The purpose is to formalize semantic rules independently and make Lean reject an
inconsistent specification before those rules are relied on by the Python
frontend or any backend.

The first proof surface mirrors two current compiler rules:

- return/void/inference shape rules from `source/parser/return_type_inference.py`;
- the fixed numeric join algorithm used by return inference, including
  signed/unsigned integers and `f32`/`f64`/`f128`.

The proof project is intentionally dependency-light: Lean + Std only, pinned by
`lean-toolchain`. CI builds the project, checks the resulting environment with
the bundled `leanchecker`, and runs an axiom audit that rejects undeclared trust
shortcuts such as `sorry`/`admit`, `native_decide`, or project-defined axioms.

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
  lossless join because the next required signed width would exceed 8192;
- the float join is idempotent and commutative over `f32`, `f64`, and `f128`;
- the selected float join represents both operands in the ordered precision
  hierarchy;
- `f32 + f64 -> f64`, and `f64 -> f128` is a lossless widening in the semantic
  model.

`f128` is a source-level semantic type even though the current C and LLVM
backends deliberately fail closed rather than lowering it to an inexact
`long double` or `double` substitute.

## Implementation conformance

The numeric proof is connected back to the actual Python frontend rather than
being left as an isolated specification. `check_python_conformance.py` runs the
Lean model and the real `_join` implementation over the complete fixed numeric
domain: 22 supported signed/unsigned integer types plus `f32`, `f64`, and
`f128`, or 625 ordered input pairs. CI fails on a missing row, an unexpected
row, or any result mismatch.

This is exhaustive extensional conformance for the current fixed numeric join
domain, not sampling. Combined with the Lean theorems for integer and floating
joins, it provides a checked bridge from the formal type algebra to the current
Python `_join` behaviour for every supported fixed numeric pair.

## Trust boundary

The return-shape theorems currently prove properties of the Lean model; they do
**not yet prove that the full Python return-inference implementation is
extensionally identical to that model**. The fixed numeric join is stronger: its
finite input domain is exhaustively compared against the real Python function in
CI. The next semantic boundary to formalize is conversion at explicit function,
assignment, and argument types so that the future typed IR can represent every
widening, checked conversion, or explicit lossy conversion rather than relying
on backend-specific coercion.

## Local use

Install Lean via `elan`, then run:

```text
cd proof
lake build
lake exe ailangProofReport
lake env leanchecker AILangProof
cd ..
python proof/check_python_conformance.py
```

`lean` is the compiler/elaborator, `lake` is the build tool, and `elan` selects
the toolchain declared in `lean-toolchain`.
