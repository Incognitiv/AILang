import Std

namespace AILangProof.Numeric

inductive IntSign where
  | signed
  | unsigned
  deriving Repr, DecidableEq

inductive IntWidth where
  | w8 | w16 | w32 | w64 | w128 | w256 | w512 | w1024 | w2048 | w4096 | w8192
  deriving Repr, DecidableEq

def IntWidth.bits : IntWidth → Nat
  | .w8 => 8
  | .w16 => 16
  | .w32 => 32
  | .w64 => 64
  | .w128 => 128
  | .w256 => 256
  | .w512 => 512
  | .w1024 => 1024
  | .w2048 => 2048
  | .w4096 => 4096
  | .w8192 => 8192

structure IntTy where
  sign : IntSign
  width : IntWidth
  deriving Repr, DecidableEq

def IntTy.exactBits (ty : IntTy) : Nat :=
  match ty.sign with
  | .signed => ty.width.bits - 1
  | .unsigned => ty.width.bits

def widthForBits (n : Nat) : Option IntWidth :=
  if n ≤ 8 then some .w8
  else if n ≤ 16 then some .w16
  else if n ≤ 32 then some .w32
  else if n ≤ 64 then some .w64
  else if n ≤ 128 then some .w128
  else if n ≤ 256 then some .w256
  else if n ≤ 512 then some .w512
  else if n ≤ 1024 then some .w1024
  else if n ≤ 2048 then some .w2048
  else if n ≤ 4096 then some .w4096
  else if n ≤ 8192 then some .w8192
  else none

def joinInt (a b : IntTy) : Option IntTy :=
  match a.sign, b.sign with
  | .signed, .signed =>
      (widthForBits (Nat.max a.width.bits b.width.bits)).map fun w => ⟨.signed, w⟩
  | .unsigned, .unsigned =>
      (widthForBits (Nat.max a.width.bits b.width.bits)).map fun w => ⟨.unsigned, w⟩
  | .signed, .unsigned =>
      (widthForBits (Nat.max a.width.bits (b.width.bits + 1))).map fun w => ⟨.signed, w⟩
  | .unsigned, .signed =>
      (widthForBits (Nat.max b.width.bits (a.width.bits + 1))).map fun w => ⟨.signed, w⟩

def canRepresent (dst src : IntTy) : Bool :=
  match dst.sign, src.sign with
  | .signed, .signed => decide (src.width.bits ≤ dst.width.bits)
  | .unsigned, .unsigned => decide (src.width.bits ≤ dst.width.bits)
  | .signed, .unsigned => decide (src.width.bits < dst.width.bits)
  | .unsigned, .signed => false

def joinIsLossless (a b : IntTy) : Bool :=
  match joinInt a b with
  | none => true
  | some out => canRepresent out a && canRepresent out b

inductive FloatTy where
  | f32
  | f64
  | f128
  deriving Repr, DecidableEq

def FloatTy.rank : FloatTy → Nat
  | .f32 => 0
  | .f64 => 1
  | .f128 => 2

def FloatTy.precision : FloatTy → Nat
  | .f32 => 24
  | .f64 => 53
  | .f128 => 113

def joinFloat (a b : FloatTy) : FloatTy :=
  if a.rank ≥ b.rank then a else b

def canRepresentFloat (dst src : FloatTy) : Bool :=
  decide (src.rank ≤ dst.rank)

def joinFloatIsLossless (a b : FloatTy) : Bool :=
  canRepresentFloat (joinFloat a b) a && canRepresentFloat (joinFloat a b) b

inductive NumericTy where
  | int (ty : IntTy)
  | float (ty : FloatTy)
  deriving Repr, DecidableEq

def canRepresentNumeric : NumericTy → NumericTy → Bool
  | .int dst, .int src => canRepresent dst src
  | .float dst, .float src => canRepresentFloat dst src
  | .float dst, .int src => decide (src.exactBits ≤ dst.precision)
  | .int _, .float _ => false

def joinNumeric : NumericTy → NumericTy → Option NumericTy
  | .int a, .int b => (joinInt a b).map .int
  | .float a, .float b => some (.float (joinFloat a b))
  | .float f, .int i =>
      if i.exactBits ≤ f.precision then some (.float f) else none
  | .int i, .float f =>
      if i.exactBits ≤ f.precision then some (.float f) else none

theorem joinInt_idempotent (a : IntTy) : joinInt a a = some a := by
  rcases a with ⟨sign, width⟩
  cases sign <;> cases width <;> decide

theorem joinInt_commutative (a b : IntTy) : joinInt a b = joinInt b a := by
  rcases a with ⟨sa, wa⟩
  rcases b with ⟨sb, wb⟩
  cases sa <;> cases sb <;> cases wa <;> cases wb <;> decide

theorem joinInt_never_claims_lossy (a b : IntTy) : joinIsLossless a b = true := by
  rcases a with ⟨sa, wa⟩
  rcases b with ⟨sb, wb⟩
  cases sa <;> cases sb <;> cases wa <;> cases wb <;> decide

theorem joinFloat_idempotent (a : FloatTy) : joinFloat a a = a := by
  cases a <;> decide

theorem joinFloat_commutative (a b : FloatTy) : joinFloat a b = joinFloat b a := by
  cases a <;> cases b <;> decide

theorem joinFloat_never_claims_lossy (a b : FloatTy) :
    joinFloatIsLossless a b = true := by
  cases a <;> cases b <;> decide

theorem i32_u32_join_is_i64 :
    joinInt ⟨.signed, .w32⟩ ⟨.unsigned, .w32⟩ = some ⟨.signed, .w64⟩ := by
  decide

theorem i64_u64_join_is_i128 :
    joinInt ⟨.signed, .w64⟩ ⟨.unsigned, .w64⟩ = some ⟨.signed, .w128⟩ := by
  decide

theorem i8192_u8192_has_no_fixed_lossless_join :
    joinInt ⟨.signed, .w8192⟩ ⟨.unsigned, .w8192⟩ = none := by
  decide

theorem f32_f64_join_is_f64 : joinFloat .f32 .f64 = .f64 := by
  decide

theorem f64_widens_losslessly_to_f128 : canRepresentFloat .f128 .f64 = true := by
  decide

theorem f32_f64_numeric_join_is_f64 :
    joinNumeric (.float .f32) (.float .f64) = some (.float .f64) := by
  decide

end AILangProof.Numeric
