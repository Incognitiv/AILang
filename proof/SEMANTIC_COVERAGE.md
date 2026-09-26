# External semantic verification: executed scope

## Required by the main CI gate

`ailang-hard-gate.yml` invokes the reusable `ailang-lean-proof.yml` on the
same checkout/revision. Its `golden-gate` job depends on successful proof
validation. There is no separate path filter that can leave a backend change
with only the Python gate. Normal compilation remains independent of Lean.

The workflow builds and independently checks `AILangProof`, audits axioms,
compares actual Python numeric semantics with Lean, and checks certificates
emitted by the real Typed IR frontend. Those checks were already connected
to compiler results; they were not a proof of the entire compiler.

## New source-to-observed-value contract

`AILangProof/StringLiterals.lean` specifies string escape semantics on lists
of Unicode code points. It is independent of Python regular expressions and
contains laws about escaped backslashes for every suffix. Its implementation
uses structural recursion and no new axioms or unchecked proof shortcuts.

`check_literal_semantics.py` captures values from the actual lexer helper,
actual AST constructor, and actual expression parser. It also invokes the
ordinary AILang CLI for LLVM AOT, C AOT and JIT, executes the resulting programs,
and records their byte lengths and byte values. The native corpus excludes
embedded NUL: the current C-string ABI truncates it; AST tests still cover NUL.

Each observation becomes a Lean theorem equating the independent decoding of
the ORIGINAL source spelling with the OBSERVED value. `lake env lean` checks
these equations with the kernel using `decide`, not `native_decide`. Only numeric
code points enter generated Lean code. The bridge never substitutes a Python
expected value for the observed output. Changing the result or its source must
produce a false-equation rejection. Missing Lean, missing imports or a process
crash cannot count as a successful negative test.

Reports include the checkout commit, hashes of the decoder, lexer, AST, bridge
and Lean specification, per-channel observation counts, kernel-checked equations,
and negative-test results. Missing rows, compiler failures, invalid UTF-8 output,
and failed Lean checks fail the job. Generated proofs and logs are artifacts.

## What this does not claim

The per-observation equations prove those concrete compiler observations against
the independent specification. They do not prove Python interpreter correctness,
arbitrary programs, FFI lifetime safety, loops, concurrency, or every optimizer
transformation. The report explicitly sets `whole_compiler_proved` to false.
The general backslash laws quantify over every suffix in the Lean specification;
Python-to-specification equivalence beyond the tested observations is not proved.

The numeric/return/Typed IR coverage described in README remains unchanged.
Extending semantic coverage requires additional specifications and source-to-result
bridges; merely adding source files to a workflow cannot prove their behavior.

## Repository line-limit scope correction

The 750-line inventory applies to AILang source and its active Python
implementation (`.ail`, `.py`, `.pyi`). It does not impose AILang's design rule
on C headers, foreign-language examples or generated Wayland protocol sources.
The previous all-language extension was an audit-scope error, not a new compiler
bug in the two generated headers. The number 750 and the old checks of the
maintained compiler remain unchanged. Neither Wayland header is edited.
