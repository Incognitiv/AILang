import Std

/- An independent source-value specification. Inputs are Unicode code points
   INSIDE the quotes, before escape decoding; outputs are decoded code points.
   This uses structural recursion on input, not Python regexes or AST values.
   Numeric escapes have the current code-point (not raw UTF-8 byte) semantics. -/
namespace AILangProof.StringLiterals

def hexDigit (n : Nat) : Option Nat :=
  if 48 ≤ n ∧ n ≤ 57 then some (n - 48)
  else if 65 ≤ n ∧ n ≤ 70 then some (n - 55)
  else if 97 ≤ n ∧ n ≤ 102 then some (n - 87)
  else none

def hexPair (a b : Nat) : Option Nat := do
  let x ← hexDigit a
  let y ← hexDigit b
  pure (16 * x + y)

def hexQuad (a b c d : Nat) : Option Nat := do
  let x ← hexPair a b
  let y ← hexPair c d
  pure (256 * x + y)

def simpleEscape (n : Nat) : List Nat :=
  match n with
  | 110 => [10]
  | 116 => [9]
  | 114 => [13]
  | 48 => [0]
  | 92 => [92]
  | 34 => [34]
  | 39 => [39]
  | _ => [92, n]

def decode : List Nat → List Nat
  | 92 :: 120 :: a :: b :: tail =>
    match hexPair a b with
    | some n => n :: decode tail
    | none => 92 :: 120 :: decode (a :: b :: tail)
  | 92 :: 117 :: a :: b :: c :: d :: tail =>
    match hexQuad a b c d with
    | some n => n :: decode tail
    | none => 92 :: 117 :: decode (a :: b :: c :: d :: tail)
  | 92 :: n :: tail => simpleEscape n ++ decode tail
  | n :: tail => n :: decode tail
  | [] => []

-- These laws quantify over every suffix. Generated backslashes are output,
-- never new escape introducers. No finite test list is hidden in these laws.
theorem escapedBackslash (tail : List Nat) :
    decode (92 :: 92 :: tail) = 92 :: decode tail := by
  simp [decode, simpleEscape]

theorem hexadecimalBackslash (tail : List Nat) :
    decode (92 :: 120 :: 53 :: 99 :: tail) = 92 :: decode tail := by
  simp [decode, hexPair, hexDigit]

theorem unicodeBackslash (tail : List Nat) :
    decode (92 :: 117 :: 48 :: 48 :: 53 :: 99 :: tail) = 92 :: decode tail := by
  simp [decode, hexQuad, hexPair, hexDigit]

-- Regression laws independent of any emitted compiler certificate.
example : decode [92, 92, 110] = [92, 110] := by decide
example : decode [92, 120, 53, 99, 110] = [92, 110] := by decide
example : decode [92, 117, 48, 49, 55, 99] = [380] := by decide
example : decode [92, 48, 66, 65, 67, 75, 83, 76, 65, 83, 72, 92, 48] =
    [0, 66, 65, 67, 75, 83, 76, 65, 83, 72, 0] := by decide

end AILangProof.StringLiterals
