"""
RuntimeNeeds — explicit data container for runtime-feature usage.

The first step in the "mixin-monolith -> services + passes" refactor
described in New Path.md (2026-05-04). Today, ``CTranspiler`` carries
``self.used_helpers`` (a Set[str] of helper-name strings) and a handful
of bool flags (``_needs_threading``, ``_needs_atomics``, ...) that the
helper-scan pass populates and the runtime-emit pass reads. They
travel as scattered instance attributes on a god object; nothing
documents the contract between producer and consumer.

This module turns that contract into a single dataclass. The migration
is incremental: ``HelperScanMixin`` will populate **both** ``self.used_helpers``
AND ``self.runtime_needs`` (parallel write), the corpus diff verifies
they stay in sync, then consumers flip one at a time from the loose
attributes to the dataclass. When all consumers have flipped, the loose
attributes can be deleted.

Why a dataclass and not a TypedDict / dict[str, bool]?

- ``slots=True`` shaves the per-instance dict overhead (one flag is one
  attribute, not one hash-table entry).
- Default values are explicit and centralized.
- Field access is a real attribute reference, so mypy / pyright catch
  typos at edit time.
- Equality / repr come for free, which makes the corpus diff harness
  easier ("did this scan produce the same RuntimeNeeds as last run").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Set

RUNTIME_FAMILY_NAMES: tuple[str, ...] = (
    "string",
    "array",
    "dict",
    "file",
    "sqlite",
    "thread",
    "socket",
    "safe_arith",
    "leak_tracker",
)

_STRING_HELPERS: frozenset[str] = frozenset(
    {
        "strlen",
        "i64_decimal_len",
        "char_at",
        "unsafe_char_at",
        "int_to_str",
        "chr",
        "substr",
        "concat",
        "strcat",
        "strcat_n",
        "split",
        "split_ints",
        "str_array",
        "index_of",
        "startswith",
        "endswith",
        "str_replace",
        "streq_lit",
        "parse_int",
        "base_conv",
        "input",
    }
)
_ARRAY_HELPERS: frozenset[str] = frozenset(
    {"safe_array", "dynamic_array", "str_array", "split", "split_ints"}
)
_FILE_HELPERS: frozenset[str] = frozenset(
    {"file_io", "fileops", "fd", "cmdline", "input"}
)
_SAFE_ARITH_HELPERS: frozenset[str] = frozenset(
    {"safe_add", "safe_sub", "safe_mul", "safe_div", "safe_shift", "safe_array"}
)


@dataclass(slots=True)
class RuntimeNeeds:
    """Which runtime-helper categories does the program actually use?

    Produced by the helper-scan pass; consumed by the C and LLVM emit
    passes to gate which fragments they include in the output.

    The ``helpers`` set carries the fine-grained string keys (e.g.
    "strlen", "ailang_strcat", "tcp_recv"); the bool flags carry the
    coarse categories that any single helper inclusion implies (a tcp
    builtin sets ``threading=False, sockets`` -- though we currently
    fold sockets into helpers only).

    Today the bool flags duplicate information that ``helpers`` already
    encodes. We keep them explicit because:

    1. Some flags are set by code-walks, not by call-name lookup
       (``exceptions`` from try/catch nodes, ``inline_asm`` from
       A.InlineAsm) -- those will never be in ``helpers``.
    2. The emit consumers ask coarse questions (``if needs.threading``)
       far more often than fine ones (``if "thread_create" in helpers``).
    """

    # Fine-grained helper-name set. Each entry is a key into the C
    # transpiler's runtime-emit dispatch (e.g. _CALL_HELPER_MAP values
    # like "strlen", "string", "math", "sockets").
    helpers: Set[str] = field(default_factory=set)

    # Coarse category flags. These map 1:1 onto the existing CTranspiler
    # bool attributes; the migration just renames the storage without
    # changing semantics.
    arrays: bool = False
    dicts: bool = False
    dynamic_arrays: bool = False
    threading: bool = False  # spawn / join support
    atomics: bool = False
    channels: bool = False
    inline_asm: bool = False
    sync: bool = False  # mutex / condvar / rwlock primitives
    exceptions: bool = False  # try / catch / throw
    stream_cleanup: bool = False

    # Spawn-target metadata: function-name -> list of AILang param-type
    # names. Populated by the helper scanner for every `spawn fn(args)`
    # site whose target is a user-defined function. The runtime-emit
    # phase consumes this to generate one box-struct + thunk per unique
    # target so spawn(c, db) can pass real arguments across the thread.
    spawn_targets: Dict[str, List[str]] = field(default_factory=dict)

    def family_flags(self) -> Dict[str, bool]:
        """Coarse runtime-family classification for reports/audits."""
        helpers = self.helpers
        flags: Dict[str, bool] = {
            "string": bool(helpers.intersection(_STRING_HELPERS)),
            "array": self.arrays
            or self.dynamic_arrays
            or bool(helpers.intersection(_ARRAY_HELPERS)),
            "dict": self.dicts or ("dict" in helpers),
            "file": bool(helpers.intersection(_FILE_HELPERS)),
            "sqlite": "sqlite" in helpers,
            "thread": self.threading or self.sync or bool(self.spawn_targets),
            "socket": "sockets" in helpers,
            "safe_arith": bool(helpers.intersection(_SAFE_ARITH_HELPERS)),
            # C backend always emits the leak-report helper in safety runtime.
            "leak_tracker": True,
        }
        for name in RUNTIME_FAMILY_NAMES:
            flags.setdefault(name, False)
        return flags

    def helper_counts_by_family(self) -> Dict[str, int]:
        """Count helper keys grouped into coarse runtime families."""
        helpers = set(self.helpers)
        counts = {
            "string": len(helpers.intersection(_STRING_HELPERS)),
            "array": len(helpers.intersection(_ARRAY_HELPERS)),
            "dict": 1 if "dict" in helpers else 0,
            "file": len(helpers.intersection(_FILE_HELPERS)),
            "sqlite": 1 if "sqlite" in helpers else 0,
            "thread": len(
                helpers.intersection({"threading", "threading_utils", "sync"})
            )
            + (1 if self.threading else 0),
            "socket": 1 if "sockets" in helpers else 0,
            "safe_arith": len(helpers.intersection(_SAFE_ARITH_HELPERS)),
            "leak_tracker": 1,
        }
        return {name: int(counts.get(name, 0)) for name in RUNTIME_FAMILY_NAMES}
