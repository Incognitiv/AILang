"""CLI support for writing generated C source without compiling it."""

from __future__ import annotations

from cli.compilation import default_emit_c_output_path


def _output_arg(argv: list[str]) -> str | None:
    for i, arg in enumerate(argv):
        if arg == "-o" and i + 1 < len(argv):
            return argv[i + 1]
    return None


def maybe_emit_c_source(argv: list[str], source_file: str) -> int | None:
    """Handle ``--emit-c`` if present.

    Returns an exit code when handled, otherwise ``None``.
    """
    if "--emit-c" not in argv:
        return None

    from transpiler.core import transpile_file as transpile_to_c

    output_c = _output_arg(argv) or str(default_emit_c_output_path(source_file))
    try:
        transpile_to_c(source_file, output_c)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Error: C source emission failed: {exc}")
        return 1
    print(f"C source written to: {output_c}")
    return 0
