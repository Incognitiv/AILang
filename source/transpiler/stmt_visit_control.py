"""Control-flow statement visitors for CStmtEmitter."""

from __future__ import annotations

from parser import ast as A

from transpiler.codegen_int_ranges import (
    clear_loop_variant_ranges,
    merge_codegen_ranges,
    prepare_while_loop_ranges,
    restore_codegen_ranges,
    snapshot_codegen_ranges,
)
from transpiler.fixed_int_cast_codegen import checked_fixed_int_conversion_expr
from transpiler.c_bigint import is_unbounded_spec, owned_bigint_expr, expression_is_borrowed_bigint
from transpiler.strlen_cache import (
    enter_strlen_cache_control,
    leave_strlen_cache_control,
)


def _condition_text(expr: str) -> str:
    """Strip one redundant full-expression paren pair for warning-clean C."""
    text = expr.strip()
    if not (text.startswith("(") and text.endswith(")")):
        return text
    depth = 0
    for index, char in enumerate(text):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0 and index != len(text) - 1:
                return text
        if depth < 0:
            return text
    return text[1:-1].strip()


def _return_temp_open(self, value: str) -> str:
    ret_type = getattr(self, "_current_ret_type", "").strip()
    if (
        ret_type
        and ret_type != "void"
        and "typeof" not in ret_type
        and "[" not in ret_type
        and ")" not in ret_type
    ):
        decl = self._format_c_declaration(ret_type, "__ret_val")
        return f"{{ {decl} = {value};"
    return f"{{ typeof({value}) __ret_val = {value};"


def _returned_string_borrows_cleaned_array(self, node: A.ASTNode) -> bool:
    """True when a string return points inside a local array we will drop.

    ``split_str_get(parts, i)`` and ``str_array_get(parts, i)`` borrow an
    element owned by ``parts``.  Returning that pointer and then running the
    ordinary local cleanup used to free the container first, leaving the
    caller with a dangling string.  Direct ``parts[i]`` has the same shape.
    """
    owner_name = None
    if isinstance(node, A.Call) and node.name in {"split_str_get", "str_array_get"}:
        if node.args and isinstance(node.args[0], A.Variable):
            owner_name = node.args[0].name
    elif isinstance(node, A.ArrayAccess) and isinstance(node.array, A.Variable):
        owner_name = node.array.name
    if owner_name is None:
        return False
    cleanup_names: set[str] = set()
    for attr in (
        "_str_array_locals_for_cleanup",
        "_lc_str_array_locals_for_cleanup",
    ):
        for entry in getattr(self, attr, None) or []:
            cleanup_names.add(entry[0] if isinstance(entry, tuple) else entry)
    return owner_name in cleanup_names


def _visit_cache_scoped_body(self, body) -> None:
    enter_strlen_cache_control(self)
    try:
        for stmt in body:
            self.visit(stmt)
    finally:
        leave_strlen_cache_control(self)


def _emit_loop_body(self, body) -> None:
    self._loop_depth += 1
    clear_loop_variant_ranges(self, body)
    _visit_cache_scoped_body(self, body)
    self._loop_depth -= 1
    self.indent -= 1


def _emit_outer_loop_stream_cleanup(self) -> None:
    if self._loop_depth == 0 and self._needs_stream_cleanup:
        self.emit("_ailang_close_streams();")
        self._needs_stream_cleanup = False


def _assigned_names(node) -> set[str]:
    if node is None:
        return set()
    if isinstance(node, A.Assign):
        return {node.var_name}
    if isinstance(node, A.VarDecl):
        return {node.var_name}
    names: set[str] = set()
    if isinstance(node, A.ASTNode):
        for child in vars(node).values():
            names.update(_assigned_names(child))
    elif isinstance(node, (list, tuple)):
        for item in node:
            names.update(_assigned_names(item))
    return names


def _drop_assigned_strlen_cache(self, node) -> None:
    cache = getattr(self, "_c_strlen_cache", None)
    if isinstance(cache, dict):
        for name in _assigned_names(node):
            cache.pop(name, None)


