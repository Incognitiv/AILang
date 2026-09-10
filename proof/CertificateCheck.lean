import AILangProof.TypedIR

namespace AILangProof.CertificateCheck

open AILangProof.Numeric
open AILangProof.Conversions
open AILangProof.TypedIR

abbrev Env := List (String × ScalarTy)

def lookupType (name : String) : Env → Option ScalarTy
  | [] => none
  | (entryName, entryType) :: rest =>
      if entryName = name then some entryType else lookupType name rest

def isFresh (name : String) (env : Env) : Bool :=
  match lookupType name env with
  | none => true
  | some _ => false

def parseScalarTy : String → Except String ScalarTy
  | "bool" => .ok .bool
  | "i8" => .ok (.numeric (.int ⟨.signed, .w8⟩))
  | "i16" => .ok (.numeric (.int ⟨.signed, .w16⟩))
  | "i32" => .ok (.numeric (.int ⟨.signed, .w32⟩))
  | "i64" => .ok (.numeric (.int ⟨.signed, .w64⟩))
  | "i128" => .ok (.numeric (.int ⟨.signed, .w128⟩))
  | "i256" => .ok (.numeric (.int ⟨.signed, .w256⟩))
  | "i512" => .ok (.numeric (.int ⟨.signed, .w512⟩))
  | "i1024" => .ok (.numeric (.int ⟨.signed, .w1024⟩))
  | "i2048" => .ok (.numeric (.int ⟨.signed, .w2048⟩))
  | "i4096" => .ok (.numeric (.int ⟨.signed, .w4096⟩))
  | "i8192" => .ok (.numeric (.int ⟨.signed, .w8192⟩))
  | "u8" => .ok (.numeric (.int ⟨.unsigned, .w8⟩))
  | "u16" => .ok (.numeric (.int ⟨.unsigned, .w16⟩))
  | "u32" => .ok (.numeric (.int ⟨.unsigned, .w32⟩))
  | "u64" => .ok (.numeric (.int ⟨.unsigned, .w64⟩))
  | "u128" => .ok (.numeric (.int ⟨.unsigned, .w128⟩))
  | "u256" => .ok (.numeric (.int ⟨.unsigned, .w256⟩))
  | "u512" => .ok (.numeric (.int ⟨.unsigned, .w512⟩))
  | "u1024" => .ok (.numeric (.int ⟨.unsigned, .w1024⟩))
  | "u2048" => .ok (.numeric (.int ⟨.unsigned, .w2048⟩))
  | "u4096" => .ok (.numeric (.int ⟨.unsigned, .w4096⟩))
  | "u8192" => .ok (.numeric (.int ⟨.unsigned, .w8192⟩))
  | "f32" => .ok (.numeric (.float .f32))
  | "f64" => .ok (.numeric (.float .f64))
  | "f128" => .ok (.numeric (.float .f128))
  | other => .error s!"unsupported certificate type: {other}"

def parseConversionKind : String → Except String ConversionKind
  | "identity" => .ok .identity
  | "lossless_widen" => .ok .losslessWiden
  | "checked" => .ok .checked
  | "explicit_lossy" => .ok .explicitLossy
  | "forbidden" => .ok .forbidden
  | other => .error s!"unknown conversion kind: {other}"

def asNumeric : ScalarTy → Except String NumericTy
  | .numeric ty => .ok ty
  | .bool => .error "binary certificate operand cannot be bool"

def validOperator (operator : String) : Bool :=
  operator == "add" || operator == "sub" || operator == "mul" || operator == "div"

structure CertState where
  env : Env
  returnType : ScalarTy
  startedInstructions : Bool
  returned : Bool


