"""CodeGen support helpers mixin."""

from __future__ import annotations

import warnings
from parser.ast import (
    ASTNode,
    CInclude,
    ClassDef,
    EnumDef,
    ExternFn,
    ExternRecordDef,
    FromImport,
    Function,
    GenericClass,
    GenericFunction,
    GenericRecord,
    Import,
    Library,
    LinkDirective,
    RecordDef,
    TemplateBlock,
    TypeAlias,
    Use,
    VarDecl,
)
from typing import Any, Optional

from ast_access import arg_at
from calling_conventions import llvm_calling_convention, normalized_decorators
from llvmlite import ir

from .codegen_errors import CodeGenError


class _CodeGenSupportMixin:
    def __init__(self: Any) -> None:
        # Linter-only declaration for mixin-owned lazy attribute.
        self.free_func: Optional[ir.Function] = None

    def _get_free(self: Any) -> ir.Function:
        """Lazy declaration of free for temporary string cleanup."""
        if self.free_func is None:
            # Check if free is already declared in module
            if "free" in self.module.globals:
                self.free_func = self.module.globals["free"]
            else:
                void_ptr = ir.IntType(8).as_pointer()
                free_ty = ir.FunctionType(ir.VoidType(), [void_ptr])
                self.free_func = ir.Function(self.module, free_ty, "free")
        return self.free_func

    def generate_string_concat(self: Any, left_str: Any, right_str: Any) -> Any:
        """
        Concatenate two strings by allocating new memory.
        Always uses malloc (not arena) because concat in loops accumulates
        intermediates that must be freed individually to avoid overflow.
        """
        # Get lengths
        left_len = self.current_builder.call(
            self.get_strlen(), [left_str], name="left_len"
        )
        right_len = self.current_builder.call(
            self.get_strlen(), [right_str], name="right_len"
        )
        # Total length = left + right + 1 (for null terminator)
        one = ir.Constant(ir.IntType(64), 1)
        total_len = self.current_builder.add(left_len, right_len, name="concat_len")
        total_len = self.current_builder.add(total_len, one, name="total_len")
        # Always use malloc for concat (arena would accumulate loop intermediates)
        new_str = self.current_builder.call(
            self.get_malloc(), [total_len], name="concat_str"
        )
        # Copy left string
        self.current_builder.call(self.get_strcpy(), [new_str, left_str])
        # Concatenate right string
        self.current_builder.call(self.get_strcat(), [new_str, right_str])
        # Free intermediate temporaries from prior concatenations
        left_name = getattr(left_str, "name", "")
        right_name = getattr(right_str, "name", "")
        if left_name in self.temp_strings:
            self.current_builder.call(self._get_free(), [left_str])
            self.temp_strings.discard(left_name)
        if right_name in self.temp_strings:
            self.current_builder.call(self._get_free(), [right_str])
            self.temp_strings.discard(right_name)
        # Track this result as a temporary for potential future cleanup
        result_name = getattr(new_str, "name", "")
        if result_name:
            self.temp_strings.add(result_name)
        return new_str

    def create_string_constant(self: Any, string: str) -> Any:
        """Create a global string constant"""
        # Convert string to bytes and add null terminator
        string_bytes = (string + "\0").encode("utf-8")
        string_const = ir.Constant(
            ir.ArrayType(ir.IntType(8), len(string_bytes)), bytearray(string_bytes)
        )
        # Create global variable for the string
        global_str = ir.GlobalVariable(
            self.module, string_const.type, name=self.module.get_unique_name("str")
        )
        global_str.linkage = "internal"
        global_str.global_constant = True
        global_str.initializer = string_const
        # Return pointer to first element using GEP constant expression
        # This works at global scope without needing a builder
        zero = ir.Constant(ir.IntType(32), 0)
        return global_str.gep([zero, zero])

    def create_string_constant_gep(
        self: Any, string: str, builder: ir.IRBuilder
    ) -> Any:
        """Create a global string constant with explicit builder for GEP."""
        string_bytes = (string + "\0").encode("utf-8")
        string_const = ir.Constant(
            ir.ArrayType(ir.IntType(8), len(string_bytes)), bytearray(string_bytes)
        )
        global_str = ir.GlobalVariable(
            self.module, string_const.type, name=self.module.get_unique_name("str")
        )
        global_str.linkage = "internal"
        global_str.global_constant = True
        global_str.initializer = string_const
        # Use GEP to get pointer to first element
        zero = ir.Constant(ir.IntType(64), 0)
        return builder.gep(global_str, [zero, zero], name="str_ptr")

    def get_i64_to_cstr_func(self: Any) -> ir.Function:
        """Emit/get a tiny decimal i64 writer: void fn(char *dst, i64 value)."""
        func_name = "__ailang_i64_to_cstr"
        existing = self.module.globals.get(func_name)
        if existing is not None:
            return existing

        i8 = ir.IntType(8)
        i64 = ir.IntType(64)
        i8_ptr = i8.as_pointer()
        func_ty = ir.FunctionType(ir.VoidType(), [i8_ptr, i64])
        func = ir.Function(self.module, func_ty, func_name)
        dst, value = func.args
        dst.name = "dst"
        value.name = "value"

        entry = func.append_basic_block("entry")
        neg_block = func.append_basic_block("neg")
        nonneg_block = func.append_basic_block("nonneg")
        zero_check = func.append_basic_block("zero_check")
        zero_block = func.append_basic_block("zero")
        digit_loop = func.append_basic_block("digit_loop")
        copy_init = func.append_basic_block("copy_init")
        copy_cond = func.append_basic_block("copy_cond")
        copy_body = func.append_basic_block("copy_body")
        term_block = func.append_basic_block("term")

        builder = ir.IRBuilder(entry)
        buf = builder.alloca(ir.ArrayType(i8, 32), name="digits")
        idx_slot = builder.alloca(i64, name="idx")
        mag_slot = builder.alloca(i64, name="mag")
        out_slot = builder.alloca(i8_ptr, name="out")
        copy_idx_slot = builder.alloca(i64, name="copy_idx")
        builder.store(ir.Constant(i64, 0), idx_slot)
        builder.store(dst, out_slot)
        is_neg = builder.icmp_signed("<", value, ir.Constant(i64, 0), name="is_neg")
        builder.cbranch(is_neg, neg_block, nonneg_block)

        builder.position_at_end(neg_block)
        out_neg = builder.load(out_slot, name="out_neg")
        builder.store(ir.Constant(i8, ord("-")), out_neg)
        out_after_sign = builder.gep(out_neg, [ir.Constant(i64, 1)], name="out_sign")
        builder.store(out_after_sign, out_slot)
        mag_neg = builder.sub(ir.Constant(i64, 0), value, name="mag_neg")
        builder.store(mag_neg, mag_slot)
        builder.branch(zero_check)

        builder.position_at_end(nonneg_block)
        builder.store(value, mag_slot)
        builder.branch(zero_check)

        builder.position_at_end(zero_check)
        mag_start = builder.load(mag_slot, name="mag_start")
        is_zero = builder.icmp_unsigned(
            "==", mag_start, ir.Constant(i64, 0), name="is_zero"
        )
        builder.cbranch(is_zero, zero_block, digit_loop)

        builder.position_at_end(zero_block)
        out_zero = builder.load(out_slot, name="out_zero")
        builder.store(ir.Constant(i8, ord("0")), out_zero)
        zero_term = builder.gep(out_zero, [ir.Constant(i64, 1)], name="zero_term")
        builder.store(ir.Constant(i8, 0), zero_term)
        builder.ret_void()

        builder.position_at_end(digit_loop)
        mag_cur = builder.load(mag_slot, name="mag_cur")
        rem = builder.urem(mag_cur, ir.Constant(i64, 10), name="rem")
        quot = builder.udiv(mag_cur, ir.Constant(i64, 10), name="quot")
        digit = builder.trunc(rem, i8, name="digit")
        digit_ch = builder.add(digit, ir.Constant(i8, ord("0")), name="digit_ch")
        idx = builder.load(idx_slot, name="idx_val")
        digit_ptr = builder.gep(
            buf, [ir.Constant(ir.IntType(32), 0), idx], name="digit_ptr"
        )
        builder.store(digit_ch, digit_ptr)
        idx_next = builder.add(idx, ir.Constant(i64, 1), name="idx_next")
        builder.store(idx_next, idx_slot)
        builder.store(quot, mag_slot)
        digits_done = builder.icmp_unsigned(
            "==", quot, ir.Constant(i64, 0), name="digits_done"
        )
        builder.cbranch(digits_done, copy_init, digit_loop)

        builder.position_at_end(copy_init)
        builder.store(builder.load(idx_slot, name="idx_final"), copy_idx_slot)
        builder.branch(copy_cond)

        builder.position_at_end(copy_cond)
        copy_idx = builder.load(copy_idx_slot, name="copy_idx_val")
        copy_done = builder.icmp_unsigned(
            "==", copy_idx, ir.Constant(i64, 0), name="copy_done"
        )
        builder.cbranch(copy_done, term_block, copy_body)

        builder.position_at_end(copy_body)
        src_idx = builder.sub(copy_idx, ir.Constant(i64, 1), name="src_idx")
        src_ptr = builder.gep(
            buf, [ir.Constant(ir.IntType(32), 0), src_idx], name="src_digit_ptr"
        )
        ch = builder.load(src_ptr, name="src_digit")
        out = builder.load(out_slot, name="out")
        builder.store(ch, out)
        out_next = builder.gep(out, [ir.Constant(i64, 1)], name="out_next")
        builder.store(out_next, out_slot)
        builder.store(src_idx, copy_idx_slot)
        builder.branch(copy_cond)

        builder.position_at_end(term_block)
        out_term = builder.load(out_slot, name="out_term")
        builder.store(ir.Constant(i8, 0), out_term)
        builder.ret_void()
        return func

    def get_i64_decimal_len_func(self: Any) -> ir.Function:
        """Emit/get a tiny decimal i64 length helper: i64 fn(i64 value)."""
        func_name = "__ailang_i64_decimal_len"
        existing = self.module.globals.get(func_name)
        if existing is not None:
            return existing

        i64 = ir.IntType(64)
        func_ty = ir.FunctionType(i64, [i64])
        func = ir.Function(self.module, func_ty, func_name)
        func.linkage = "internal"
        func.attributes.add("alwaysinline")
        func.attributes.add("nounwind")
        value = arg_at(func, 0)
        value.name = "value"

        entry = func.append_basic_block("entry")
        zero_block = func.append_basic_block("zero")
        digit_loop = func.append_basic_block("digit_loop")
        done_block = func.append_basic_block("done")

        builder = ir.IRBuilder(entry)
        zero = ir.Constant(i64, 0)
        one = ir.Constant(i64, 1)
        ten = ir.Constant(i64, 10)

        is_neg = builder.icmp_signed("<", value, zero, name="is_neg")
        sign_len = builder.select(is_neg, one, zero, name="sign_len")
        # Plain LLVM subtraction is modulo arithmetic; this preserves INT64_MIN
        # as the correct unsigned magnitude for digit counting.
        neg_mag = builder.sub(zero, value, name="neg_mag")
        mag_start = builder.select(is_neg, neg_mag, value, name="mag_start")
        is_zero = builder.icmp_unsigned("==", mag_start, zero, name="is_zero")
        builder.cbranch(is_zero, zero_block, digit_loop)
        entry_end = builder.block

        builder.position_at_end(zero_block)
        builder.ret(builder.add(sign_len, one, name="zero_len"))

        builder.position_at_end(digit_loop)
        mag_phi = builder.phi(i64, name="mag")
        len_phi = builder.phi(i64, name="len")
        mag_phi.add_incoming(mag_start, entry_end)
        len_phi.add_incoming(sign_len, entry_end)
        len_next = builder.add(len_phi, one, name="len_next")
        quot = builder.udiv(mag_phi, ten, name="quot")
        digits_done = builder.icmp_unsigned("==", quot, zero, name="digits_done")
        builder.cbranch(digits_done, done_block, digit_loop)
        loop_end = builder.block
        mag_phi.add_incoming(quot, loop_end)
        len_phi.add_incoming(len_next, loop_end)

        builder.position_at_end(done_block)
        builder.ret(len_next)
        return func

    def _print_bigint_dec(self: Any, value: ir.Value, _is_unsigned: bool) -> None:
        """Print big integers (>64-bit) in decimal without truncation.
        Handles both signed and unsigned bigints. For signed values,
        prints a '-' prefix and negates before extracting digits.
        """
        printf = self.get_printf()
        # Working value and constants
        val = value
        zero = ir.Constant(value.type, 0)
        ten = ir.Constant(value.type, 10)
        # Buffers: max digits ~ ceil(width * log10(2)) + margin
        # 8192-bit (colos) needs ~2467 digits; formula: (bits * 31 // 100) + 10
        bit_width = value.type.width if hasattr(value.type, "width") else 64
        max_digits = (bit_width * 31 // 100) + 10
        digit_array_ty = ir.ArrayType(ir.IntType(8), max_digits)
        buf = self.current_builder.alloca(digit_array_ty, name="bigint_digits")
        rev_buf = self.current_builder.alloca(digit_array_ty, name="bigint_rev")
        idx = self.current_builder.alloca(ir.IntType(32), name="bigint_idx")
        self.current_builder.store(ir.Constant(ir.IntType(32), 0), idx)
        # Handle zero specially
        is_zero = self.current_builder.icmp_unsigned("==", val, zero)
        zero_block = self.current_function.append_basic_block("bigint_zero")
        loop_block = self.current_function.append_basic_block("bigint_loop")
        after_loop = self.current_function.append_basic_block("bigint_after_loop")
        self.current_builder.cbranch(is_zero, zero_block, loop_block)
        # zero block
        self.current_builder.position_at_end(zero_block)
        buf0_ptr = self.current_builder.gep(
            buf, [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 0)]
        )
        self.current_builder.store(ir.Constant(ir.IntType(8), ord("0")), buf0_ptr)
        self.current_builder.store(ir.Constant(ir.IntType(32), 1), idx)
        self.current_builder.branch(after_loop)
        # loop block
        self.current_builder.position_at_end(loop_block)
        loop_cond_block = self.current_function.append_basic_block("bigint_loop_cond")
        loop_body_block = self.current_function.append_basic_block("bigint_loop_body")
        self.current_builder.branch(loop_cond_block)
        # loop condition
        self.current_builder.position_at_end(loop_cond_block)
        tmp_phi = self.current_builder.phi(value.type, name="bigint_val_phi")
        tmp_phi.add_incoming(val, loop_block)
        not_done = self.current_builder.icmp_unsigned("!=", tmp_phi, zero)
        self.current_builder.cbranch(not_done, loop_body_block, after_loop)
        # loop body
        self.current_builder.position_at_end(loop_body_block)
        q = self.current_builder.udiv(tmp_phi, ten, name="bigint_q")
        r = self.current_builder.urem(tmp_phi, ten, name="bigint_r")
        digit = self.current_builder.trunc(r, ir.IntType(8), name="bigint_digit")
        digit_char = self.current_builder.add(
            digit, ir.Constant(ir.IntType(8), ord("0"))
        )
        cur_idx = self.current_builder.load(idx, name="bigint_idx_val")
        dst_ptr = self.current_builder.gep(
            buf, [ir.Constant(ir.IntType(32), 0), cur_idx], name="bigint_digit_ptr"
        )
        self.current_builder.store(digit_char, dst_ptr)
        next_idx = self.current_builder.add(cur_idx, ir.Constant(ir.IntType(32), 1))
        self.current_builder.store(next_idx, idx)
        tmp_phi.add_incoming(q, loop_body_block)
        self.current_builder.branch(loop_cond_block)
        # after loop: reverse digits
        self.current_builder.position_at_end(after_loop)
        final_len = self.current_builder.load(idx, name="bigint_len")
        rev_i = self.current_builder.alloca(ir.IntType(32), name="bigint_rev_i")
        self.current_builder.store(ir.Constant(ir.IntType(32), 0), rev_i)
        rev_cond = self.current_function.append_basic_block("bigint_rev_cond")
        rev_body = self.current_function.append_basic_block("bigint_rev_body")
        rev_done = self.current_function.append_basic_block("bigint_rev_done")
        self.current_builder.branch(rev_cond)
        self.current_builder.position_at_end(rev_cond)
        cur_rev_i = self.current_builder.load(rev_i)
        rev_not_done = self.current_builder.icmp_unsigned("<", cur_rev_i, final_len)
        self.current_builder.cbranch(rev_not_done, rev_body, rev_done)
        self.current_builder.position_at_end(rev_body)
        src_ptr = self.current_builder.gep(
            buf, [ir.Constant(ir.IntType(32), 0), cur_rev_i]
        )
        rev_idx = self.current_builder.sub(final_len, ir.Constant(ir.IntType(32), 1))
        rev_idx = self.current_builder.sub(rev_idx, cur_rev_i)
        dst_ptr = self.current_builder.gep(
            rev_buf, [ir.Constant(ir.IntType(32), 0), rev_idx]
        )
        ch = self.current_builder.load(src_ptr)
        self.current_builder.store(ch, dst_ptr)
        self.current_builder.store(
            self.current_builder.add(cur_rev_i, ir.Constant(ir.IntType(32), 1)), rev_i
        )
        self.current_builder.branch(rev_cond)
        self.current_builder.position_at_end(rev_done)
        # Null-terminate
        term_ptr = self.current_builder.gep(
            rev_buf, [ir.Constant(ir.IntType(32), 0), final_len]
        )
        self.current_builder.store(ir.Constant(ir.IntType(8), 0), term_ptr)
        # Print via printf("%.*s", len, buf)
        fmt = self.create_string_constant("%.*s\n")
        len_i32 = final_len
        buf_ptr = self.current_builder.gep(
            rev_buf, [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 0)]
        )
        self.current_builder.call(printf, [fmt, len_i32, buf_ptr])

    def _register_std_module(
        self: Any, module_path: str, names: list[str] | None
    ) -> None:
        """Register standard library functions from a 'use' statement"""
        from runtime.stdlib import get_std_module

        module = get_std_module(module_path)
        if not module:
            return
        # Get functions to import (all or selective)
        funcs_to_import = names if names else list(module.keys())
        for func_name in funcs_to_import:
            if func_name not in module:
                raise CodeGenError(f"Function '{func_name}' not found in {module_path}")
            std_func = module[func_name]
            if std_func.c_name and not std_func.is_intrinsic:
                # Declare the C library function
                self._declare_c_function(std_func)

    def _type_str_to_llvm(self: Any, type_str: str) -> ir.Type:
        """Map AILang type string to LLVM type using dictionary lookup."""
        type_map: dict[str, ir.Type] = {
            "void": ir.VoidType(),
            "int": ir.IntType(64),
            "double": ir.DoubleType(),
            "float": ir.FloatType(),
            "string": ir.IntType(8).as_pointer(),
            "ptr": ir.IntType(8).as_pointer(),
            "ptrptr": ir.IntType(8).as_pointer().as_pointer(),
            "bool": ir.IntType(1),
            "any": ir.IntType(8).as_pointer(),
        }
        result = type_map.get(type_str)
        if result is None:
            warnings.warn(
                f"Unknown type '{type_str}' in stdlib mapping, defaulting to i64",
                stacklevel=2,
            )
            return ir.IntType(64)
        return result

    def _declare_c_function(self: Any, std_func: Any) -> None:
        """Declare a C library function for use"""
        # Check if already declared
        c_name = std_func.c_name
        if c_name in [f.name for f in self.module.functions]:
            # Already declared, just register the AILang name
            for f in self.module.functions:
                if f.name == c_name:
                    self.functions[std_func.name] = f
                    return
            return
        # Build function type
        param_types = [self._type_str_to_llvm(t) for t in std_func.param_types]
        ret_type = self._type_str_to_llvm(std_func.return_type)
        func_type = ir.FunctionType(ret_type, param_types)
        func = ir.Function(self.module, func_type, c_name)
        # Register under AILang name for function lookups
        self.functions[std_func.name] = func
        # Also register under C name
        if std_func.name != c_name:
            self.functions[c_name] = func

    def _process_use_statement(
        self,
        node: Any,
        source_file: str,
        *,
        is_std_module: Any,
        get_loader: Any,
        imported_modules: dict[str, Any],
    ) -> None:
        """Process a single 'use' statement."""
        if is_std_module(node.module_path):
            self._register_std_module(node.module_path, node.names)
            return
        try:
            loader = get_loader()
            if source_file:
                loader.set_current_file(source_file)
            module = loader.load_module(node.module_path)
            imported_modules[node.module_path] = module
        except ImportError as e:
            raise CodeGenError(f"Cannot load library '{node.module_path}': {e}") from e

    def _process_ast_node(self: Any, node: ASTNode) -> Optional[Function]:
        """Process a single AST node in Pass 1. Returns Function if it's a function."""
        from parser.ast import Assign

        if isinstance(node, (Import, FromImport, Library, Use)):
            return None
        if isinstance(node, EnumDef):
            self.generate_enum(node)
            return None
        if isinstance(node, RecordDef):
            self.generate_record(node)
            return None
        if isinstance(node, ClassDef):
            self.generate_class(node)
            return None
        if isinstance(node, VarDecl):
            self.generate_global_var(node)
            return None
        if isinstance(node, TypeAlias):
            self.type_aliases[node.name] = node.target_type
            return None
        if isinstance(node, Assign):
            # Handle global assignments (arrays, constants)
            self.generate_global_assign(node)
            return None
        if isinstance(node, Function):
            self.declare_function(node)
            return node
        # Handle generic definitions - register for later instantiation
        if isinstance(node, GenericRecord):
            self.monomorphizer.register_generic(node)
            return None
        if isinstance(node, GenericClass):
            self.monomorphizer.register_generic(node)
            return None
        if isinstance(node, GenericFunction):
            self.monomorphizer.register_generic(node)
            return None
        if isinstance(node, TemplateBlock):
            self._process_template_block(node)
            return None
        if isinstance(node, ExternFn):
            self._process_extern_fn(node)
            return None
        if isinstance(node, ExternRecordDef):
            self._process_extern_record(node)
            return None
        if isinstance(node, (CInclude, LinkDirective)):
            return None  # Directives are C-backend only
        # Import ExternVar and UnionDef
        from parser.ast import ExternVar, UnionDef

        if isinstance(node, ExternVar):
            self._process_extern_var(node)
            return None
        if isinstance(node, UnionDef):
            self._process_union_def(node)
            return None
        return None

    def _process_template_block(self: Any, node: TemplateBlock) -> None:
        """Compile a template block and forward-declare its functions.
        Template blocks contain foreign code (C, C++, Rust, Zig, raw LLVM IR)
        that gets compiled to LLVM IR by templates.py, then merged with
        AILang IR at the end of generate().  Functions defined in template
        code are forward-declared here so AILang code can call them.
        """
        compiled_ir = self._compile_ast_template(node)
        if not compiled_ir:
            return
        self.template_irs.append(compiled_ir)
        # Forward-declare extracted functions so AILang can call them
        for func_sig in self._parse_template_func_sigs(compiled_ir):
            func_name, ret_type, param_types = func_sig
            if func_name in self.functions:
                continue  # Already declared (user override takes priority)
            ftype = ir.FunctionType(ret_type, param_types)
            func = ir.Function(self.module, ftype, name=func_name)
            func.linkage = "external"
            self.functions[func_name] = func

    def _process_extern_fn(self: Any, node: ExternFn) -> None:
        """Declare an external C function from 'extern fn' declaration.
        Creates an LLVM function with external linkage matching the
        declared signature. Handles AILang type names -> LLVM IR types.
        """
        ret_ty = self.get_llvm_type(node.ret_type)
        param_tys: list[ir.Type] = []
        for _, ptype in node.params:
            param_tys.append(self.get_llvm_type(ptype))
        if node.name not in self.functions:
            ftype = ir.FunctionType(ret_ty, param_tys, var_arg=node.variadic)
            func = ir.Function(self.module, ftype, name=node.name)
            func.linkage = "external"
            # Apply calling convention from decorators
            callconv = llvm_calling_convention(
                normalized_decorators(getattr(node, "decorators", []))
            )
            if callconv:
                func.calling_convention = callconv
            # cdecl is the default - no change needed
            self.functions[node.name] = func

    def _process_extern_var(self: Any, node) -> None:
        """Declare an external global variable from 'extern var' declaration."""
        var_type = self.get_llvm_type(node.var_type)
        if node.name not in self.globals:
            gv = ir.GlobalVariable(self.module, var_type, name=node.name)
            gv.linkage = "external"
            self.globals[node.name] = gv

    def _process_extern_record(self: Any, node: ExternRecordDef) -> None:
        """Register an imported/opaque C record as pointer-only."""
        self.opaque_record_names.add(node.name)
        self.extern_record_c_names[node.name] = getattr(node, "c_name", node.name)
        layout_size = getattr(node, "layout_size", None)
        layout_align = getattr(node, "layout_align", None)
        if layout_size is not None or layout_align is not None:
            field_offsets = getattr(node, "field_offsets", {}) or {}
            field_sizes = getattr(node, "field_sizes", {}) or {}
            self.extern_record_layouts[node.name] = {
                "size": int(layout_size or 0),
                "align": int(layout_align or 0),
                "fields": {
                    field_name: {
                        "offset": int(offset),
                        "size": int(field_sizes.get(field_name, 0)),
                    }
                    for field_name, offset in sorted(field_offsets.items())
                },
            }

    def _process_union_def(self: Any, node) -> None:
        """Process a union definition as a struct with the largest field size."""
        # Find the largest field size to determine union storage
        max_bits = 0
        for _fname, ftype_name in node.fields:
            ftype = self.get_llvm_type(ftype_name)
            bits = self._type_size_bits(ftype)
            if bits > max_bits:
                max_bits = bits
        max_bytes = (max_bits + 7) // 8
        if max_bytes == 0:
            max_bytes = 8
        # Union is represented as a byte array of the largest member's size
        union_type = ir.LiteralStructType([ir.ArrayType(ir.IntType(8), max_bytes)])
        self.record_types[node.name] = union_type
        # Store field info for access
        field_types = {}
        for fname, ftype_name in node.fields:
            field_types[fname] = self.get_llvm_type(ftype_name)
        self.union_field_types[node.name] = field_types

    @staticmethod
    @staticmethod
    def _type_size_bits(llvm_type: ir.Type) -> int:
        """Get size of an LLVM type in bits."""
        if isinstance(llvm_type, ir.IntType):
            return llvm_type.width
        if isinstance(llvm_type, ir.FloatType):
            return 32
        if isinstance(llvm_type, ir.DoubleType):
            return 64
        if isinstance(llvm_type, ir.PointerType):
            return 64
        if isinstance(llvm_type, ir.ArrayType):
            return _CodeGenSupportMixin._type_size_bits(llvm_type.element) * int(
                llvm_type.count
            )
        return 64

    def _compile_ast_template(self: Any, node: TemplateBlock) -> str:
        """Compile a TemplateBlock AST node to LLVM IR."""
        from .templates import TemplateBlock as TBlock
        from .templates import template_compiler

        tblock = TBlock(node.language, node.code, node.captured_vars)
        result = template_compiler.compile_template(tblock)
        return result or ""

    def _parse_template_func_sigs(
        self, llvm_ir: str
    ) -> list[tuple[str, ir.Type, list[ir.Type]]]:
        """Parse function signatures from LLVM IR text.
        Returns list of (name, return_type, [param_types]).
        Handles lines like:
          define dso_local i32 @c_add(i32 noundef %0, i32 noundef %1) #0 {
          define i64 @compute(i64 %x, double %y) {
          define void @setup() {
        """
        type_map: dict[str, ir.Type] = {
            "void": ir.VoidType(),
            "i1": ir.IntType(1),
            "i8": ir.IntType(8),
            "i16": ir.IntType(16),
            "i32": ir.IntType(32),
            "i64": ir.IntType(64),
            "i128": ir.IntType(128),
            "float": ir.FloatType(),
            "double": ir.DoubleType(),
        }
        ptr_type = ir.IntType(8).as_pointer()
        results: list[tuple[str, ir.Type, list[ir.Type]]] = []
        for line in llvm_ir.split("\n"):
            stripped = line.strip()
            if not stripped.startswith("define"):
                continue
            if "@" not in stripped:
                continue
            # Strip 'define' and optional linkage (dso_local, hidden, etc.)
            parts = stripped.split("@", 1)
            pre_at = parts[0].split()  # ['define', 'dso_local', 'i32'] etc.
            post_at = parts[1]  # 'c_add(i32 noundef %0, ...) #0 {'
            # Return type is last token before @
            ret_str = pre_at[-1] if pre_at else "void"
            if ret_str.endswith("*"):
                ret_type: ir.Type = ptr_type
            else:
                ret_type = type_map.get(ret_str, ir.IntType(64))
            # Function name is before '('
            name = post_at.split("(", 1)[0]
            # Parse param types from between ( and )
            param_section = ""
            if "(" in post_at and ")" in post_at:
                param_section = post_at.split("(", 1)[1].rsplit(")", 1)[0].strip()
            param_types: list[ir.Type] = []
            if param_section and param_section != "...":
                for param in param_section.split(","):
                    param = param.strip()
                    if not param or param == "...":
                        continue
                    # First word is the type
                    ptype_str = param.split()[0]
                    if ptype_str.endswith("*"):
                        param_types.append(ptr_type)
                    else:
                        param_types.append(type_map.get(ptype_str, ir.IntType(64)))
            results.append((name, ret_type, param_types))
        return results