def _visit_loop_body_and_close(self, body, *, cleanup_streams: bool) -> None:
    self.indent += 1
    self._loop_depth += 1
    clear_loop_variant_ranges(self, body)
    _visit_cache_scoped_body(self, body)
    self._loop_depth -= 1
    self.indent -= 1
    self.emit("}")
    _drop_assigned_strlen_cache(self, body)
    if cleanup_streams and self._loop_depth == 0 and self._needs_stream_cleanup:
        self.emit("_ailang_close_streams();")
        self._needs_stream_cleanup = False


def visit_If(self, node: A.If) -> None:
    """Generate if statement."""
    before_ranges = snapshot_codegen_ranges(self)
    elsif_branches = getattr(node, "elsif_branches", None) or []
    has_elsif = bool(elsif_branches)
    cond = _condition_text(self.expr(node.cond))
    self.emit(f"if ({cond}) {{")
    self.indent += 1
    restore_codegen_ranges(self, before_ranges)
    _visit_cache_scoped_body(self, node.then_body)
    then_ranges = snapshot_codegen_ranges(self)
    self.indent -= 1

    else_ranges = before_ranges
    if has_elsif:
        for elsif_cond, elsif_body in elsif_branches:
            econd = _condition_text(self.expr(elsif_cond))
            self.emit(f"}} else if ({econd}) {{")
            self.indent += 1
            restore_codegen_ranges(self, before_ranges)
            _visit_cache_scoped_body(self, elsif_body)
            else_ranges = snapshot_codegen_ranges(self)
            self.indent -= 1

    if node.else_body:
        self.emit("} else {")
        self.indent += 1
        restore_codegen_ranges(self, before_ranges)
        _visit_cache_scoped_body(self, node.else_body)
        else_ranges = snapshot_codegen_ranges(self)
        self.indent -= 1

    self.emit("}")
    if has_elsif:
        restore_codegen_ranges(self, before_ranges)
    else:
        merge_codegen_ranges(self, then_ranges, else_ranges, source_if=node)
    _drop_assigned_strlen_cache(self, node)


def visit_TryExcept(self, node: A.TryExcept) -> None:
    """Generate try/catch using setjmp/longjmp."""
    self._needs_exceptions = True

    self.emit("/* try/catch (setjmp/longjmp) */")
    self.emit("{")
    self.indent += 1

    # Save previous jmp_buf for nesting
    self.emit("jmp_buf __saved_jmpbuf;")
    self.emit("int __saved_in_try = __ailang_in_try;")
    self.emit("memcpy(__saved_jmpbuf, __ailang_exc_jmpbuf, sizeof(jmp_buf));")
    self.emit("__ailang_in_try = 1;")
    self.emit("int __exc_val = setjmp(__ailang_exc_jmpbuf);")
    self.emit("if (__exc_val == 0) {")
    self.indent += 1

    # Try body
    for stmt in node.try_body:
        self.visit(stmt)

    # Restore jmp_buf after successful try
    self.emit("__ailang_in_try = __saved_in_try;")
    self.emit("memcpy(__ailang_exc_jmpbuf, __saved_jmpbuf, sizeof(jmp_buf));")

    self.indent -= 1
    self.emit("} else {")
    self.indent += 1

    # Restore jmp_buf so outer try blocks work
    self.emit("__ailang_in_try = __saved_in_try;")
    self.emit("memcpy(__ailang_exc_jmpbuf, __saved_jmpbuf, sizeof(jmp_buf));")

    # Dispatch to catch blocks by type code
    first = True
    for error_type, var, body in node.catch_blocks:
        type_code = self._error_type_hash(error_type)
        cond = "if" if first else "} else if"
        self.emit(f"{cond} (__ailang_exc_type == {type_code}u) {{")
        self.indent += 1
        if var:
            self.emit(f"const char *{var} = __ailang_exc_msg;")
        for stmt in body:
            self.visit(stmt)
        self.indent -= 1
        first = False

    # Except (catch-all)
    if node.except_block:
        error_var, except_body = node.except_block
        cond = "if" if first else "} else if"
        self.emit(f"{cond} (1) {{")
        self.indent += 1
        if error_var:
            self.emit(f"const char *{error_var} = __ailang_exc_msg;")
        for stmt in except_body:
            self.visit(stmt)
        self.indent -= 1
        first = False

    if not first:
        self.emit("}")

    self.indent -= 1
    self.emit("}")

    # Finally block (always executes)
    if node.finally_block:
        self.emit("/* finally */")
        for stmt in node.finally_block:
            self.visit(stmt)

    self.indent -= 1
    self.emit("}")


