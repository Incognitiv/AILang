import Lake
open Lake DSL

package AILangProof where
  version := v!"0.1.0"

lean_lib AILangProof

@[default_target]
lean_exe ailangProofReport where
  root := `Main
