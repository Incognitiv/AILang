"""
BuiltinMiscEmitter - misc LLVM builtin implementations.

Phase A8 extraction from ``CodeGen``:
- function-pointer builtins (fn_ptr/fn_call/fn_call_str)
- printing builtins (putc/print + formatting helpers)
- len() builtin
"""

from __future__ import annotations

from parser.ast import ASTNode, StringLit, Variable
from typing import Any

from callback_types import callback_parts, resolve_callback_alias
from calling_conventions import llvm_calling_convention
from codegen.codegen import CodeGenError
from codegen.strlen_fact_cache import lookup_strlen_fact
from llvmlite import ir
from runtime.modes import CompilationContext


class BuiltinMiscEmitter:
    """Misc builtin emitters used by the LLVM expression pipeline."""

    def __init__(self, codegen: Any) -> None:
        self._cg = codegen

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cg, name)

    def builtin_fn_ptr(self, args: list[ASTNode]) -> ir.Value:
        """Get function pointer: fn_ptr("name") or fn_ptr("name", "Alias")."""
        if len(args) not in (1, 2):
            raise CodeGenError(
                "fn_ptr() expects a function name and optional callback alias"
            )
        if len(args) == 1:
            (name_node,) = args
            alias_node = None
        else:
            name_node, alias_node = args
        # Must be a string literal so we can resolve at compile time.
        from parser import ast as A

        if not isinstance(name_node, A.StringLit):
            raise CodeGenError("fn_ptr() requires a string literal function name")
        func_name = name_node.value
        if func_name not in self.functions:
            raise CodeGenError(f"fn_ptr(): unknown function '{func_name}'")
        func = self.functions[func_name]
        if alias_node is not None:
            if not isinstance(alias_node, A.StringLit):
                raise CodeGenError("fn_ptr() callback alias must be a string literal")
            alias = alias_node.value
            spec = resolve_callback_alias(alias, getattr(self, "type_aliases", {}))
            if spec is None:
                raise CodeGenError(f"fn_ptr(): unknown callback alias '{alias}'")
            fn_ptr_type = self.get_llvm_type(alias)
            if func.type == fn_ptr_type:
                return func
            return self.current_builder.bitcast(
                func, fn_ptr_type, name=f"fnptr_{func_name}_{alias}"
            )
        i8ptr = self.current_builder.bitcast(
            func, ir.IntType(8).as_pointer(), name=f"fnptr_{func_name}"
        )
        return self.current_builder.ptrtoint(
            i8ptr, ir.IntType(64), name=f"fnaddr_{func_name}"
        )

    def builtin_fn_call(self, args: list[ASTNode]) -> ir.Value:
        """Call function through pointer: fn_call(ptr, arg1, ...) -> i64."""
        if len(args) < 1:
            raise CodeGenError("fn_call() expects at least 1 argument (function ptr)")
        from parser import ast as A

        if len(args) >= 2:
            ptr_arg, maybe_alias, *call_arg_nodes = args
        else:
            (ptr_arg,) = args
            maybe_alias = None
            call_arg_nodes = []

        if isinstance(maybe_alias, A.StringLit):
            alias = maybe_alias.value
            spec = resolve_callback_alias(alias, getattr(self, "type_aliases", {}))
            if spec is None:
                raise CodeGenError(f"fn_call(): unknown callback alias '{alias}'")
            params, ret_type, decorators = callback_parts(spec)
            if len(call_arg_nodes) != len(params):
                raise CodeGenError(
                    f"fn_call(): alias '{alias}' expects {len(params)} argument(s)"
                )
            fn_ptr_type = self.get_llvm_type(alias)
            fn_ptr = self.generate_expr(ptr_arg)
            if fn_ptr.type != fn_ptr_type:
                fn_ptr = self.cast_value(fn_ptr, fn_ptr_type)
            call_args = []
            for arg_node, (_pname, ptype) in zip(call_arg_nodes, params):
                value = self.generate_expr(arg_node)
                target_type = self.get_llvm_type(ptype)
                if value.type != target_type:
                    value = self.cast_value(value, target_type)
                call_args.append(value)
            result_name = "" if ret_type == "void" else "fn_result"
            call = self.current_builder.call(fn_ptr, call_args, name=result_name)
            callconv = llvm_calling_convention(decorators)
            if callconv:
                call.calling_convention = callconv
            return call
        ptr_arg, *call_args_nodes = args
        nargs = len(call_args_nodes)

        fn_addr = self.ensure_int64(self.generate_expr(ptr_arg))
        call_args = [self.ensure_int64(self.generate_expr(a)) for a in call_args_nodes]

        i64 = ir.IntType(64)
        fn_type = ir.FunctionType(i64, [i64] * nargs)
        fn_ptr_type = fn_type.as_pointer()

        i8ptr = self.current_builder.inttoptr(
            fn_addr, ir.IntType(8).as_pointer(), name="fn_i8ptr"
        )
        fn_ptr = self.current_builder.bitcast(i8ptr, fn_ptr_type, name="fn_typed")
        return self.current_builder.call(fn_ptr, call_args, name="fn_result")

    def builtin_fn_call_str(self, args: list[ASTNode]) -> ir.Value:
        """Call function pointer that returns string: fn_call_str(ptr, args...) -> string."""
        if len(args) < 1:
            raise CodeGenError(
                "fn_call_str() expects at least 1 argument (function ptr)"
            )
        ptr_arg, *call_args_nodes = args
        nargs = len(call_args_nodes)

        fn_addr = self.ensure_int64(self.generate_expr(ptr_arg))
        call_args = [self.ensure_int64(self.generate_expr(a)) for a in call_args_nodes]

        i64 = ir.IntType(64)
        i8ptr = ir.IntType(8).as_pointer()
        fn_type = ir.FunctionType(i8ptr, [i64] * nargs)
        fn_ptr_type = fn_type.as_pointer()

        raw = self.current_builder.inttoptr(
            fn_addr, ir.IntType(8).as_pointer(), name="fns_i8ptr"
        )
        fn_ptr = self.current_builder.bitcast(raw, fn_ptr_type, name="fns_typed")
        return self.current_builder.call(fn_ptr, call_args, name="fns_result")

    def builtin_putc(self, args: list[ASTNode]) -> ir.Value:
        """Generate code for putc(char_code)."""
        CompilationContext.require_feature("print", "putc()")

        if len(args) != 1:
            raise CodeGenError("putc() expects exactly 1 argument")

        (arg_expr,) = args
        char_code = self.generate_expr(arg_expr)

        if isinstance(char_code.type, ir.IntType):
            if char_code.type.width > 32:
                char_code = self.current_builder.trunc(
                    char_code, ir.IntType(32), name="putc_trunc"
                )
            elif char_code.type.width < 32:
                char_code = self.current_builder.zext(
                    char_code, ir.IntType(32), name="putc_zext"
                )
        else:
            raise CodeGenError("putc() argument must be an integer")

        putchar_ty = ir.FunctionType(ir.IntType(32), [ir.IntType(32)])
        try:
            putchar = self.module.get_global("putchar")
        except KeyError:
            putchar = ir.Function(self.module, putchar_ty, "putchar")

        return self.current_builder.call(putchar, [char_code])

    def builtin_print(self, args: list[ASTNode]) -> ir.Value:
        """Generate code for built-in print()."""
        CompilationContext.require_feature("print", "print()")

        printf = self.get_printf()
        format_strs: list[str] = []
        ir_args: list[Any] = []

        for arg_node in args:
            value = self.generate_expr(arg_node)
            result = self._format_print_value(value, format_strs, ir_args, printf)
            if result == "continue":
                continue

        if format_strs:
            fmt_str = self.create_string_constant(" ".join(format_strs) + "\n")
            return self.current_builder.call(printf, [fmt_str, *ir_args])
        nl = self.create_string_constant("\n")
        return self.current_builder.call(printf, [nl])

    def _format_print_value(
        self,
        value: ir.Value,
        format_strs: list[str],
        ir_args: list[Any],
        printf: ir.Function,
    ) -> str:
        """Format a value for printing, returns 'continue' for big-int case."""
        if isinstance(value.type, ir.IntType):
            return self._format_int_for_print(value, format_strs, ir_args, printf)
        if isinstance(value.type, (ir.FloatType, ir.DoubleType)):
            format_strs.append("%f")
            ir_args.append(self.current_builder.fpext(value, ir.DoubleType()))
        elif self._is_string_pointer(value.type):
            format_strs.append("%s")
            ir_args.append(value)
        else:
            format_strs.append("<?>")
        return ""

    def _format_int_for_print(
        self,
        value: ir.Value,
        format_strs: list[str],
        ir_args: list[Any],
        printf: ir.Function,
    ) -> str:
        """Handle integer formatting for print. Returns 'continue' for big-int."""
        width = value.type.width
        is_unsigned = self.is_unsigned_value(value)
        if width == 1:
            format_strs.append("%s")
            true_str = self.create_string_constant("true")
            false_str = self.create_string_constant("false")
            ir_args.append(self.current_builder.select(value, true_str, false_str))
            return ""
        if width <= 32:
            format_strs.append("%u" if is_unsigned else "%d")
            ir_args.append(value)
            return ""
        if width <= 64:
            format_strs.append("%llu" if is_unsigned else "%lld")
            ir_args.append(value)
            return ""
        if format_strs:
            fmt_str = self.create_string_constant(" ".join(format_strs) + " ")
            self.current_builder.call(printf, [fmt_str, *ir_args])
            format_strs.clear()
            ir_args.clear()
        self._print_bigint_dec(value, is_unsigned)
        return "continue"

    def _is_string_pointer(self, typ: ir.Type) -> bool:
        """Check if type is a pointer to i8 (string)."""
        return (
            isinstance(typ, ir.PointerType)
            and isinstance(typ.pointee, ir.IntType)
            and typ.pointee.width == 8
        )

    def builtin_len(self, arg_node: ASTNode) -> ir.Value:
        """Generate code for built-in len()."""
        if isinstance(arg_node, Variable) and arg_node.name in self.array_metadata:
            length, _ = self.array_metadata[arg_node.name]
            return ir.Constant(ir.IntType(64), length)

        cached_len = lookup_strlen_fact(self._cg, arg_node)
        if cached_len is not None:
            return self.ensure_int64(cached_len)
        virtual_len = self.builtin_string._try_emit_virtual_strlen(arg_node)
        if virtual_len is not None:
            return virtual_len

        str_val = self.generate_expr(arg_node)
        if not (
            isinstance(str_val.type, ir.PointerType)
            and isinstance(str_val.type.pointee, ir.IntType)
            and str_val.type.pointee.width == 8
        ):
            raise CodeGenError("len() called on non-string or non-array type")

        strlen = self.get_strlen()
        return self.current_builder.call(strlen, [str_val])

    def builtin_as_class(self, args: list[ASTNode]) -> ir.Value:
        """Cast i64 value to class pointer: as_class(val, ClassName)."""
        if len(args) != 2:
            raise CodeGenError("as_class() expects (value, ClassName)")
        value_arg, class_name_arg = args

        if isinstance(class_name_arg, Variable):
            class_name = class_name_arg.name
        elif isinstance(class_name_arg, StringLit):
            class_name = class_name_arg.value
        else:
            raise CodeGenError("as_class() second argument must be a class name")

        if class_name not in self.class_types:
            raise CodeGenError(f"as_class(): unknown class '{class_name}'")
        class_struct = self.class_types[class_name]
        target_type = class_struct.as_pointer()

        raw_val = self.generate_expr(value_arg)
        if isinstance(raw_val.type, ir.PointerType):
            return self.current_builder.bitcast(
                raw_val, target_type, name=f"as_{class_name}"
            )
        return self.current_builder.inttoptr(
            raw_val, target_type, name=f"as_{class_name}"
        )
