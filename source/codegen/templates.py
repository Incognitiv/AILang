"""
AILang Template System
Handles foreign code injection via #template blocks
"""

import os
import subprocess
import tempfile
from typing import Any, Optional

TEMPLATE_COMPILE_TIMEOUT_SECONDS = 120


class TemplateError(Exception):
    """Exception raised during template compilation."""


class TemplateBlock:
    """Represents a #template ... #end block with language code and metadata."""

    language: str
    code: str
    captured_vars: list[str]
    compiled_ir: Optional[str]
    function_names: list[str]

    def __init__(
        self, language: str, code: str, captured_vars: Optional[list[str]] = None
    ) -> None:
        self.language = language
        self.code = code
        self.captured_vars = captured_vars or []
        self.compiled_ir = None
        self.function_names = []


class TemplateCompiler:
    """Compiles template blocks to LLVM IR"""

    templates: dict[str, dict[str, Any]]

    def __init__(self) -> None:
        self.templates = {}
        self.load_builtin_templates()

    def load_builtin_templates(self) -> None:
        """Load built-in template configurations for LLVM-based languages"""

        # Platform-specific flags
        import sys

        # -fPIC is not supported on Windows MSVC target
        pic_flag = [] if sys.platform == "win32" else ["-fPIC"]

        # C (ANSI C with stdint)
        self.templates["ansi_c"] = {
            "language": "C",
            "compiler": "clang",
            "flags": ["-S", "-emit-llvm", "-O2", *pic_flag],
            "extension": ".c",
            "type_mapping": {
                "int": "int64_t",
                "long": "__int128",
                "float": "float",
                "double": "double",
                "bool": "_Bool",
            },
        }

        # C (freestanding - no libc)
        self.templates["c_freestanding"] = {
            "language": "C (freestanding)",
            "compiler": "clang",
            "flags": [
                "-S",
                "-emit-llvm",
                "-O2",
                "-ffreestanding",
                "-fno-builtin",
                *pic_flag,
            ],
            "extension": ".c",
            "preamble": "typedef long int64_t;\ntypedef unsigned long uint64_t;\n",
            "type_mapping": {
                "int": "int64_t",
                "long": "__int128",
                "float": "float",
                "double": "double",
                "bool": "_Bool",
            },
        }

        # C++
        self.templates["cpp"] = {
            "language": "C++",
            "compiler": "clang++",
            "flags": ["-S", "-emit-llvm", "-O2", *pic_flag, "-std=c++17"],
            "extension": ".cpp",
            "type_mapping": {
                "int": "int64_t",
                "long": "__int128",
                "float": "float",
                "double": "double",
                "bool": "bool",
            },
        }

        # Rust (requires rustc with --emit=llvm-ir)
        self.templates["rust"] = {
            "language": "Rust",
            "compiler": "rustc",
            "flags": ["--emit=llvm-ir", "-O", "--crate-type=lib"],
            "extension": ".rs",
            "preamble": "#![no_std]\n#![allow(unused)]\n",
            "type_mapping": {
                "int": "i64",
                "long": "i128",
                "float": "f32",
                "double": "f64",
                "bool": "bool",
            },
        }

        # Zig (compiles to LLVM IR)
        self.templates["zig"] = {
            "language": "Zig",
            "compiler": "zig",
            "flags": ["build-obj", "-femit-llvm-ir", "-OReleaseFast"],
            "extension": ".zig",
            "type_mapping": {
                "int": "i64",
                "long": "i128",
                "float": "f32",
                "double": "f64",
                "bool": "bool",
            },
        }

        # LLVM IR directly (pass-through)
        self.templates["llvm"] = {
            "language": "LLVM IR",
            "compiler": None,  # No compilation needed
            "flags": [],
            "extension": ".ll",
            "type_mapping": {
                "int": "i64",
                "long": "i128",
                "float": "float",
                "double": "double",
                "bool": "i1",
            },
        }

    def compile_template(self, template_block: TemplateBlock) -> Optional[str]:
        """Compile template block to LLVM IR"""
        template_config = self.templates.get(template_block.language)
        if not template_config:
            raise TemplateError(f"Unknown template language: {template_block.language}")

        # LLVM IR pass-through - no compilation needed
        if template_config["compiler"] is None:
            template_block.compiled_ir = template_block.code
            template_block.function_names = self._extract_function_names(
                template_block.code
            )
            return template_block.compiled_ir

        # Create temporary source file
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=template_config["extension"],
            delete=False,
            encoding="utf-8",
        ) as src_file:
            # Add preamble if defined (e.g., #![no_std] for Rust)
            if "preamble" in template_config:
                src_file.write(template_config["preamble"])

            # Add standard includes for C
            if template_block.language == "ansi_c":
                src_file.write("#include <stdint.h>\n")
                src_file.write("#include <stdbool.h>\n")
                src_file.write("#include <stdio.h>\n\n")

            src_file.write(template_block.code)
            src_file.flush()
            src_path = src_file.name

        try:
            # Compile to LLVM IR
            ir_path = src_path.replace(template_config["extension"], ".ll")

            # Build compile command based on language
            if template_block.language == "zig":
                # Zig outputs to zig-cache by default, use -femit-llvm-ir=<path> to control
                # M25 fix: Explicitly specify output path
                compile_cmd = [
                    template_config["compiler"],
                    "build-obj",
                    f"-femit-llvm-ir={ir_path}",
                    "-OReleaseFast",
                    src_path,
                ]
            elif template_block.language == "rust":
                # Rust outputs to .ll with same name
                compile_cmd = [
                    template_config["compiler"],
                    *template_config["flags"],
                    "-o",
                    ir_path.replace(".ll", ""),
                    src_path,
                ]
            else:
                compile_cmd = [
                    template_config["compiler"],
                    *template_config["flags"],
                    "-o",
                    ir_path,
                    src_path,
                ]

            try:
                result = subprocess.run(
                    compile_cmd,
                    capture_output=True,
                    text=True,
                    timeout=TEMPLATE_COMPILE_TIMEOUT_SECONDS,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise TemplateError(
                    "Template compilation timed out "
                    f"after {TEMPLATE_COMPILE_TIMEOUT_SECONDS}s"
                ) from exc

            if result.returncode != 0:
                raise TemplateError(f"Template compilation failed:\n{result.stderr}")

            # Read generated LLVM IR
            with open(ir_path, "r", encoding="utf-8") as f:
                raw_ir = f.read()

            # Strip language-specific bloat (runtime init, panic handlers, etc.)
            template_block.compiled_ir = self._strip_runtime_bloat(
                raw_ir, template_block.language
            )

            # Extract function names from IR (simple parsing)
            template_block.function_names = self._extract_function_names(
                template_block.compiled_ir
            )

            # Cleanup
            if os.path.exists(ir_path):
                os.unlink(ir_path)

            return template_block.compiled_ir

        finally:
            if os.path.exists(src_path):
                os.unlink(src_path)

    def _strip_runtime_bloat(self, llvm_ir: str, language: str) -> str:
        """Strip language-specific runtime bloat from LLVM IR.

        Each language adds its own runtime initialization, panic handlers,
        personality functions, etc. AILang wants pure computation only.
        """
        lines = llvm_ir.split("\n")
        filtered: list[str] = []
        skip_until_closing_brace = False

        # Patterns to skip (language runtime bloat)
        skip_patterns = [
            # Rust runtime
            "rust_begin_unwind",
            "rust_panic",
            "_ZN4core",  # Rust core:: mangled names
            "__rust_",
            "lang_start",
            # C++ runtime
            "__cxa_",
            "_ZSt",  # std:: mangled
            "__gxx_personality",
            # Common runtime init
            "_GLOBAL__sub_I_",
            "__do_global_",
            "llvm.global_ctors",
            "llvm.global_dtors",
            # Exception handling (unless explicitly needed)
            "personality.*__gxx_personality",
            "landingpad",
        ]

        # Attributes to strip (debug info, language-specific metadata)
        strip_attributes = [
            "!dbg",  # Debug info
            "!llvm.module.flags",
            "!llvm.ident",
            "source_filename",
        ]

        for line in lines:
            # Skip function definitions for runtime bloat
            if any(pattern in line for pattern in skip_patterns):
                if line.strip().startswith("define"):
                    skip_until_closing_brace = True
                continue

            # Skip until we close the function body
            if skip_until_closing_brace:
                if line.strip() == "}":
                    skip_until_closing_brace = False
                continue

            # Skip metadata and debug lines
            if any(attr in line for attr in strip_attributes):
                continue

            # Skip empty attribute groups
            if line.strip().startswith("attributes #") and "sanitize" in line:
                continue

            filtered.append(line)

        return "\n".join(filtered)

    def _extract_function_names(self, llvm_ir: str) -> list[str]:
        """Extract function names from LLVM IR"""
        functions: list[str] = []
        for line in llvm_ir.split("\n"):
            line = line.strip()
            if line.startswith("define") and "@" in line:
                # Extract function name between 'define' and '('
                parts = line.split("@")
                if len(parts) > 1:
                    func_name = parts[1].split("(")[0]
                    functions.append(func_name)
        return functions

    def merge_ir(self, ailang_ir: str, template_irs: list[str]) -> str:
        """Merge AILang IR with template IRs"""
        # Simple concatenation - proper LLVM module linking would be better
        merged = ailang_ir

        for template_ir in template_irs:
            # Skip duplicate target triple/datalayout
            filtered = [
                line
                for line in template_ir.split("\n")
                if not line.startswith(
                    ("target triple", "target datalayout", "source_filename")
                )
            ]

            merged += "\n\n; Template code\n"
            merged += "\n".join(filtered)

        return merged


# Singleton instance
template_compiler = TemplateCompiler()
