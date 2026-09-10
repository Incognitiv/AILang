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
`lean-toolchain`. CI builds the project, checks the resulting environment with
the bundled `leanchecker`, and runs an axiom audit that rejects undeclared trust
shortcuts such as `sorry`/`admit`, `native_decide`, or project-defined axioms.

`nanoda` is intentionally not a release gate at the moment. The current
`leanprover/lean-action` integration has a known upstream export-format failure
(`leanprover/lean-action#169`) that produces `invalid digit found in string` on
recent Lean versions before project declarations are checked. It can be restored
once that checker/exporter combination supports the pinned Lean toolchain.

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

## Implementation conformance

The fixed-integer proof is connected back to the actual Python frontend rather
than being left as an isolated specification. `check_python_conformance.py`
runs the Lean model and the real `_join_ints` implementation over the complete
fixed-integer domain: 22 supported signed/unsigned types, or 484 ordered input
pairs. CI fails on a missing row, an unexpected row, or any result mismatch.

This is exhaustive extensional conformance for the current fixed-integer join
domain, not sampling. Combined with the Lean theorem that a successful model
join can represent both operands, it gives a checked bridge from that theorem to
the current Python `_join_ints` behaviour for every supported fixed-width pair.

## Trust boundary

The return-shape theorems currently prove properties of the Lean model; they do
**not yet prove that the full Python return-inference implementation is
extensionally identical to that model**. The fixed-integer join is stronger: its
finite input domain is exhaustively compared against the real Python function in
CI. The next useful bridge is to extract return summaries from real parsed AILang
functions and compare those frontend decisions with the Lean return model.

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
