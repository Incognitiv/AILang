import AILangProof.Conversions

namespace AILangProof.TypedIR

open AILangProof.Numeric
open AILangProof.Conversions

structure ConvertInst where
  source : ScalarTy
  target : ScalarTy
  kind : ConversionKind
  deriving Repr, DecidableEq

structure BinaryInst where
  left : NumericTy
  right : NumericTy
  result : NumericTy
  deriving Repr, DecidableEq

def implicitKind : ConversionKind → Bool
  | .identity => true
  | .losslessWiden => true
  | .checked => true
  | .explicitLossy => false
  | .forbidden => false

def ConvertInst.valid (inst : ConvertInst) : Bool :=
  inst.kind == classifyConversion inst.source inst.target &&
    inst.kind != .identity &&
    implicitKind inst.kind

def BinaryInst.valid (inst : BinaryInst) : Bool :=
  inst.left == inst.result && inst.right == inst.result

private def f32 : ScalarTy := .numeric (.float .f32)
private def f64 : ScalarTy := .numeric (.float .f64)
private def f128 : ScalarTy := .numeric (.float .f128)

private def widenF32F64 : ConvertInst :=
  ⟨f32, f64, .losslessWiden⟩

private def addF64 : BinaryInst :=
  ⟨.float .f64, .float .f64, .float .f64⟩

private def widenF64F128 : ConvertInst :=
  ⟨f64, f128, .losslessWiden⟩

private def checkedI256I8 : ConvertInst :=
  ⟨
    .numeric (.int ⟨.signed, .w256⟩),
    .numeric (.int ⟨.signed, .w8⟩),
    .checked
  ⟩

private def forgedNarrow : ConvertInst :=
  ⟨f64, f32, .losslessWiden⟩

def goldenFloatDoubleQuadValid : Bool :=
  widenF32F64.valid && addF64.valid && widenF64F128.valid

theorem f32_to_f64_ir_widen_is_valid : widenF32F64.valid = true := by
  decide

theorem f64_to_f128_ir_widen_is_valid : widenF64F128.valid = true := by
  decide

theorem i256_to_i8_ir_checked_is_valid : checkedI256I8.valid = true := by
  decide

theorem forged_f64_to_f32_widen_is_rejected : forgedNarrow.valid = false := by
  decide

theorem mismatched_binary_types_are_rejected :
    BinaryInst.valid ⟨.float .f32, .float .f64, .float .f64⟩ = false := by
  decide

theorem golden_float_double_quad_ir_is_valid :
    goldenFloatDoubleQuadValid = true := by
  decide

end AILangProof.TypedIR
