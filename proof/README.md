# AILang Lean proof model

This directory contains an external Lean 4 verification layer for AILang. It is
not embedded in AILang syntax, the compiler runtime, or generated programs. The
compiler remains usable without Lean; Lean is a developer/CI proof and
certificate checker that independently validates semantic contracts before they
are relied on by backends.

The current proof surface covers:

- return/void/inference shape rules;
- fixed numeric joins for signed/unsigned integers and `f32`/`f64`/`f128`;
- scalar conversion classification;
- structural invariants of the backend-neutral Typed IR;
- a data-only certificate emitted from real Python-generated Typed IR and parsed
  and checked independently by Lean.

The proof project is intentionally dependency-light: Lean + Std only, pinned by
`lean-toolchain`. CI builds the project, checks the resulting environment with
the bundled `leanchecker`, and runs an axiom audit that rejects undeclared trust
shortcuts such as `sorry`/`admit`, `native_decide`, or project-defined axioms.

## What is proved

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
- lossless conversion classifications imply representability in the formal
  scalar model;
- a valid Typed IR conversion must carry exactly the conversion kind selected by
  the formal classifier;
- a Typed IR binary instruction is valid only when both operands and the result
  have the same numeric type;
- deliberately forged conversion metadata and mismatched binary types are
  rejected by the formal Typed IR model.

`f128` is a source-level semantic type even though the current C and LLVM
backends deliberately fail closed rather than lowering it to an inexact
`long double` or `double` substitute.

## Exhaustive implementation conformance

`check_python_conformance.py` connects the finite formal type algebra to the
actual canonical Python implementation in `source/type_semantics.py`.

CI exhaustively checks:

- all 625 ordered pairs from the 25 fixed numeric types (22 signed/unsigned
  integer types plus `f32`, `f64`, and `f128`) for numeric join equality;
- all 676 ordered pairs from the 26 scalar types (`bool` plus those 25 numeric
  types) for conversion-classifier equality.

A missing row, unexpected row, or result mismatch fails CI. These are exhaustive
extensional checks over the complete current finite domains, not sampling or
fuzzing.

## Real Typed IR certificate bridge

`source/ir/certificate.py` serializes an actual `FunctionIR` produced by the
Python frontend into a versioned, data-only certificate. The certificate repeats
SSA names and types deliberately so the Lean checker can validate them against
its own environment instead of trusting Python object invariants.

`CertificateCheck.lean` parses the certificate and independently checks, among
other things:

- certificate version and row structure;
- unique/fresh SSA names;
- operand definition-before-use and repeated type consistency;
- formal conversion classification and implicit-conversion legality;
- numeric binary type uniformity;
- supported binary operators;
- return definition and exact function return-type agreement;
- absence of rows after the terminating return.

`check_ir_certificate.py` generates certificates from real AILang source through
lexer -> parser -> AST -> Typed IR and invokes the Lean executable. The gate
currently requires three real certificates to be accepted and two deliberately
forged certificates to be rejected. This tests both positive and negative sides
of the bridge.

## Trust boundary

The certificate checker is substantially stronger than a disconnected proof
model, but it is not a proof of the Python interpreter or compiler
implementation itself. Python is still trusted to serialize the `FunctionIR`
object it produced; Lean independently validates the serialized semantic facts
and SSA structure it receives.

Likewise, the return-shape theorems currently prove properties of the Lean model
and are not yet an exhaustive equivalence proof for arbitrary Python AST/control
flow. The fixed numeric join and scalar conversion domains are stronger because
they are exhaustively compared against the real canonical Python functions.

The current Typed IR frontend is intentionally narrow and fail-closed. It covers
straight-line numeric binary returns over function parameters. The next useful
boundary is to extend this same certificate-checked path to constants, local SSA
bindings, nested expressions, and then control-flow blocks, without allowing
backends to re-infer language semantics.

## Local use

Install Lean via `elan`, then run:

```text
cd proof
lake build
lake exe ailangProofReport
lake env leanchecker AILangProof
cd ..
python proof/check_python_conformance.py
python proof/check_ir_certificate.py
```

`lean` is the compiler/elaborator, `lake` is the build tool, and `elan` selects
the toolchain declared in `lean-toolchain`.