def visit_Throw(self, node: A.Throw) -> None:
    """Generate throw using longjmp."""
    self._needs_exceptions = True

    msg = self.expr(node.message) if node.message is not None else '"Unknown error"'

    type_code = self._error_type_hash(node.error_type) if node.error_type else 0

    self.emit(f"__ailang_exc_msg = (const char*){msg};")
    self.emit(f"__ailang_exc_type = {type_code}u;")
    self.emit("longjmp(__ailang_exc_jmpbuf, 1);")


def _error_type_hash(error_type: str) -> int:
    """Deterministic hash for error type names (catch dispatch)."""
    result = 5381
    for char in error_type:
        result = ((result * 33) + ord(char)) & 0xFFFFFFFF
    return result if result != 0 else 1


def visit_While(self, node: A.While) -> None:
    """Generate while loop with optional max_iterations bound."""
    cond = _condition_text(self.expr(node.cond))

    if node.max_iterations is not None:
        # Bounded while loop - use unique counter to avoid redefinition
        max_expr = self.expr(node.max_iterations)
        counter_var = f"_bound_iter_{self._bound_counter}"
        self._bound_counter += 1
        self.emit(f"int64_t {counter_var} = 0;")
        self.emit(f"while (({cond}) && ({counter_var} < {max_expr})) {{")
        self.indent += 1
        self.emit(f"{counter_var}++;")
    else:
        self.emit(f"while ({cond}) {{")
        self.indent += 1

    self._loop_depth += 1
    prepare_while_loop_ranges(self, node)
    _visit_cache_scoped_body(self, node.body)
    self._loop_depth -= 1
    self.indent -= 1
    self.emit("}")
    _drop_assigned_strlen_cache(self, node.body)
    _emit_outer_loop_stream_cleanup(self)


def visit_DoWhile(self, node: A.DoWhile) -> None:
    """Generate do-while loop with optional max_iterations bound."""
    if node.max_iterations is not None:
        # Bounded do-while loop - use unique counter to avoid redefinition
        max_expr = self.expr(node.max_iterations)
        counter_var = f"_bound_iter_{self._bound_counter}"
        self._bound_counter += 1
        self._current_bound_var = counter_var  # Save for closing condition
        self.emit(f"int64_t {counter_var} = 0;")
        self.emit("do {")
        self.indent += 1
        self.emit(f"{counter_var}++;")
    else:
        self.emit("do {")
        self.indent += 1

    _emit_loop_body(self, node.body)
    cond = _condition_text(self.expr(node.cond))

    if node.max_iterations is not None:
        max_expr = self.expr(node.max_iterations)
        counter_var = self._current_bound_var  # Use saved variable name
        self.emit(f"}} while (({cond}) && ({counter_var} < {max_expr}));")
    else:
        self.emit(f"}} while ({cond});")

    _emit_outer_loop_stream_cleanup(self)
    _drop_assigned_strlen_cache(self, node.body)


def visit_For(self, node: A.For) -> None:
    """Generate for loop."""
    init_str = ""
    if node.init and isinstance(node.init, A.Assign):
        init_str = f"{node.init.var_name} = {self.expr(node.init.value)}"

    cond_str = _condition_text(self.expr(node.cond)) if node.cond else "1"

    step_str = ""
    if node.step and isinstance(node.step, A.Assign):
        step_str = f"{node.step.var_name} = {self.expr(node.step.value)}"

    if node.max_iterations is not None:
        # Bounded for loop - use unique counter to avoid redefinition
        max_expr = self.expr(node.max_iterations)
        counter_var = f"_bound_iter_{self._bound_counter}"
        self._bound_counter += 1
        self.emit(f"int64_t {counter_var} = 0;")
        self.emit(
            f"for ({init_str}; ({cond_str}) && ({counter_var} < {max_expr}); {step_str}) {{"
        )
        self.indent += 1
        self.emit(f"{counter_var}++;")
    else:
        self.emit(f"for ({init_str}; {cond_str}; {step_str}) {{")
        self.indent += 1

    _emit_loop_body(self, node.body)
    self.emit("}")
    _drop_assigned_strlen_cache(self, node.body)
    _emit_outer_loop_stream_cleanup(self)


