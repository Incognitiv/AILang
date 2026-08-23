"""
AILang Standard Library Registry

Defines built-in modules that don't need file lookup:
- use std.io      → print, input, etc.
- use std.math    → sqrt, sin, cos, etc.
- use core.types  → type intrinsics
- use freestanding → no OS dependencies (for kernels)
"""

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass
class StdFunction:
    """Represents a standard library function"""

    name: str
    param_types: List[str]
    return_type: str
    description: str
    # For built-ins that map to C functions
    c_name: Optional[str] = None
    # For built-ins that need special codegen
    is_intrinsic: bool = False


# Standard library modules
STD_MODULES: Dict[str, Dict[str, StdFunction]] = {
    # std.io - Input/Output functions
    "std.io": {
        "print": StdFunction(
            "print", ["any"], "void", "Print value to stdout", c_name="printf"
        ),
        "println": StdFunction("println", ["any"], "void", "Print value with newline"),
        "input": StdFunction("input", [], "string", "Read line from stdin"),
        "read_stdin": StdFunction("read_stdin", [], "string", "Read all stdin"),
        "read_file": StdFunction("read_file", ["string"], "string", "Read entire file"),
        "list_dir": StdFunction(
            "list_dir", ["string"], "string", "List directory entries"
        ),
        "write_file": StdFunction(
            "write_file", ["string", "string"], "bool", "Write to file"
        ),
        "file_exists": StdFunction(
            "file_exists", ["string"], "int", "Return 1 if a path exists"
        ),
        "file_can_execute": StdFunction(
            "file_can_execute",
            ["string"],
            "int",
            "Return 1 if a path is executable/searchable on the host",
        ),
        "access": StdFunction(
            "access",
            ["string", "int"],
            "int",
            "POSIX-style host access check: F_OK=0, X_OK=1, W_OK=2, R_OK=4",
        ),
        "process_capture": StdFunction(
            "process_capture", ["string"], "string", "Run command and capture stdout"
        ),
        "process_run_argv": StdFunction(
            "process_run_argv",
            ["str_array"],
            "int",
            "Run command by argv vector and return exit status",
        ),
        "process_run_argv_redirs": StdFunction(
            "process_run_argv_redirs",
            ["str_array", "str_array", "str_array"],
            "int",
            "Run argv vector with ordered redirections",
        ),
        "process_run_argv_env_redirs": StdFunction(
            "process_run_argv_env_redirs",
            ["str_array", "str_array", "str_array", "str_array"],
            "int",
            "Run argv vector with explicit environment additions and ordered redirections",
        ),
        "process_spawn_argv_env_redirs": StdFunction(
            "process_spawn_argv_env_redirs",
            ["str_array", "str_array", "str_array", "str_array"],
            "int",
            "Spawn argv vector asynchronously with explicit environment additions and ordered redirections",
        ),
        "process_spawn_argv_env_redirs_pgrp": StdFunction(
            "process_spawn_argv_env_redirs_pgrp",
            ["str_array", "str_array", "str_array", "str_array", "int"],
            "int",
            "Spawn argv asynchronously and place the child in a POSIX process group where supported",
        ),
        "process_wait_pid": StdFunction(
            "process_wait_pid",
            ["int"],
            "int",
            "Wait for a spawned process and return POSIX-style exit status",
        ),
        "process_wait_pid_event": StdFunction(
            "process_wait_pid_event",
            ["int"],
            "int",
            "Wait for a spawned process, reporting stopped children as negative signal events",
        ),
        "process_poll_pid": StdFunction(
            "process_poll_pid",
            ["int"],
            "int",
            "Poll a spawned process: -1 while running, otherwise exit status",
        ),
        "process_kill_pid": StdFunction(
            "process_kill_pid",
            ["int", "int"],
            "int",
            "Send a signal/termination request to a process",
        ),
        "process_get_pgrp": StdFunction(
            "process_get_pgrp",
            ["int"],
            "int",
            "Return a process group id for a process where the host supports it",
        ),
        "process_set_pgrp": StdFunction(
            "process_set_pgrp",
            ["int", "int"],
            "int",
            "Set a process group id where the host supports it",
        ),
        "process_kill_pgrp": StdFunction(
            "process_kill_pgrp",
            ["int", "int"],
            "int",
            "Send a signal to a process group where the host supports it",
        ),
        "terminal_get_pgrp": StdFunction(
            "terminal_get_pgrp",
            ["int"],
            "int",
            "Return the foreground process group for a terminal fd",
        ),
        "terminal_set_pgrp": StdFunction(
            "terminal_set_pgrp",
            ["int", "int"],
            "int",
            "Set the foreground process group for a terminal fd",
        ),
        "process_exec_replace_argv_env_redirs": StdFunction(
            "process_exec_replace_argv_env_redirs",
            ["str_array", "str_array", "str_array", "str_array"],
            "int",
            "Replace the current process with argv where the host supports exec",
        ),
        "signal_install": StdFunction(
            "signal_install",
            ["int"],
            "int",
            "Install a native handler that records a pending signal",
        ),
        "signal_ignore": StdFunction(
            "signal_ignore", ["int"], "int", "Ignore a native signal"
        ),
        "signal_default": StdFunction(
            "signal_default", ["int"], "int", "Restore default signal behavior"
        ),
        "signal_pending": StdFunction(
            "signal_pending", [], "int", "Return a pending signal without clearing it"
        ),
        "signal_clear": StdFunction(
            "signal_clear", ["int"], "int", "Clear a pending signal"
        ),
        "signal_drain": StdFunction(
            "signal_drain", [], "int", "Return and clear the next pending signal"
        ),
        "signal_raise": StdFunction(
            "signal_raise", ["int"], "int", "Raise a signal in the current process"
        ),
        "process_pipe_argv_redirs": StdFunction(
            "process_pipe_argv_redirs",
            [
                "str_array",
                "str_array",
                "str_array",
                "str_array",
                "str_array",
                "str_array",
            ],
            "int",
            "Run two argv vectors connected by a native pipe",
        ),
        "process_pipeline_argv_redirs": StdFunction(
            "process_pipeline_argv_redirs",
            ["str_array", "array", "str_array", "str_array", "array"],
            "int",
            "Run flattened argv vectors as a native pipeline",
        ),
        "process_pipeline_argv_env_redirs": StdFunction(
            "process_pipeline_argv_env_redirs",
            ["str_array", "array", "str_array", "str_array", "str_array", "array"],
            "int",
            "Run flattened argv vectors as a native pipeline with explicit environment additions",
        ),
        "process_spawn_pipeline_argv_env_redirs": StdFunction(
            "process_spawn_pipeline_argv_env_redirs",
            ["str_array", "array", "str_array", "str_array", "str_array", "array"],
            "int",
            "Spawn flattened argv vectors as a native pipeline and return the last stage PID",
        ),
        "process_spawn_pipeline_argv_env_redirs_pgrp": StdFunction(
            "process_spawn_pipeline_argv_env_redirs_pgrp",
            [
                "str_array",
                "array",
                "str_array",
                "str_array",
                "str_array",
                "array",
                "int",
            ],
            "int",
            "Spawn a native pipeline in a POSIX process group and return the last stage PID",
        ),
        "process_capture_pipeline_argv_redirs": StdFunction(
            "process_capture_pipeline_argv_redirs",
            ["str_array", "array", "str_array", "str_array", "array"],
            "string",
            "Run flattened argv vectors as a native pipeline and capture stdout",
        ),
        "process_capture_pipeline_argv_env_redirs": StdFunction(
            "process_capture_pipeline_argv_env_redirs",
            ["str_array", "array", "str_array", "str_array", "str_array", "array"],
            "string",
            "Run flattened argv vectors as a native pipeline with explicit environment additions and capture stdout",
        ),
        "process_capture_argv_env_redirs": StdFunction(
            "process_capture_argv_env_redirs",
            ["str_array", "str_array", "str_array", "str_array"],
            "string",
            "Run argv vector with env/redirections and capture stdout",
        ),
        "process_last_capture_status": StdFunction(
            "process_last_capture_status",
            [],
            "int",
            "Return exit status from the most recent argv stdout capture",
        ),
        "process_set_last_capture_status": StdFunction(
            "process_set_last_capture_status",
            ["int"],
            "void",
            "Set the most recent argv stdout capture status",
        ),
        "process_last_exec_errno": StdFunction(
            "process_last_exec_errno",
            [],
            "int",
            "Return errno captured from the most recent failed native exec",
        ),
        "process_errno_enoexec": StdFunction(
            "process_errno_enoexec",
            [],
            "int",
            "Return the platform errno value for executable-format errors",
        ),
        "process_errno_enoent": StdFunction(
            "process_errno_enoent",
            [],
            "int",
            "Return the platform errno value for missing files",
        ),
        "process_errno_eacces": StdFunction(
            "process_errno_eacces",
            [],
            "int",
            "Return the platform errno value for permission denied",
        ),
        "process_errno_eperm": StdFunction(
            "process_errno_eperm",
            [],
            "int",
            "Return the platform errno value for operation not permitted",
        ),
        "fd_dup": StdFunction(
            "fd_dup", ["int"], "int", "Duplicate a hosted file descriptor"
        ),
        "fd_dup2": StdFunction(
            "fd_dup2", ["int", "int"], "int", "Duplicate a hosted fd onto another fd"
        ),
        "fd_tell": StdFunction(
            "fd_tell", ["int"], "int", "Return the current hosted fd offset"
        ),
        "fd_seek": StdFunction(
            "fd_seek", ["int", "int"], "int", "Set and return the hosted fd offset"
        ),
        "fd_flush": StdFunction("fd_flush", [], "int", "Flush hosted stdio streams"),
    },
    # std.math - Math functions (map to libm)
    "std.math": {
        "sqrt": StdFunction("sqrt", ["double"], "double", "Square root", c_name="sqrt"),
        "sin": StdFunction("sin", ["double"], "double", "Sine", c_name="sin"),
        "cos": StdFunction("cos", ["double"], "double", "Cosine", c_name="cos"),
        "tan": StdFunction("tan", ["double"], "double", "Tangent", c_name="tan"),
        "pow": StdFunction(
            "pow", ["double", "double"], "double", "Power", c_name="pow"
        ),
        "log": StdFunction("log", ["double"], "double", "Natural log", c_name="log"),
        "log10": StdFunction(
            "log10", ["double"], "double", "Log base 10", c_name="log10"
        ),
        "exp": StdFunction("exp", ["double"], "double", "Exponential", c_name="exp"),
        "abs": StdFunction("abs", ["int"], "int", "Absolute value", c_name="llabs"),
        "fabs": StdFunction(
            "fabs", ["double"], "double", "Float absolute", c_name="fabs"
        ),
        "floor": StdFunction("floor", ["double"], "double", "Floor", c_name="floor"),
        "ceil": StdFunction("ceil", ["double"], "double", "Ceiling", c_name="ceil"),
        "round": StdFunction("round", ["double"], "double", "Round", c_name="round"),
    },
    # std.string - String functions
    "std.string": {
        "strlen": StdFunction(
            "strlen", ["string"], "int", "String length", c_name="strlen"
        ),
        "strcmp": StdFunction(
            "strcmp", ["string", "string"], "int", "Compare strings", c_name="strcmp"
        ),
        "strcpy": StdFunction(
            "strcpy", ["string", "string"], "string", "Copy string", c_name="strcpy"
        ),
        "strcat": StdFunction(
            "strcat", ["string", "string"], "string", "Concatenate", c_name="strcat"
        ),
        "substr": StdFunction(
            "substr", ["string", "int", "int"], "string", "Substring"
        ),
    },
    # std.memory - Memory operations
    "std.memory": {
        "malloc": StdFunction(
            "malloc", ["int"], "ptr", "Allocate memory", c_name="malloc"
        ),
        "free": StdFunction("free", ["ptr"], "void", "Free memory", c_name="free"),
        "memcpy": StdFunction(
            "memcpy", ["ptr", "ptr", "int"], "ptr", "Copy memory", c_name="memcpy"
        ),
        "memset": StdFunction(
            "memset", ["ptr", "int", "int"], "ptr", "Set memory", c_name="memset"
        ),
    },
    # core.types - Type intrinsics (minimal, no OS)
    "core.types": {
        "sizeof": StdFunction(
            "sizeof", ["type"], "int", "Size of type", is_intrinsic=True
        ),
        "alignof": StdFunction(
            "alignof", ["type"], "int", "Alignment of type", is_intrinsic=True
        ),
        "offsetof": StdFunction(
            "offsetof",
            ["type", "field"],
            "int",
            "Field offset in bytes",
            is_intrinsic=True,
        ),
        "typeof": StdFunction(
            "typeof", ["any"], "string", "Type name", is_intrinsic=True
        ),
    },
    # core.intrinsics - CPU-level operations
    "core.intrinsics": {
        "asm": StdFunction(
            "asm", ["string"], "void", "Inline assembly", is_intrinsic=True
        ),
    },
    # freestanding - Kernel/no-OS mode (no libc, no heap)
    # Empty by design: freestanding mode uses only language builtins
    # (peek/poke/inb/outb/asm) which are handled directly by codegen.
    "freestanding": {},
}


def get_std_module(module_path: str) -> Optional[Dict[str, StdFunction]]:
    """Get a standard library module by path"""
    return STD_MODULES.get(module_path)


def is_std_module(module_path: str) -> bool:
    """Check if a module path is a standard library module"""
    return module_path in STD_MODULES


def get_std_function(module_path: str, func_name: str) -> Optional[StdFunction]:
    """Get a specific function from a standard library module"""
    module = STD_MODULES.get(module_path)
    if module:
        return module.get(func_name)
    return None


def list_std_modules() -> List[str]:
    """List all available standard library modules"""
    return list(STD_MODULES.keys())


def list_std_functions(module_path: str) -> List[str]:
    """List all functions in a standard library module"""
    module = STD_MODULES.get(module_path)
    if module:
        return list(module.keys())
    return []
