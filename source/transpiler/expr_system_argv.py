"""argc/argv globals and builtins for ExprBuiltinSystemEmitter."""

from __future__ import annotations

from llvmlite import ir
from transpiler.expr_common import ARG_FIRST, ExprGenError


def _ensure_argv_globals(self) -> tuple:
    """Ensure global variables for argc/argv are declared.

    Returns (argc_global, argv_global) tuple.
    These store the command-line arguments passed to main().
    """
    int64 = ir.IntType(64)
    char_ptr = ir.IntType(8).as_pointer()
    char_ptr_ptr = char_ptr.as_pointer()

    # Create __ailang_argc global if not exists
    if not hasattr(self.codegen, "argc_global") or self.codegen.argc_global is None:
        argc_global = ir.GlobalVariable(self.codegen.module, int64, "__ailang_argc")
        argc_global.initializer = ir.Constant(int64, 0)
        argc_global.linkage = "internal"
        self.codegen.argc_global = argc_global

    # Create __ailang_argv global if not exists
    if not hasattr(self.codegen, "argv_global") or self.codegen.argv_global is None:
        argv_global = ir.GlobalVariable(
            self.codegen.module, char_ptr_ptr, "__ailang_argv"
        )
        argv_global.initializer = ir.Constant(char_ptr_ptr, None)
        argv_global.linkage = "internal"
        self.codegen.argv_global = argv_global

    return self.codegen.argc_global, self.codegen.argv_global


def _builtin_argc(self, args) -> ir.Value:
    """Return the number of command-line arguments: argc() -> int

    Example:
        count = argc()
        print(count)  // prints argument count including program name
    """
    if len(args) != 0:
        raise ExprGenError("argc() takes no arguments")

    argc_global, _ = self._ensure_argv_globals()
    return self.builder.load(argc_global, name="argc_val")


def _builtin_argv(self, args) -> ir.Value:
    """Return a command-line argument by index: argv(index) -> string

    Example:
        if argc() > 1 then
            first_arg = argv(1)
            print(first_arg)
        end
    """
    if len(args) != 1:
        raise ExprGenError("argv() expects exactly 1 argument (index)")

    _, argv_global = self._ensure_argv_globals()

    # Get the index
    index = self.generate_expr(args[ARG_FIRST])
    if not isinstance(index.type, ir.IntType):
        raise ExprGenError("argv() index must be an integer")

    # Load argv array pointer
    argv_ptr = self.builder.load(argv_global, name="argv_ptr")

    # Get pointer to argv[index]
    arg_ptr_ptr = self.builder.gep(argv_ptr, [index], name="arg_ptr_ptr")

    # Load the string pointer
    arg_ptr = self.builder.load(arg_ptr_ptr, name="arg_str")

    return arg_ptr
