"""Diagnostics and reporting helpers for `#cinclude` directives."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from diagnostics.diagnostics_models import Diagnostic
from target_info import os_from_platform, target_matches


@dataclass(frozen=True, slots=True)
class CIncludeDirective:
    """One `#cinclude` directive visible to a backend/report."""

    path: str
    system: bool
    target_os: str | None = None
    active: bool = True
    source_file: str | None = None
    line: int = 1
    column: int = 1
    raw: str = ""
    resolved_path: str | None = None
    exists: bool | None = None


@dataclass(frozen=True, slots=True)
class CIncludeDiagnostic:
    """Structured diagnostic for a `#cinclude` directive."""

    severity: str
    kind: str
    message: str
    suggestion: str
    directive: CIncludeDirective

    def to_diagnostic(self) -> Diagnostic:
        """Convert to the shared diagnostics model."""
        return Diagnostic(
            self.directive.line,
            self.directive.column,
            self.message,
            suggestion=self.suggestion,
            severity=self.severity,
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON/report friendly diagnostic row."""
        return {
            "severity": self.severity,
            "kind": self.kind,
            "message": self.message,
            "suggestion": self.suggestion,
            "line": self.directive.line,
            "column": self.directive.column,
            "path": self.directive.path,
            "system": self.directive.system,
            "target_os": self.directive.target_os,
            "active": self.directive.active,
            "source_file": self.directive.source_file,
            "resolved_path": self.directive.resolved_path,
        }


def format_cinclude(
    directive: CIncludeDirective,
    *,
    include_target: bool = False,
) -> str:
    """Format a directive as it would appear in source."""
    if directive.system:
        spelling = f"<{directive.path}>"
    else:
        spelling = f'"{directive.path}"'
    if include_target and directive.target_os:
        return f"{directive.target_os} {spelling}"
    return spelling


def cinclude_backend_support_payload() -> dict[str, dict[str, str]]:
    """Return the stable support matrix for `#cinclude` consumers."""
    return {
        "c_backend": {
            "status": "emitted",
            "detail": "Emits active directives as real C #include lines.",
        },
        "header_generation": {
            "status": "emitted",
            "detail": "Emits active directives into generated C headers.",
        },
        "llvm_aot": {
            "status": "ignored_header_import",
            "detail": "Does not parse C headers; use extern declarations and #link.",
        },
        "jit": {
            "status": "ignored_header_import",
            "detail": "Does not parse C headers; use extern declarations and #link.",
        },
    }


def _resolve_local_header(
    path: str,
    source_file: str | None,
) -> tuple[str | None, bool | None]:
    """Resolve a quoted include relative to the owning AILang file."""
    if not source_file or not path:
        return None, None
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = Path(source_file).parent / candidate
    try:
        resolved = candidate.resolve()
    except OSError:
        resolved = candidate.absolute()
    return str(resolved), resolved.exists()


def cinclude_directive_from_node(
    node: object,
    *,
    current_os: str | None = None,
    fallback_source_file: str | None = None,
) -> CIncludeDirective:
    """Build a directive record from a parsed AST node."""
    target_os = getattr(node, "target_os", None)
    active_os = current_os or os_from_platform()
    active = target_matches(target_os, active_os)
    system = bool(getattr(node, "is_system", False))
    source_file = getattr(node, "_source_file", None) or fallback_source_file
    path = str(getattr(node, "path", ""))
    resolved_path: str | None = None
    exists: bool | None = None
    if not system:
        resolved_path, exists = _resolve_local_header(path, source_file)
    return CIncludeDirective(
        path=path,
        system=system,
        target_os=target_os,
        active=active,
        source_file=source_file,
        line=int(getattr(node, "line", 1) or 1),
        column=int(getattr(node, "column", 1) or 1),
        raw=str(getattr(node, "raw", "") or ""),
        resolved_path=resolved_path,
        exists=exists,
    )


def cinclude_directive_payload(directive: CIncludeDirective) -> dict[str, Any]:
    """Return the JSON/report representation of one directive."""
    active_status = "active" if directive.active else "inactive"
    per_backend = {
        "c_backend": "emitted" if directive.active else "inactive",
        "header_generation": "emitted" if directive.active else "inactive",
        "llvm_aot": "ignored_header_import" if directive.active else "inactive",
        "jit": "ignored_header_import" if directive.active else "inactive",
    }
    return {
        "path": directive.path,
        "system": directive.system,
        "target_os": directive.target_os,
        "active": directive.active,
        "status": active_status,
        "spelling": format_cinclude(directive),
        "source_file": directive.source_file,
        "line": directive.line,
        "column": directive.column,
        "resolved_path": directive.resolved_path,
        "exists": directive.exists,
        "backend_support": per_backend,
    }


