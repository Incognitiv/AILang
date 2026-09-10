import AILangProof.Numeric

open AILangProof.Numeric

private def signs : Array IntSign := #[.signed, .unsigned]

private def widths : Array IntWidth := #[
  .w8, .w16, .w32, .w64, .w128, .w256, .w512, .w1024, .w2048, .w4096, .w8192
]

private def signPrefix : IntSign → String
  | .signed => "i"
  | .unsigned => "u"

private def widthText : IntWidth → String
  | .w8 => "8"
  | .w16 => "16"
  | .w32 => "32"
  | .w64 => "64"
  | .w128 => "128"
  | .w256 => "256"
  | .w512 => "512"
  | .w1024 => "1024"
  | .w2048 => "2048"
  | .w4096 => "4096"
  | .w8192 => "8192"

private def typeText (ty : IntTy) : String :=
  signPrefix ty.sign ++ widthText ty.width

private def resultText : Option IntTy → String
  | none => "none"
  | some ty => typeText ty

def main : IO Unit := do
  for signA in signs do
    for widthA in widths do
      let a : IntTy := ⟨signA, widthA⟩
      for signB in signs do
        for widthB in widths do
          let b : IntTy := ⟨signB, widthB⟩
          IO.println s!"{typeText a}\t{typeText b}\t{resultText (joinInt a b)}"
