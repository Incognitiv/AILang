import AILangProof

open AILangProof.Numeric
open AILangProof.Returns

private def intValue (sign : IntSign) (width : IntWidth) : ValueTy :=
  .int ⟨sign, width⟩

private def floatValue (ty : FloatTy) : ValueTy :=
  .float ty

private def showCheck (label : String) (result : Except Reject ReturnTy) : IO Unit :=
  IO.println s!"{label}: {reprStr result}"

def main : IO Unit := do
  IO.println "AILang / Lean proof-model report"
  IO.println ""
  IO.println "Numeric joins mirrored from return_type_inference.py:"
  IO.println s!"  i32 + u32   => {reprStr (joinNumeric (.int ⟨.signed, .w32⟩) (.int ⟨.unsigned, .w32⟩))}"
  IO.println s!"  i64 + u64   => {reprStr (joinNumeric (.int ⟨.signed, .w64⟩) (.int ⟨.unsigned, .w64⟩))}"
  IO.println s!"  i8192+u8192 => {reprStr (joinNumeric (.int ⟨.signed, .w8192⟩) (.int ⟨.unsigned, .w8192⟩))}"
  IO.println s!"  f32 + f64   => {reprStr (joinNumeric (.float .f32) (.float .f64))}"
  IO.println s!"  f64 -> f128 lossless widening => {reprStr (canRepresentFloat .f128 .f64)}"
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
  showCheck "  float + double expression join"
    (inferReturn joinValue {
      hasBare := false
      values := [floatValue .f32, floatValue .f64]
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
  IO.println "  - joinFloat_idempotent"
  IO.println "  - joinFloat_commutative"
  IO.println "  - joinFloat_never_claims_lossy"
  IO.println "  - f32_f64_join_is_f64"
  IO.println "  - f64_widens_losslessly_to_f128"
  IO.println "  - i8192_u8192_has_no_fixed_lossless_join"
