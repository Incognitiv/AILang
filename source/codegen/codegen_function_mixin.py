"""CodeGen function emission mixin."""

from __future__ import annotations

import contextlib
from parser.ast import ASTNode
from typing import Any, Optional

from abi_symbols import has_export_decorator
from ast_access import arg_at
from llvmlite import ir


class _CodeGenFunctionMixin:
    var_signedness: dict[str, bool]
    value_signedness: dict[str, bool]

    def __init__(self: Any) -> None:
        # Linter-only declarations for attributes reassigned while generating
        # methods/functions. CodeGen.__init__ owns the actual runtime state.
        self.builder: Optional[ir.IRBuilder] = None
        self._pending_di_sp: Any = None
        self._pending_di_sp_line: int = 0
        self.func: Optional[ir.Function] = None
        self.locals: dict[str, Any] = {}
        self.local_decl_types: dict[str, Any] = {}
        self._synchronized_mutex_ptr: Any = None
        self._fastmath_mode = False
        self._unchecked_mode = False
        self.scope_cleanup_stack: list[list[tuple[str, str, Any]]] = [[]]
        self.temp_strings: set[str] = set()
        self._current_di_sp: Any = None
        self._string_arena: Any = None
        self._request_arena_slot: Any = None
        self._current_function_name: Optional[str] = None
        self.argc_global: Optional[ir.GlobalVariable] = None
        self.argv_global: Optional[ir.GlobalVariable] = None
        self._stack_class_locals: set[str] = set()
        self._stack_class_cleanup_plans: dict[str, Any] = {}
        self._stack_array_field_values: dict[tuple[str, str], tuple[Any, ...]] = {}
        self._inline_this_stack_var: Optional[str] = None
        self._llvm_length_only_string_locals: set[str] = set()

    def _resolved_function_name(self: Any, node: Any) -> str:
        func_name = node.name
        if not getattr(node, "is_public", True):
            func_name = f"_{func_name}"
        return func_name

    def _record_source_location(self: Any, node: Any) -> tuple[str, int]:
        src_path = getattr(node, "_source_path", "") or self._compile_source_file
        node_line = getattr(node, "line", 0)
        if src_path:
            self.source_map[node.name] = (src_path, node_line)
        return src_path, node_line

    def _prepare_pending_di(
        self: Any, node: Any, src_path: str, node_line: int
    ) -> None:
        # DWARF: attach a !DISubprogram so external tools (perf, gdb,
        # llvm-symbolizer) can resolve PC ranges back to AILang names.
        self._pending_di_sp = self.emit_dwarf_subprogram(
            self.functions[node.name], node.name, src_path, node_line
        )
        self._pending_di_sp_line = node_line

    def _save_function_generation_state(self: Any) -> dict[str, Any]:
        return {
            "func": getattr(self, "func", None),
            "locals": getattr(self, "locals", {}).copy(),
            "local_decl_types": getattr(self, "local_decl_types", {}).copy(),
            "scope_cleanup_stack": getattr(self, "scope_cleanup_stack", [[]]),
            "temp_strings": getattr(self, "temp_strings", set()).copy(),
            "di_sp": self._current_di_sp,
            "string_arena": self._string_arena,
            "request_arena_slot": self._request_arena_slot,
            "function_name": self._current_function_name,
            "function_body": getattr(self, "_current_function_body", None),
            "stack_array_field_values": getattr(
                self, "_stack_array_field_values", {}
            ).copy(),
            "inline_this_stack_var": getattr(self, "_inline_this_stack_var", None),
            "llvm_length_only_string_locals": getattr(
                self, "_llvm_length_only_string_locals", set()
            ).copy(),
            "stack_class_locals": getattr(self, "_stack_class_locals", set()).copy(),
            "stack_class_cleanup_plans": getattr(
                self, "_stack_class_cleanup_plans", {}
            ).copy(),
            "loop_stack_class_cleanup": [
                list(scope) for scope in getattr(self, "_loop_stack_class_cleanup", [])
            ],
        }

    def _apply_function_decorators(self: Any, decorators: list[str]) -> None:
        for decorator in decorators:
            if decorator == "inline":
                self.func.attributes.add("alwaysinline")
            elif decorator == "noinline":
                self.func.attributes.add("noinline")
            elif has_export_decorator([decorator]):
                self.func.linkage = "external"
            elif decorator == "pure":
                self.func.attributes.add("readonly")
                with contextlib.suppress(ValueError):
                    self.func.attributes.add("nofree")
            elif decorator == "noalias":
                for arg in self.func.args:
                    if isinstance(arg.type, ir.PointerType):
                        arg.add_attribute("noalias")
                        arg.add_attribute("nocapture")
            elif decorator == "fastmath":
                # llvmlite has no function-level unsafe-fp-math flag.
                self._fastmath_mode = True
            elif decorator == "unchecked":
                self._unchecked_mode = True

    def _apply_auto_inline_heuristics(
        self: Any, node: Any, decorators: list[str]
    ) -> None:
        body_size = len(node.body) if hasattr(node, "body") else 0
        has_inline_decorator = "inline" in decorators or "noinline" in decorators
        if not has_inline_decorator and node.name != "main":
            if body_size <= 10:
                self.func.attributes.add("alwaysinline")
            elif body_size <= 20:
                self.func.attributes.add("inlinehint")

    def _initialize_function_context(self: Any, node: Any) -> None:
        self.func = self.functions[node.name]
        self.locals = {}
        self.local_decl_types = {}
        self.var_signedness = {}
        self.value_signedness = {}
        self._synchronized_mutex_ptr = None
        self.scope_cleanup_stack = [[]]
        self.temp_strings = set()
        self._request_arena_slot = None
        self._current_function_name = node.name
        self._current_function_body = getattr(node, "body", [])
        from codegen.strlen_scalarization import collect_length_only_str_locals

        self._llvm_length_only_string_locals = collect_length_only_str_locals(
            self._current_function_body
        )
        self._stack_array_field_values = {}
        self._inline_this_stack_var = None
        self._stack_class_locals = set()
        self._stack_class_cleanup_plans = {}
        self._loop_stack_class_cleanup: list[list[str]] = []

    def _node_children(self: Any, node: Any) -> list[Any]:
        from parser import ast as A

        children: list[Any] = []
        node_vars = vars(node) if hasattr(node, "__dict__") else {}
        for value in node_vars.values():
            if isinstance(value, A.ASTNode):
                children.append(value)
            elif isinstance(value, (list, tuple)):
                children.extend(item for item in value if isinstance(item, A.ASTNode))
        return children

    def _walk_nodes(self: Any, nodes: list[Any]):
        for node in nodes:
            yield node
            yield from self._walk_nodes(self._node_children(node))

    def _expr_uses_var_stack_safely(self: Any, node: Any, var_name: str) -> bool:
        from parser import ast as A

        if isinstance(node, A.Variable):
            return node.name != var_name
        if isinstance(node, A.MethodCall):
            if (
                isinstance(node.object_expr, A.Variable)
                and node.object_expr.name == var_name
            ):
                return all(
                    self._expr_uses_var_stack_safely(arg, var_name) for arg in node.args
                )
            return all(
                self._expr_uses_var_stack_safely(child, var_name)
                for child in self._node_children(node)
            )
        if isinstance(node, (A.FieldAccess, A.SafeFieldAccess)):
            if (
                isinstance(node.object_expr, A.Variable)
                and node.object_expr.name == var_name
            ):
                return True
            return self._expr_uses_var_stack_safely(node.object_expr, var_name)
        return all(
            self._expr_uses_var_stack_safely(child, var_name)
            for child in self._node_children(node)
        )

    def _stmt_uses_var_stack_safely(self: Any, node: Any, var_name: str) -> bool:
        from parser import ast as A

        if isinstance(node, A.VarDecl):
            if node.var_name == var_name:
                return True
            if node.init_value is not None:
                return self._expr_uses_var_stack_safely(node.init_value, var_name)
            return True
        if isinstance(node, A.Assign):
            if node.var_name == var_name:
                return False
            return self._expr_uses_var_stack_safely(node.value, var_name)
        if isinstance(node, A.FieldAssign):
            object_ok = True
            if not (
                isinstance(node.object_expr, A.Variable)
                and node.object_expr.name == var_name
            ):
                object_ok = self._expr_uses_var_stack_safely(node.object_expr, var_name)
            return object_ok and self._expr_uses_var_stack_safely(node.value, var_name)
        return all(
            self._expr_uses_var_stack_safely(child, var_name)
            for child in self._node_children(node)
        )

    def _find_stack_class_locals(self: Any, body: list[Any]) -> set[str]:
        from parser import ast as A
        from parser.ast import parsed_type_to_str

        candidates: set[str] = set()
        for stmt in self._walk_nodes(body):
            if not isinstance(stmt, A.VarDecl):
                continue
            if not isinstance(stmt.init_value, A.NewExpr):
                continue
            type_name = parsed_type_to_str(stmt.type_name)
            if type_name == stmt.init_value.type_name and type_name in self.class_types:
                candidates.add(stmt.var_name)

        return {
            var_name
            for var_name in candidates
            if all(self._stmt_uses_var_stack_safely(stmt, var_name) for stmt in body)
        }

    def _create_entry_builder(self: Any, node_line: int) -> None:
        block = self.current_function.append_basic_block(name="entry")
        self.builder = ir.IRBuilder(block)
        if getattr(self, "_module_uses_string_arena", True):
            i8_ptr = ir.IntType(8).as_pointer()
            self._request_arena_slot = self.builder.alloca(
                i8_ptr, name="request_arena_slot"
            )
            self.builder.store(ir.Constant(i8_ptr, None), self._request_arena_slot)
        pending_sp = getattr(self, "_pending_di_sp", None)
        if pending_sp is not None:
            self.builder.debug_metadata = self._make_di_location(
                pending_sp, getattr(self, "_pending_di_sp_line", 0)
            )
            self._current_di_sp = pending_sp
            self._pending_di_sp = None
        else:
            self._current_di_sp = None

    def _maybe_create_main_string_arena(self: Any, node_name: str) -> None:
        if node_name == "main" and getattr(self, "_module_uses_string_arena", True):
            arena_size = ir.Constant(ir.IntType(64), self._string_arena_size)
            self._string_arena = self._arena_gen.create_arena(arena_size)

    def _maybe_store_main_argv(self: Any, node_name: str) -> None:
        if node_name != "main" or len(self.current_function.args) < 2:
            return
        argc_arg = arg_at(self.current_function, 0)
        argv_arg = arg_at(self.current_function, 1)
        char_ptr_ptr = ir.IntType(8).as_pointer().as_pointer()
        if argc_arg.type != ir.IntType(32) or argv_arg.type != char_ptr_ptr:
            return

        int64 = ir.IntType(64)
        if getattr(self, "argc_global", None) is None:
            argc_global = ir.GlobalVariable(self.module, int64, "__ailang_argc")
            argc_global.initializer = ir.Constant(int64, 0)
            argc_global.linkage = "internal"
            self.argc_global = argc_global
        if getattr(self, "argv_global", None) is None:
            argv_global = ir.GlobalVariable(self.module, char_ptr_ptr, "__ailang_argv")
            argv_global.initializer = ir.Constant(char_ptr_ptr, None)
            argv_global.linkage = "internal"
            self.argv_global = argv_global

        argc64 = self.builder.sext(argc_arg, int64, name="main_argc64")
        self.builder.store(argc64, self.argc_global)
        self.builder.store(argv_arg, self.argv_global)

    def _maybe_emit_function_entry_guards(
        self: Any, node_name: str, decorators: list[str]
    ) -> None:
        if self._function_needs_recursion_guard(node_name):
            self._emit_recursion_check(node_name)
        if "synchronized" in decorators:
            self._emit_synchronized_lock(node_name)
        self.emit_profile_enter(node_name)

    def _function_needs_recursion_guard(
        self: Any, node_name: str | None = None
    ) -> bool:
        if self._unchecked_mode:
            return False
        name = node_name or getattr(self, "_current_function_name", None)
        analyzed: set[str] = getattr(self, "_recursion_analyzed_functions", set())
        if name not in analyzed:
            return True
        recursive = getattr(self, "_recursive_functions", None)
        if not recursive:
            return False
        return name in recursive

    def _bind_function_parameters(self: Any, node: Any) -> None:
        mutated_params = self._analyze_param_mutations(node)
        for i, param_info in enumerate(node.params):
            if len(param_info) == 2:
                param_name, param_type = param_info
            else:
                param_name, param_type, _default = param_info
            arg = self.current_function.args[i]
            arg.name = param_name
            type_str = param_type if isinstance(param_type, str) else str(param_type)
            is_signed = not type_str.lower().startswith("u")
            self.var_signedness[param_name] = is_signed
            self.local_decl_types[param_name] = type_str
            if param_name in mutated_params:
                param_slot = self.builder.alloca(arg.type, name=f"{param_name}_slot")
                self.builder.store(arg, param_slot)
                self.locals[param_name] = param_slot
                self.set_signedness(param_slot, is_signed)
            else:
                self.locals[param_name] = arg
                self.set_signedness(arg, is_signed)

    def _emit_implicit_return_if_needed(self: Any, node_name: str) -> None:
        if self.current_builder.block.is_terminated:
            return
        plans = getattr(self, "_stack_class_cleanup_plans", {})
        if plans:
            from transpiler.emit_statements_control_data import (
                _emit_stack_class_cleanup,
            )

            for var_name in reversed(list(plans)):
                _emit_stack_class_cleanup(self.stmt_generator, var_name)
        self.cleanup_all_scopes()
        if node_name == "main" and self._string_arena is not None:
            self._arena_gen.arena_destroy(self._string_arena)
        if self._function_needs_recursion_guard(node_name):
            self._emit_recursion_decrement()
        self.emit_profile_exit(node_name)
        ret_type = self.current_function.function_type.return_type
        if isinstance(ret_type, ir.VoidType):
            self.current_builder.ret_void()
        else:
            self.current_builder.ret(self.default_value(ret_type))

    def _restore_function_generation_state(self: Any, state: dict[str, Any]) -> None:
        self._unchecked_mode = False
        self._string_arena = state["string_arena"]
        self._request_arena_slot = state["request_arena_slot"]
        self._current_function_name = state.get("function_name")
        self._current_function_body = state.get("function_body")
        self._stack_array_field_values = state.get("stack_array_field_values", {})
        self._inline_this_stack_var = state.get("inline_this_stack_var")
        self._llvm_length_only_string_locals = state.get(
            "llvm_length_only_string_locals", set()
        )
        self._stack_class_locals = state.get("stack_class_locals", set())
        self._stack_class_cleanup_plans = state.get("stack_class_cleanup_plans", {})
        self._loop_stack_class_cleanup = state.get("loop_stack_class_cleanup", [])
        if state["func"] is not None:
            self.func = state["func"]
            self.locals = state["locals"]
            self.local_decl_types = state["local_decl_types"]
            self.scope_cleanup_stack = state["scope_cleanup_stack"]
            self.temp_strings = state["temp_strings"]
        self._current_di_sp = state["di_sp"]

    def generate_function(self: Any, node: Any) -> None:
        """Generate function body"""
        # Skip if already generated (transitive imports can re-export same function)
        func_name = self._resolved_function_name(node)
        if func_name in self.functions and self.functions[func_name].basic_blocks:
            return
        src_path, node_line = self._record_source_location(node)
        self._prepare_pending_di(node, src_path, node_line)
        saved_state = self._save_function_generation_state()
        saved_strlen_cache = getattr(self, "_llvm_strlen_cache", None)
        self._llvm_strlen_cache = {}
        self._initialize_function_context(node)
        decorators = getattr(node, "decorators", [])
        self._apply_function_decorators(decorators)
        self._apply_auto_inline_heuristics(node, decorators)
        self._create_entry_builder(node_line)
        self._maybe_store_main_argv(node.name)
        self._maybe_create_main_string_arena(node.name)
        self._maybe_emit_function_entry_guards(node.name, decorators)
        self._bind_function_parameters(node)
        self._stack_class_locals = self._find_stack_class_locals(node.body)
        for stmt in node.body:
            self.stmt_generator.generate_stmt(stmt)
        self._emit_implicit_return_if_needed(node.name)
        self._restore_function_generation_state(saved_state)
        if saved_strlen_cache is None:
            if hasattr(self, "_llvm_strlen_cache"):
                delattr(self, "_llvm_strlen_cache")
        else:
            self._llvm_strlen_cache = saved_strlen_cache

    def generate_stmt(self: Any, node: ASTNode) -> None:
        """Delegates statement generation to StmtGenerator."""
        self.stmt_generator.generate_stmt(node)

    def generate_expr(self: Any, node: ASTNode) -> ir.Value:
        """Delegates expression generation to ExprGenerator."""
        return self.expr_generator.generate_expr(node)