def visit_Foreach(self, node: A.Foreach) -> None:
    """Generate foreach loop."""
    var = node.var_name

    if isinstance(node.iterable, A.Range):
        start = self.expr(node.iterable.start)
        end = self.expr(node.iterable.end)
        self.emit(f"for ({var} = {start}; {var} < {end}; {var}++) {{")
    elif isinstance(node.iterable, A.ArrayLit):
        # Array literal: create temporary array with length
        elements = [self.expr(e) for e in node.iterable.elements]
        arr_name = f"_arr_{id(node)}"
        self.emit(f"int64_t {arr_name}[] = {{ {', '.join(elements)} }};")
        self.emit(f"for (int64_t _i = 0; _i < {len(elements)}; _i++) {{")
        self.emit(f"    {var} = {arr_name}[_i];")
    elif isinstance(node.iterable, A.Variable):
        # Variable: assume it's an ailang_array struct
        iterable = self.expr(node.iterable)
        self.emit(f"for (int64_t _i = 0; _i < {iterable}.length; _i++) {{")
        self.emit(f"    {var} = {iterable}.data[_i];")
    else:
        # Fallback for other expressions
        iterable = self.expr(node.iterable)
        self.emit(f"/* foreach over {iterable} (unsupported type) */")
        self.emit("for (int64_t _i = 0; _i < 0; _i++) {")
        self.emit(f"    {var} = 0;")

    _emit_loop_body(self, node.body)
    self.emit("}")
    _drop_assigned_strlen_cache(self, node.body)
    _emit_outer_loop_stream_cleanup(self)


def visit_Loop(self, node: A.Loop) -> None:
    """Generate infinite loop with optional max_iterations bound.

    If unbounded, loops exit via break/return.
    If bounded, loop terminates after max_iterations.
    """
    if node.max_iterations is not None:
        # Bounded infinite loop - use unique counter to avoid redefinition
        max_expr = self.expr(node.max_iterations)
        counter_var = f"_bound_iter_{self._bound_counter}"
        self._bound_counter += 1
        self.emit(
            f"for (int64_t {counter_var} = 0; {counter_var} < {max_expr}; {counter_var}++) {{"
        )
    else:
        self.emit("for (;;) {")

    _emit_loop_body(self, node.body)
    self.emit("}")
    _drop_assigned_strlen_cache(self, node.body)


def visit_Repeat(self, node: A.Repeat) -> None:
    """Generate repeat N times loop."""
    count = self.expr(node.count)
    self.emit(f"for (int64_t _rep = 0; _rep < {count}; _rep++) {{")
    _visit_loop_body_and_close(self, node.body, cleanup_streams=True)


