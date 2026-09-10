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
  and checked independently by Lean;
- Stage 5 straight-line numeric constants, typed local SSA bindings, and nested
  arithmetic expressions on that same certificate-checked path.

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
Python frontend into a versioned, data-only certificate. Certificate version 2
adds typed numeric constant rows to the Stage 4 conversion/binary/return rows.
The certificate repeats SSA names and types deliberately so the Lean checker can
validate them against its own environment instead of trusting Python object
invariants.

`CertificateCheck.lean` parses the certificate and independently checks, among
other things:

- certificate version and row structure;
- unique/fresh SSA names;
- operand definition-before-use and repeated type consistency;
- constant literal family (`int` versus `float`) against its IR result type;
- formal conversion classification and implicit-conversion legality;
- numeric binary type uniformity;
- supported binary operators;
- return definition and exact function return-type agreement;
- absence of rows after the terminating return.

`check_ir_certificate.py` generates certificates from real AILang source through
lexer -> parser -> AST -> Typed IR and invokes the Lean executable. Stage 5 adds
real examples for contextual `f32` literals, typed local bindings, nested
expressions, and checked local integer boundaries. The negative side also
forges a constant-kind row and requires Lean to reject it.

## Stage 5 frontend boundary

The current Typed IR frontend accepts one straight-line function body containing
zero or more typed local declarations followed by one valued return. Within
those declarations and the return expression it recursively lowers:

- fixed numeric function parameters and local bindings;
- integer and floating literals;
- nested `+`, `-`, `*`, and `/`;
- every implicit widening or checked conversion as an explicit IR `Convert`.

Unsuffixed floating literals preserve the existing frontend rule: when an
immediate binary sibling already has `f32`, `f64`, or `f128`, the literal uses
that sibling type. An explicit `f`, `d`, or `q` suffix chooses its own precision.

There is one deliberate fail-closed exception. The current parser stores a
floating literal as a Python `float` and does not retain its exact source lexeme.
That is sufficient for the existing `f32`/`f64` path, but it cannot faithfully
represent an arbitrary IEEE binary128 literal. Therefore Stage 5 refuses to
materialize an `f128` constant rather than silently certifying a value that has
already been rounded through Python binary64. `f128` values from parameters and
non-literal expressions remain supported.

Mutation (`Assign`), calls, control flow, and non-numeric expressions remain
outside this Stage 5 slice and fail closed.

## Trust boundary

The certificate checker is substantially stronger than a disconnected proof
model, but it is not a proof of the Python interpreter or compiler
implementation itself. Python is still trusted to serialize the `FunctionIR`
object it produced; Lean independently validates the serialized semantic facts
and SSA structure it receives.

For constants, Lean validates the constant family and typed SSA structure. It
does not independently parse AILang numeric literal spelling or prove the
source-text-to-AST numeric conversion. The `f128` fail-closed rule above avoids
claiming exactness where the current parser cannot provide it.

Likewise, the return-shape theorems currently prove properties of the Lean model
and are not yet an exhaustive equivalence proof for arbitrary Python AST/control
flow. The fixed numeric join and scalar conversion domains are stronger because
they are exhaustively compared against the real canonical Python functions.

The next natural Typed IR boundary after Stage 5 is explicit control-flow
blocks and joins/phi semantics, followed by moving backends to consume Typed IR
instead of re-inferring language semantics from AST nodes.

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
