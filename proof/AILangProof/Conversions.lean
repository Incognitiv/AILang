import AILangProof.Numeric

namespace AILangProof.Conversions

open AILangProof.Numeric

inductive ScalarTy where
  | bool
  | numeric (ty : NumericTy)
  deriving Repr, DecidableEq

inductive ConversionKind where
  | identity
  | losslessWiden
  | checked
  | explicitLossy
  | forbidden
  deriving Repr, DecidableEq

def classifyConversion (source target : ScalarTy) : ConversionKind :=
  if source = target then
    .identity
  else
    match source, target with
    | .bool, .numeric (.int _) => .losslessWiden
    | .numeric (.int _), .bool => .explicitLossy
    | .numeric (.int src), .numeric (.int dst) =>
        if canRepresent dst src then .losslessWiden else .checked
    | .numeric (.float src), .numeric (.float dst) =>
        if canRepresentFloat dst src then .losslessWiden else .explicitLossy
    | .numeric (.int src), .numeric (.float dst) =>
        if src.exactBits ≤ dst.precision then .losslessWiden else .explicitLossy
    | .numeric (.float _), .numeric (.int _) => .explicitLossy
    | _, _ => .forbidden

def scalarRepresents : ScalarTy → ScalarTy → Bool
  | .bool, .bool => true
  | .numeric (.int _), .bool => true
  | .numeric dst, .numeric src => canRepresentNumeric dst src
  | _, _ => false

def losslessClassificationSound (source target : ScalarTy) : Bool :=
  match classifyConversion source target with
  | .losslessWiden => scalarRepresents target source
  | _ => true

theorem classify_lossless_widen_is_representable (source target : ScalarTy) :
    losslessClassificationSound source target = true := by
  cases source with
  | bool =>
      cases target with
      | bool => decide
      | numeric target =>
          cases target with
          | float f => cases f <;> decide
          | int i =>
              rcases i with ⟨s, w⟩
              cases s <;> cases w <;> decide
  | numeric source =>
      cases source with
      | float sf =>
          cases target with
          | bool => cases sf <;> decide
          | numeric target =>
              cases target with
              | float tf => cases sf <;> cases tf <;> decide
              | int ti =>
                  rcases ti with ⟨ts, tw⟩
                  cases sf <;> cases ts <;> cases tw <;> decide
      | int si =>
          rcases si with ⟨ss, sw⟩
          cases target with
          | bool => cases ss <;> cases sw <;> decide
          | numeric target =>
              cases target with
              | float tf => cases ss <;> cases sw <;> cases tf <;> decide
              | int ti =>
                  rcases ti with ⟨ts, tw⟩
                  cases ss <;> cases sw <;> cases ts <;> cases tw <;> decide

theorem f64_to_f128_is_lossless :
    classifyConversion (.numeric (.float .f64)) (.numeric (.float .f128)) = .losslessWiden := by
  decide

theorem f128_to_f64_is_explicit_lossy :
    classifyConversion (.numeric (.float .f128)) (.numeric (.float .f64)) = .explicitLossy := by
  decide

theorem i256_to_i8_is_checked :
    classifyConversion
      (.numeric (.int ⟨.signed, .w256⟩))
      (.numeric (.int ⟨.signed, .w8⟩)) = .checked := by
  decide

theorem bool_to_i64_is_lossless :
    classifyConversion .bool (.numeric (.int ⟨.signed, .w64⟩)) = .losslessWiden := by
  decide

theorem i8_to_bool_is_explicit_lossy :
    classifyConversion (.numeric (.int ⟨.signed, .w8⟩)) .bool = .explicitLossy := by
  decide

end AILangProof.Conversions
