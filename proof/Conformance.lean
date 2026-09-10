import AILangProof.Numeric
import AILangProof.Conversions

open AILangProof.Numeric
open AILangProof.Conversions

private def intType (sign : IntSign) (width : IntWidth) : NumericTy :=
  .int ⟨sign, width⟩

private def numericTypes : Array NumericTy := #[
  intType .signed .w8,
  intType .signed .w16,
  intType .signed .w32,
  intType .signed .w64,
  intType .signed .w128,
  intType .signed .w256,
  intType .signed .w512,
  intType .signed .w1024,
  intType .signed .w2048,
  intType .signed .w4096,
  intType .signed .w8192,
  intType .unsigned .w8,
  intType .unsigned .w16,
  intType .unsigned .w32,
  intType .unsigned .w64,
  intType .unsigned .w128,
  intType .unsigned .w256,
  intType .unsigned .w512,
  intType .unsigned .w1024,
  intType .unsigned .w2048,
  intType .unsigned .w4096,
  intType .unsigned .w8192,
  .float .f32,
  .float .f64,
  .float .f128
]

private def scalarTypes : Array ScalarTy := #[
  .bool,
  .numeric (intType .signed .w8),
  .numeric (intType .signed .w16),
  .numeric (intType .signed .w32),
  .numeric (intType .signed .w64),
  .numeric (intType .signed .w128),
  .numeric (intType .signed .w256),
  .numeric (intType .signed .w512),
  .numeric (intType .signed .w1024),
  .numeric (intType .signed .w2048),
  .numeric (intType .signed .w4096),
  .numeric (intType .signed .w8192),
  .numeric (intType .unsigned .w8),
  .numeric (intType .unsigned .w16),
  .numeric (intType .unsigned .w32),
  .numeric (intType .unsigned .w64),
  .numeric (intType .unsigned .w128),
  .numeric (intType .unsigned .w256),
  .numeric (intType .unsigned .w512),
  .numeric (intType .unsigned .w1024),
  .numeric (intType .unsigned .w2048),
  .numeric (intType .unsigned .w4096),
  .numeric (intType .unsigned .w8192),
  .numeric (.float .f32),
  .numeric (.float .f64),
  .numeric (.float .f128)
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

private def floatText : FloatTy → String
  | .f32 => "f32"
  | .f64 => "f64"
  | .f128 => "f128"

private def typeText : NumericTy → String
  | .int ty => signPrefix ty.sign ++ widthText ty.width
  | .float ty => floatText ty

private def scalarText : ScalarTy → String
  | .bool => "bool"
  | .numeric ty => typeText ty

private def resultText : Option NumericTy → String
  | none => "none"
  | some ty => typeText ty

private def conversionText : ConversionKind → String
  | .identity => "identity"
  | .losslessWiden => "lossless_widen"
  | .checked => "checked"
  | .explicitLossy => "explicit_lossy"
  | .forbidden => "forbidden"

def main : IO Unit := do
  for a in numericTypes do
    for b in numericTypes do
      IO.println s!"J\t{typeText a}\t{typeText b}\t{resultText (joinNumeric a b)}"
  for source in scalarTypes do
    for target in scalarTypes do
      IO.println s!"C\t{scalarText source}\t{scalarText target}\t{conversionText (classifyConversion source target)}"
