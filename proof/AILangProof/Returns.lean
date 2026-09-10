import Std
import AILangProof.Numeric

namespace AILangProof.Returns

open AILangProof.Numeric

inductive ValueTy where
  | bool
  | string
  | ptr
  | int (ty : IntTy)
  deriving Repr, DecidableEq

inductive ReturnTy where
  | void
  | value (ty : ValueTy)
  deriving Repr, DecidableEq

inductive Reject where
  | mixedBareValue
  | fallthrough
  | incompatibleValues
  | voidReturnsValue
  | nonVoidBare
  deriving Repr, DecidableEq

structure ReturnSummary where
  hasBare : Bool
  values : List ValueTy
  allPathsValueOrThrow : Bool
  deriving Repr, DecidableEq

structure FunctionSpec where
  declared : Option ReturnTy
  summary : ReturnSummary
  deriving Repr, DecidableEq

abbrev Join := ValueTy → ValueTy → Option ValueTy

def joinValue : Join
  | .bool, .bool => some .bool
  | .string, .string => some .string
  | .ptr, .ptr => some .ptr
  | .int a, .int b => (joinInt a b).map .int
  | _, _ => none

def foldJoin (join : Join) (acc : ValueTy) : List ValueTy → Option ValueTy
  | [] => some acc
  | ty :: rest =>
      match join acc ty with
      | none => none
      | some next => foldJoin join next rest

def inferReturn (join : Join) (summary : ReturnSummary) : Except Reject ReturnTy :=
  match summary.values with
  | [] => .ok .void
  | first :: rest =>
      if summary.hasBare then
        .error .mixedBareValue
      else if !summary.allPathsValueOrThrow then
        .error .fallthrough
      else
        match foldJoin join first rest with
        | none => .error .incompatibleValues
        | some ty => .ok (.value ty)

def checkExplicit (summary : ReturnSummary) : ReturnTy → Except Reject ReturnTy
  | .void =>
      if summary.values.isEmpty then .ok .void else .error .voidReturnsValue
  | .value ty =>
      if summary.hasBare then
        .error .nonVoidBare
      else if !summary.allPathsValueOrThrow then
        .error .fallthrough
      else
        .ok (.value ty)

def checkReturn (join : Join) (fn : FunctionSpec) : Except Reject ReturnTy :=
  match fn.declared with
  | none => inferReturn join fn.summary
  | some ty => checkExplicit fn.summary ty

def InferredSafe (summary : ReturnSummary) : ReturnTy → Prop
  | .void => summary.values = []
  | .value _ =>
      summary.values ≠ [] ∧
      summary.hasBare = false ∧
      summary.allPathsValueOrThrow = true

theorem void_is_not_a_value (ty : ValueTy) : ReturnTy.void ≠ ReturnTy.value ty := by
  intro h
  cases h

theorem mixed_value_and_bare_is_rejected
    (join : Join) (head : ValueTy) (tail : List ValueTy) (paths : Bool) :
    inferReturn join
      { hasBare := true, values := head :: tail, allPathsValueOrThrow := paths } =
      .error .mixedBareValue := by
  simp [inferReturn]

theorem inferred_value_fallthrough_is_rejected
    (join : Join) (head : ValueTy) (tail : List ValueTy) :
    inferReturn join
      { hasBare := false, values := head :: tail, allPathsValueOrThrow := false } =
      .error .fallthrough := by
  simp [inferReturn]

theorem explicit_void_cannot_return_value
    (head : ValueTy) (tail : List ValueTy) (bare paths : Bool) :
    checkExplicit
      { hasBare := bare, values := head :: tail, allPathsValueOrThrow := paths }
      .void = .error .voidReturnsValue := by
  simp [checkExplicit]

theorem explicit_nonvoid_cannot_use_bare_return
    (ty : ValueTy) (values : List ValueTy) (paths : Bool) :
    checkExplicit
      { hasBare := true, values := values, allPathsValueOrThrow := paths }
      (.value ty) = .error .nonVoidBare := by
  simp [checkExplicit]

theorem inferred_void_has_no_values
    (join : Join) (summary : ReturnSummary)
    (h : inferReturn join summary = .ok .void) :
    summary.values = [] := by
  cases hv : summary.values with
  | nil => exact hv
  | cons first rest =>
      cases hb : summary.hasBare <;>
      cases hp : summary.allPathsValueOrThrow <;>
      cases hj : foldJoin join first rest <;>
      simp [inferReturn, hv, hb, hp, hj] at h

theorem inferred_nonvoid_has_sound_shape
    (join : Join) (summary : ReturnSummary) (ty : ValueTy)
    (h : inferReturn join summary = .ok (.value ty)) :
    summary.values ≠ [] ∧
    summary.hasBare = false ∧
    summary.allPathsValueOrThrow = true := by
  cases hv : summary.values with
  | nil =>
      simp [inferReturn, hv] at h
  | cons first rest =>
      constructor
      · simp [hv]
      · cases hb : summary.hasBare <;>
        cases hp : summary.allPathsValueOrThrow <;>
        cases hj : foldJoin join first rest <;>
        simp [inferReturn, hv, hb, hp, hj] at h ⊢

theorem inferred_return_sound
    (join : Join) (summary : ReturnSummary) (ret : ReturnTy)
    (h : inferReturn join summary = .ok ret) :
    InferredSafe summary ret := by
  cases ret with
  | void =>
      exact inferred_void_has_no_values join summary h
  | value ty =>
      exact inferred_nonvoid_has_sound_shape join summary ty h

end AILangProof.Returns
