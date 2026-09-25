"""Automatic native-build selection behind the ordinary AILang CLI."""

from __future__ import annotations

import subprocess
from pathlib import Path

from cli.cinclude_diagnostics import emit_cinclude_backend_warning
from cli.compilation import compile_to_native as _file_compile_to_native
from cli.link_flags import _extract_ailang_link_flags, _merge_link_flags
from cli.llvm_diagnostics import _detect_llvm_link_flags
from codegen.codegen_errors import CodeGenError
from compiler.memory_link import (
    link_object_in_memory,
    memory_link_flags_supported,
    memory_link_supported,
    publish_executable,
    validate_output_paths,
)
from runtime.modes import CompilationContext, CompilationMode
from runtime.phases import Phase


def _memory_eligible(
    opt_level: int | str,
    native_toolchain: str,
    debug_info: bool,
    profiles: tuple[str, ...],
) -> bool:
    """Honor explicit platform/toolchain requests instead of guessing equivalents."""
    return (
        native_toolchain in {"auto", "default", ""}
        and isinstance(opt_level, int)
        and opt_level in range(4)
        and not debug_info
        and not any(profiles)
        and CompilationContext.get_mode() == CompilationMode.HOSTED
        and memory_link_supported()
    )


def _memory_build(source_file: str, output_exe: str, opt_level: int) -> bool | None:
    """None means unsupported link options; compilation errors never retry."""
    from codegen.fast_jit import compile_to_ir_fast
    from compiler.memory_native import compile_ir_object

    source_path = Path(source_file).resolve()
    validate_output_paths(source_path, Path(output_exe))
    emit_cinclude_backend_warning(source_file, "LLVM AOT")
    with Phase("native.read_source"):
        source = source_path.read_text(encoding="utf-8")
    with Phase("native.frontend"):
        ir_code = compile_to_ir_fast(source, source_file=str(source_path), debug=False)
    flags = _merge_link_flags(
        _extract_ailang_link_flags(source),
        _extract_ailang_link_flags(ir_code),
        ["-lm"],
        _detect_llvm_link_flags(ir_code),
    )
    if not memory_link_flags_supported(tuple(flags)):
        return None
    with Phase("native.object"):
        object_code = compile_ir_object(ir_code, opt_level=opt_level)
    del ir_code, source
    with Phase("native.link"):
        image = link_object_in_memory(object_code, link_flags=tuple(flags))
    del object_code
    with Phase("native.publish"):
        publish_executable(image, Path(output_exe))
    return True


def compile_to_native(
    source_file: str,
    output_exe: str,
    opt_level: int | str = 3,
    pgo_generate_dir: str = "",
    pgo_use_dir: str = "",
    llvm_pgo_generate_dir: str = "",
    llvm_pgo_use_dir: str = "",
    native_toolchain: str = "auto",
    debug_info: bool = False,
) -> bool:
    """Build and link automatically; output-only mode does not execute user code.

    The default hosted Linux path keeps intermediates in memory. Other targets
    and explicit specialist options keep the existing supported driver path.
    """
    profiles = (
        pgo_generate_dir,
        pgo_use_dir,
        llvm_pgo_generate_dir,
        llvm_pgo_use_dir,
    )
    if _memory_eligible(opt_level, native_toolchain, debug_info, profiles):
        try:
            result = _memory_build(source_file, output_exe, int(opt_level))
        except (
            OSError,
            ValueError,
            RuntimeError,
            SyntaxError,
            ImportError,
            MemoryError,
            CodeGenError,
            subprocess.TimeoutExpired,
        ) as error:
            print(f"Error: {error}")
            return False
        if result is not None:
            return result
    return bool(
        _file_compile_to_native(
            source_file,
            output_exe,
            opt_level,
            pgo_generate_dir=pgo_generate_dir,
            pgo_use_dir=pgo_use_dir,
            llvm_pgo_generate_dir=llvm_pgo_generate_dir,
            llvm_pgo_use_dir=llvm_pgo_use_dir,
            native_toolchain=native_toolchain,
            debug_info=debug_info,
        )
    )
