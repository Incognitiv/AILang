import AILangProof

open AILangProof.Numeric
open AILangProof.Returns

private def intValue (sign : IntSign) (width : IntWidth) : ValueTy :=
  .int ⟨sign, width⟩

private def showCheck (label : String) (result : Except Reject ReturnTy) : IO Unit :=
  IO.println s!"{label}: {reprStr result}"

def main : IO Unit := do
  IO.println "AILang / Lean proof-model report"
  IO.println ""
  IO.println "Fixed integer joins mirrored from return_type_inference.py:"
  IO.println s!"  i32 + u32   => {reprStr (joinInt ⟨.signed, .w32⟩ ⟨.unsigned, .w32⟩)}"
  IO.println s!"  i64 + u64   => {reprStr (joinInt ⟨.signed, .w64⟩ ⟨.unsigned, .w64⟩)}"
  IO.println s!"  i8192+u8192 => {reprStr (joinInt ⟨.signed, .w8192⟩ ⟨.unsigned, .w8192⟩)}"
  IO.println ""
  IO.println "Return-shape diagnostics:"
  showCheck "  inferred [value i64, bare]"
    (inferReturn joinValue {
      hasBare := true
      values := [intValue .signed .w64]
      allPathsValueOrThrow := true
    })
  showCheck "  inferred non-void with fallthrough"
    (inferReturn joinValue {
      hasBare := false
      values := [intValue .signed .w64]
      allPathsValueOrThrow := false
    })
  showCheck "  explicit void returning a value"
    (checkExplicit {
      hasBare := false
      values := [intValue .signed .w64]
      allPathsValueOrThrow := true
    } .void)
  showCheck "  clean inferred i64"
    (inferReturn joinValue {
      hasBare := false
      values := [intValue .signed .w64, intValue .signed .w32]
      allPathsValueOrThrow := true
    })
  IO.println ""
  IO.println "Compiled theorems include:"
  IO.println "  - void_is_not_a_value"
  IO.println "  - inferred_return_sound"
  IO.println "  - mixed_value_and_bare_is_rejected"
  IO.println "  - joinInt_idempotent"
  IO.println "  - joinInt_commutative"
  IO.println "  - joinInt_never_claims_lossy"
  IO.println "  - i8192_u8192_has_no_fixed_lossless_join"
