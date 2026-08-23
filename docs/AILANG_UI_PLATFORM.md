# AILang UI Platform

AILang keeps the UI platform contract in the repository so UI examples do not
depend on local machine paths or archived downloads.

The canonical historical UI DSL corpus is:

`archived\source-cruft\Desktop Experiment`

Current backend-facing code lives under `source/ui`, while public UI primitives
live under `stdlib/ui`. New UI tests should point to these repository-relative
paths only.