def collect_cinclude_directives(
    source_file: str,
    *,
    active_only: bool = True,
    target_os: str | None = None,
) -> list[CIncludeDirective]:
    """Collect local and imported `#cinclude` directives for a source file."""
    try:
        from parser import ast as A
        from parser.parser import Parser

        from lexer.scan import tokenize
        from transpiler.import_resolver import ImportResolver
    except ImportError:
        return []

    try:
        with open(source_file, "r", encoding="utf-8") as f:
            source = f.read()
        parser = Parser(tokenize(source))
        nodes = ImportResolver().run(parser.parse_program(), source_file)
    except (OSError, RuntimeError, SyntaxError, ValueError):
        return []

    current_os = target_os or os_from_platform()
    result: list[CIncludeDirective] = []
    seen: set[tuple[str, bool, str | None, str | None]] = set()
    for node in nodes:
        if not isinstance(node, A.CInclude):
            continue
        directive = cinclude_directive_from_node(
            node,
            current_os=current_os,
            fallback_source_file=source_file,
        )
        if active_only and not directive.active:
            continue
        key = (
            directive.path,
            directive.system,
            directive.target_os,
            directive.source_file,
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(directive)
    return result


def diagnose_cinclude_directives(
    directives: list[CIncludeDirective],
    *,
    include_inactive: bool = False,
) -> list[CIncludeDiagnostic]:
    """Build semantic diagnostics for parsed include directives."""
    diagnostics: list[CIncludeDiagnostic] = []
    for directive in directives:
        if not directive.active and not include_inactive:
            continue
        if not directive.path.strip():
            diagnostics.append(
                CIncludeDiagnostic(
                    severity="error",
                    kind="cinclude.empty_path",
                    message="#cinclude is missing a header path.",
                    suggestion='Use #cinclude <header.h> or #cinclude "local.h".',
                    directive=directive,
                )
            )
            continue
        if (
            directive.active
            and not directive.system
            and directive.exists is False
            and directive.resolved_path
        ):
            diagnostics.append(
                CIncludeDiagnostic(
                    severity="warning",
                    kind="cinclude.local_header_not_found",
                    message=(
                        "#cinclude local header was not found relative to "
                        "the owning AILang source file."
                    ),
                    suggestion=(
                        "Create the header at "
                        f"{directive.resolved_path} or provide it through a "
                        "native compiler include path."
                    ),
                    directive=directive,
                )
            )
    return diagnostics


def collect_cinclude_diagnostics(
    source_file: str,
    *,
    target_os: str | None = None,
    include_inactive: bool = False,
) -> list[Diagnostic]:
    """Collect shared-model diagnostics for `#cinclude` directives."""
    directives = collect_cinclude_directives(
        source_file,
        active_only=False,
        target_os=target_os,
    )
    return [
        row.to_diagnostic()
        for row in diagnose_cinclude_directives(
            directives,
            include_inactive=include_inactive,
        )
    ]


def collect_cinclude_include_dirs(
    source_file: str,
    *,
    target_os: str | None = None,
) -> list[str]:
    """Return source directories needed for active quoted `#cinclude` headers."""
    result: list[str] = []
    seen: set[str] = set()
    for directive in collect_cinclude_directives(
        source_file,
        active_only=True,
        target_os=target_os,
    ):
        if directive.system:
            continue
        if not directive.source_file:
            continue
        header_path = Path(directive.path)
        if header_path.is_absolute():
            continue
        try:
            include_dir = str(Path(directive.source_file).parent.resolve())
        except OSError:
            include_dir = str(Path(directive.source_file).parent.absolute())
        if include_dir in seen:
            continue
        seen.add(include_dir)
        result.append(include_dir)
    return result


def cinclude_backend_warning(
    directives: list[CIncludeDirective],
    backend_name: str,
) -> str:
    """Build the warning text for backends that cannot consume C headers."""
    if not directives:
        return ""
    includes = ", ".join(
        format_cinclude(row, include_target=True) for row in directives
    )
    return (
        f"#cinclude is C-backend-only; {backend_name} cannot import C headers "
        f"directly. Ignored headers: {includes}. Declare needed symbols with "
        "extern fn/extern var and link native objects or libraries with #link."
    )


def emit_cinclude_backend_warning(
    source_file: str,
    backend_name: str,
    *,
    stream: TextIO | None = None,
) -> bool:
    """Print a non-blocking warning for LLVM/JIT header limitations."""
    directives = collect_cinclude_directives(source_file)
    warning = cinclude_backend_warning(directives, backend_name)
    if not warning:
        return False
    output = stream if stream is not None else sys.stderr
    print(f"Warning: {warning}", file=output)
    return True
