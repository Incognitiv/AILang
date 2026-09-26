# Correctness before a native compiler migration — 2026-09-25

Base commit: `6eeec77d185bb5aa890f7161652a0d331a7898ce`.
Scope: verifier-cache storage only. No compiler rewrite, daemon, launcher, ABI,
backend default, gate threshold or source-language change is included.

## Reproduced and fixed

The existing cache expected every decoded JSON value to be a dictionary and
`cached_at` to be a usable number. Wrong-shaped but valid JSON, invalid UTF-8,
very deeply nested input and nonnumeric timestamps could raise exceptions.
Future and NaN timestamps could bypass expiry. Nondictionary results could
escape validation and break later consumers.

Writes opened the final path with truncation before serialization completed.
Serialization failure could both raise and destroy the previous entry. Readers
could see partial JSON during concurrent writes. A preexisting symlink could
redirect the write into another file. Failure to create the optional cache
could prevent checks from starting. Cleanup stopped after one inaccessible file.

`verifier/cache_storage.py` now validates shape, encoding, finite timestamps,
expiry and dictionary results, with an 8 MiB entry-size limit. NaN/Infinity JSON
extensions are rejected on reads and writes. Invalid entries are cache misses,
not successful verification. Serialization completes before publication.

Publication uses a unique same-directory temporary file followed by `os.replace`.
Readers see a complete old or new entry on filesystems supporting atomic replace.
Write/replace failures preserve the previous entry, and temporary files are
removed best-effort. No per-entry fsync is introduced for this disposable cache.
Lookup does not unlink an expired path that a concurrent writer might replace.
Explicit cleanup continues past individual inaccessible entries. Concurrent
cleanup is best-effort and can still cause harmless cache misses.

## Executed local evidence

The original 3306-byte `verifier/cache.py` was reconstructed from the GitHub
connector and verified as Git blob `14b3b63c16c71235953f137a21fc63bcdfbb0202`.
The unchanged 593-byte verifier package initializer was likewise checked against
Git blob `2c004b5987b1c9fdbb3e48edd20d24f22187e1e8`.

The same regression file ran against the original cache class and the patch:

- Original: 31 failed, 8 passed.
- Patched: 39 passed.

Two size-budget tests use the new storage module's configurable test constant;
the original implementation ignores that module and fails those assertions.
The tests exercise real temporary files and concurrent writers. Publication
failures, permission errors and small byte budgets are explicitly injected.
No external verifier tools are mocked as if they had run successfully.

Environment: Linux / Python 3.13.5 / pytest 9.0.2. Only the scoped source subset
was available locally. Full compiler/native/Lean results cannot be inferred from
these unit tests. CI must establish results for the exact published revision.

## Explicitly not solved

The existing semantic key is still code plus preset. Storage validation does not
make reuse valid across changed tool versions, import graphs, file identities or
configuration. This separate cache-context finding remains open; no new cache
use is enabled. The standard Golden Gate does not enable the result cache.
These files are not authenticated evidence against an attacker with write access.

## Native-compiler boundary

`tools/package_ailang.py` already contains Nuitka, PyInstaller and Cython builds.
Its Cython path translates only the `ailang.py` launcher with `--embed`, then links
Python embed flags. That is not conversion of the full compiler to Python-free
native logic. PyInstaller bundles an interpreter; Nuitka compiles Python logic
but retains CPython runtime services; ordinary Cython embedding also needs Python.

A native compiler without that dependency must move its actual frontend,
semantic analysis and backend adapter to code that does not need CPython objects
or the Python C API. LLVM can remain a native backend. Do not describe packaging,
a native launcher, or an embedded interpreter as completion of that migration.
First preserve a tested semantic reference and error behavior. Then compare the
native implementation against it on the same programs, including failures.

References:
- https://pyinstaller.org/en/stable/operating-mode.html
- https://nuitka.net/user-documentation/tips.html
- https://docs.cython.org/en/latest/src/tutorial/embedding.html
- https://docs.python.org/3/library/os.html#os.replace
