"""Runtime declaration service facade for LLVM codegen.

This module now composes category-focused mixins to keep declaration
logic split by responsibility while preserving the original
``RuntimeDecls`` API used by ``CodeGen`` and generators.
"""

from __future__ import annotations

from .runtime_decls_base import RuntimeDeclsBase
from .runtime_decls_file_io import RuntimeDeclsFileIOMixin
from .runtime_decls_sqlite_math import RuntimeDeclsSqliteMathMixin
from .runtime_decls_string_mem import RuntimeDeclsStringMemMixin
from .runtime_decls_thread_sync import RuntimeDeclsThreadSyncMixin


class RuntimeDecls(
    RuntimeDeclsStringMemMixin,
    RuntimeDeclsFileIOMixin,
    RuntimeDeclsThreadSyncMixin,
    RuntimeDeclsSqliteMathMixin,
    RuntimeDeclsBase,
):
    """Composed runtime declaration service."""