def visit_Return(self, node: A.Return) -> None:
    """Generate return statement with recursion + auto-cleanup of
    owned class and string locals.

    Cleanup emission shares `_emit_class_cleanup` so that any new
    tracked-resource type (dict, mixed-ownership, future ones)
    gets handled in both implicit-return and explicit-return paths
    without having to wire it twice.
    """
    sync_unlock = ""
    if self._synchronized_mutex_name:
        sync_unlock = f"ailang_mutex_unlock({self._synchronized_mutex_name}); "

    # If returning a Variable directly, that var transfers ownership
    # to the caller — `_emit_class_cleanup(exclude=node.value)`
    # filters it out.
    exclude_node = node.value if isinstance(node.value, A.Variable) else None
    has_cleanup = self._has_any_cleanup(exclude_node)

    def emit_cleanup() -> None:
        if (
            self._current_class
            and self.current_function == f"{self._current_class}_destructor"
        ):
            self._emit_owned_field_cleanup(self._current_class, "self")
        self._emit_class_cleanup(exclude_node)

    if node.value:
        return_spec = getattr(self, "_current_ret_spec", None)
        if return_spec is not None and is_unbounded_spec(self, return_spec):
            # Value semantics: returning a local variable transfers its owned
            # object; all other borrowed values are cloned, while expression
            # temporaries already own their result.
            if isinstance(node.value, A.Variable):
                val = self.expr(node.value)
            else:
                val = owned_bigint_expr(self, node.value)
            if has_cleanup or self._guard_active or sync_unlock:
                self.emit(f"void *__ret_val = (void *)({val});")
                emit_cleanup()
                if self._guard_active:
                    self.emit("__ailang_end_recursion();")
                if sync_unlock:
                    self.emit(sync_unlock.rstrip())
                self.emit("return __ret_val;")
            else:
                self.emit(f"return (void *)({val});")
            return
        val = self.expr(node.value)
        if return_spec is not None:
            checked_int = checked_fixed_int_conversion_expr(
                self, node.value, val, return_spec
            )
            if checked_int is not None:
                val = checked_int
        if _returned_string_borrows_cleaned_array(self, node.value):
            # Materialize caller-owned storage before the array cleanup.
            self.used_helpers.add("strcat")
            self.runtime_needs.helpers.add("strcat")
            val = f'ailang_strcat("", {val})'
        # String literals are static -- safe to return directly without
        # the typeof temp wrapper.
        if val.startswith('"') and val.endswith('"'):
            emit_cleanup()
            if self._guard_active:
                self.emit("__ailang_end_recursion();")
            if sync_unlock:
                self.emit(sync_unlock.rstrip())
            self.emit(f"return {val};")
        elif not self._guard_active and not has_cleanup:
            if sync_unlock:
                self.emit(sync_unlock.rstrip())
            self.emit(f"return {val};")
        else:
            # Evaluate the expression into a typed temp; THEN cleanup;
            # THEN return. Lets the return expression read fields of
            # locals we're about to free without UAF.
            self.emit(_return_temp_open(self, val))
            emit_cleanup()
            if self._guard_active:
                self.emit("__ailang_end_recursion();")
            if sync_unlock:
                self.emit(sync_unlock.rstrip())
            self.emit("return __ret_val; }")
    else:
        emit_cleanup()
        if self._guard_active:
            self.emit("__ailang_end_recursion();")
        if sync_unlock:
            self.emit(sync_unlock.rstrip())
        if self._current_ret_type == "void":
            self.emit("return;")
        else:
            # Parser validation should reject this first, but the C backend
            # must not manufacture a value when a non-void function executes
            # a bare `return`.
            raise ValueError(
                "bare return is not valid in a non-void function"
            )


def visit_Break(self, _node: A.Break) -> None:
    """Generate break statement."""
    self.emit("break;")


def visit_Continue(self, _node: A.Continue) -> None:
    """Generate continue statement."""
    self.emit("continue;")


def visit_InlineAsm(self, node: A.InlineAsm) -> None:
    """Generate inline assembly using GCC/Clang __asm__ syntax."""
    # Escape the assembly code properly
    asm_code = node.code.replace("\\", "\\\\").replace('"', '\\"')

    # Build the asm statement
    if node.outputs or node.inputs or node.clobbers:
        # Extended asm with constraints
        parts = [f'"{asm_code}"']
        parts.append(f": {node.outputs}" if node.outputs else ": ")
        parts.append(f": {node.inputs}" if node.inputs else ": ")
        if node.clobbers:
            parts.append(f": {node.clobbers}")
        self.emit(f"__asm__ volatile ({' '.join(parts)});")
    else:
        # Simple asm (no operands)
        self.emit(f'__asm__ volatile ("{asm_code}");')


def visit_Block(self, node: A.Block) -> None:
    """Generate block of statements."""
    self.emit("{")
    self.indent += 1
    _visit_cache_scoped_body(self, node.body)
    self.indent -= 1
    self.emit("}")
    _drop_assigned_strlen_cache(self, node.body)