def validateRows : CertState → List String → Except String Unit
  | state, [] =>
      if state.returned then .ok () else .error "certificate function has no return"
  | state, line :: rest => do
      if state.returned then
        throw "certificate contains rows after return"
      match line.splitOn "\t" with
      | ["P", name, typeText] =>
          if state.startedInstructions then
            throw "parameter row appears after instructions"
          let ty ← parseScalarTy typeText
          if !isFresh name state.env then
            throw s!"duplicate SSA name: {name}"
          validateRows
            { state with env := (name, ty) :: state.env }
            rest
      | ["C", sourceName, sourceTypeText, resultName, resultTypeText, kindText] =>
          let sourceType ← parseScalarTy sourceTypeText
          let resultType ← parseScalarTy resultTypeText
          let kind ← parseConversionKind kindText
          if lookupType sourceName state.env != some sourceType then
            throw s!"conversion source is undefined or has the wrong type: {sourceName}"
          if !isFresh resultName state.env then
            throw s!"conversion result is not fresh: {resultName}"
          let inst : ConvertInst := ⟨sourceType, resultType, kind⟩
          if !inst.valid then
            throw s!"invalid conversion certificate: {sourceTypeText} -> {resultTypeText} as {kindText}"
          validateRows
            { state with
                env := (resultName, resultType) :: state.env
                startedInstructions := true }
            rest
      | ["B", operator, leftName, leftTypeText, rightName, rightTypeText, resultName, resultTypeText] =>
          if !validOperator operator then
            throw s!"unsupported binary operator in certificate: {operator}"
          let leftScalar ← parseScalarTy leftTypeText
          let rightScalar ← parseScalarTy rightTypeText
          let resultScalar ← parseScalarTy resultTypeText
          if lookupType leftName state.env != some leftScalar then
            throw s!"binary left operand is undefined or has the wrong type: {leftName}"
          if lookupType rightName state.env != some rightScalar then
            throw s!"binary right operand is undefined or has the wrong type: {rightName}"
          if !isFresh resultName state.env then
            throw s!"binary result is not fresh: {resultName}"
          let leftType ← asNumeric leftScalar
          let rightType ← asNumeric rightScalar
          let resultType ← asNumeric resultScalar
          let inst : BinaryInst := ⟨leftType, rightType, resultType⟩
          if !inst.valid then
            throw "binary operands and result are not type-uniform"
          validateRows
            { state with
                env := (resultName, resultScalar) :: state.env
                startedInstructions := true }
            rest
      | ["R", valueName, valueTypeText] =>
          let valueType ← parseScalarTy valueTypeText
          if lookupType valueName state.env != some valueType then
            throw s!"return value is undefined or has the wrong type: {valueName}"
          if valueType != state.returnType then
            throw "return value type does not match function return type"
          validateRows
            { state with
                startedInstructions := true
                returned := true }
            rest
      | fields =>
          throw s!"malformed certificate row with {fields.length} field(s): {line}"


def validateCertificate (content : String) : Except String Unit := do
  let lines := (content.splitOn "\n").filter (fun line => !line.isEmpty)
  match lines with
  | header :: functionRow :: rest =>
      if header.splitOn "\t" != ["AILANG_TYPED_IR_CERTIFICATE", "1"] then
        throw "unsupported or malformed typed IR certificate header"
      match functionRow.splitOn "\t" with
      | ["F", functionName, returnTypeText] =>
          if functionName.isEmpty then
            throw "certificate function name cannot be empty"
          let returnType ← parseScalarTy returnTypeText
          validateRows
            { env := []
              returnType := returnType
              startedInstructions := false
              returned := false }
            rest
      | _ => throw "malformed certificate function row"
  | _ => throw "typed IR certificate is incomplete"

end AILangProof.CertificateCheck

open AILangProof.CertificateCheck

def main (args : List String) : IO UInt32 := do
  match args with
  | [path] =>
      let content ← IO.FS.readFile (System.FilePath.mk path)
      match validateCertificate content with
      | .ok () =>
          IO.println "Lean typed IR certificate: ACCEPT"
          return 0
      | .error message =>
          IO.eprintln s!"Lean typed IR certificate: REJECT: {message}"
          return 1
  | _ =>
      IO.eprintln "usage: ailangProofCertificateCheck <certificate>"
      return 2