def visit_Match(self, node: A.Match) -> None:
    """Generate match as switch statement with optional destructuring."""
    expr_code = self.expr(node.expr)

    # Check if any case is a destructuring pattern
    has_destructuring = any(
        isinstance(case_val, A.MatchPattern) for case_val, _ in node.cases
    )

    if has_destructuring:
        # Generate if-else chain for destructuring match
        # Need to match on tag and extract fields
        expr_var = f"_match_expr_{id(node)}"
        expr_type = self._infer_type(node.expr)
        self.emit(f"{expr_type} {expr_var} = {expr_code};")

        first = True
        for case_val, case_body in node.cases:
            if isinstance(case_val, A.MatchPattern):
                # Destructuring pattern
                enum_name = case_val.enum_name
                variant_name = case_val.variant_name
                bindings = case_val.bindings

                if first:
                    self.emit(
                        f"if ({expr_var}.tag == {enum_name}_TAG_{variant_name}) {{"
                    )
                    first = False
                else:
                    self.emit(
                        f"}} else if ({expr_var}.tag == "
                        f"{enum_name}_TAG_{variant_name}) {{"
                    )

                self.indent += 1

                # Extract fields into local variables
                if enum_name in self.data_enums:
                    data_variants = self.data_enums[enum_name]
                    if variant_name in data_variants:
                        fields = data_variants[variant_name]
                        for (fname, ftype), binding in zip(
                            fields, bindings, strict=False
                        ):
                            ctype = self._ailang_type_to_c(ftype)
                            self.emit(
                                f"{ctype} {binding} = "
                                f"{expr_var}.data.{variant_name.lower()}.{fname};"
                            )
                            # Track string variables for print formatting
                            if ftype in ("string", "str"):
                                func_scope = self.current_function
                                if func_scope not in self._string_vars:
                                    self._string_vars[func_scope] = set()
                                self._string_vars[func_scope].add(binding)

                for stmt in case_body:
                    self.visit(stmt)
                self.indent -= 1
            else:
                # Simple value match (within destructuring match)
                val = self.expr(case_val)
                # For data enums, compare tags
                if first:
                    self.emit(f"if ({expr_var}.tag == {val}.tag) {{")
                    first = False
                else:
                    self.emit(f"}} else if ({expr_var}.tag == {val}.tag) {{")
                self.indent += 1
                for stmt in case_body:
                    self.visit(stmt)
                self.indent -= 1

        if node.default_case:
            self.emit("} else {")
            self.indent += 1
            for stmt in node.default_case:
                self.visit(stmt)
            self.indent -= 1

        self.emit("}")
    else:
        expr_type = self._infer_type(node.expr)
        is_string_match = expr_type in ("string", "str") or self._might_be_string(
            node.expr
        )
        can_switch = (not is_string_match) and all(
            isinstance(case_val, A.Number) and not case_val.is_float
            for case_val, _ in node.cases
        )

        if not can_switch:
            expr_var = f"_match_expr_{id(node)}"
            ctype = self._ailang_type_to_c(expr_type)
            self.emit(f"{ctype} {expr_var} = {expr_code};")

            first = True
            for case_val, case_body in node.cases:
                val = self.expr(case_val)
                if is_string_match or self._might_be_string(case_val):
                    self.used_helpers.add("string")
                    cond = f"(__ailang_strcmp_raw({expr_var}, {val}) == 0)"
                else:
                    cond = f"({expr_var} == {val})"

                if first:
                    self.emit(f"if ({cond}) {{")
                    first = False
                else:
                    self.emit(f"}} else if ({cond}) {{")
                self.indent += 1
                for stmt in case_body:
                    self.visit(stmt)
                self.indent -= 1

            if node.default_case:
                if first:
                    self.emit("{")
                else:
                    self.emit("} else {")
                self.indent += 1
                for stmt in node.default_case:
                    self.visit(stmt)
                self.indent -= 1

            if not first or node.default_case:
                self.emit("}")
            return

        # Simple switch statement for integer constant cases.
        self.emit(f"switch ({expr_code}) {{")
        self.indent += 1
        for case_val, case_body in node.cases:
            val = self.expr(case_val)
            self.emit(f"case {val}:")
            self.indent += 1
            for stmt in case_body:
                self.visit(stmt)
            self.emit("break;")
            self.indent -= 1
        if node.default_case:
            self.emit("default:")
            self.indent += 1
            for stmt in node.default_case:
                self.visit(stmt)
            self.emit("break;")
            self.indent -= 1
        self.indent -= 1
        self.emit("}")
