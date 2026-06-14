"""CodeGen class/record emission mixin."""

from __future__ import annotations

from parser.ast import Function
from typing import Any, Optional

from ast_access import arg_at
from llvmlite import ir


def _is_string_type(type_name: Any) -> bool:
    return str(type_name).strip().lower() in {"string", "str"}


def _string_len_name(name: str) -> str:
    return f"__ailang_{name}_len"


class _CodeGenClassRecordMixin:
    current_class: Optional[str]

    def __init__(self: Any) -> None:
        # Linter-only declarations for attributes reassigned while generating
        # class methods. CodeGen.__init__ owns the actual runtime state.
        self.current_class = None
        self.current_this: Any = None
        self.func: Optional[ir.Function] = None
        self.locals: dict[str, Any] = {}
        self.local_decl_types: dict[str, Any] = {}
        self.var_signedness: dict[str, bool] = {}
        self.value_signedness: dict[str, bool] = {}
        self.builder: Optional[ir.IRBuilder] = None
        self._current_function_name: Optional[str] = None
        self._current_function_body: Any = None
        self._unchecked_mode: bool = False
        self._inline_this_stack_var: Optional[str] = None

    def generate_record(self: Any, node: Any) -> None:
        """Generate LLVM struct type for record definition"""
        llvm_fields = []
        for _, field_type in node.fields:
            llvm_type = self.get_llvm_type(field_type)
            llvm_fields.append(llvm_type)
        record_type = ir.LiteralStructType(llvm_fields)
        self.record_types[node.name] = record_type
        self.record_type_ids[id(record_type)] = node.name
        self.record_fields[node.name] = node.fields

    def generate_class(self: Any, node: Any) -> None:
        """Generate LLVM struct type and methods for class definition."""
        class_name = node.name
        llvm_fields = []
        field_info = []
        for _, field_name, field_type, _ in node.fields:
            llvm_type = self.get_llvm_type(field_type)
            llvm_fields.append(llvm_type)
            field_info.append((field_name, field_type))
            if _is_string_type(field_type):
                llvm_fields.append(ir.IntType(64))
                field_info.append((_string_len_name(field_name), "int"))
        class_type = ir.LiteralStructType(llvm_fields)
        self.class_types[class_name] = class_type
        self.record_types[class_name] = class_type
        self.record_type_ids[id(class_type)] = class_name
        self.record_fields[class_name] = field_info
        self.class_fields[class_name] = node.fields
        self.class_methods[class_name] = node.methods
        self._register_class_method_recursion(class_name, node.methods)
        for method in node.methods:
            self._generate_method(class_name, class_type, method)
            if method.name == f"~{class_name}":
                destructor_name = f"{class_name}_~{class_name}"
                if destructor_name in self.functions:
                    self.class_destructors[class_name] = self.functions[destructor_name]

    def _register_class_method_recursion(
        self: Any, class_name: str, methods: list[Function]
    ) -> None:
        """Make class methods visible to the recursion-guard analysis."""
        from parser.ast import MethodCall

        method_names = {method.name for method in methods}
        mangled = {name: f"{class_name}_{name}" for name in method_names}
        graph: dict[str, set[str]] = {mangled[name]: set() for name in method_names}
        for method in methods:
            source = mangled[method.name]
            for child in self._walk_ast_nodes(method.body):
                if isinstance(child, MethodCall) and child.method_name in mangled:
                    graph[source].add(mangled[child.method_name])

        recursive: set[str] = set()

        def reaches(start: str, current: str, seen: set[str]) -> bool:
            for nxt in graph.get(current, set()):
                if nxt == start:
                    return True
                if nxt in seen:
                    continue
                seen.add(nxt)
                if reaches(start, nxt, seen):
                    return True
            return False

        for name in graph:
            if reaches(name, name, set()):
                recursive.add(name)

        analyzed = set(getattr(self, "_recursion_analyzed_functions", set()))
        analyzed.update(graph)
        self._recursion_analyzed_functions = analyzed
        existing_recursive = set(getattr(self, "_recursive_functions", set()) or set())
        existing_recursive.update(recursive)
        self._recursive_functions = existing_recursive

    def _generate_method(
        self: Any, class_name: str, class_type: Any, method: Function
    ) -> None:
        """Generate a class method as a function with 'this' pointer."""
        mangled_name = f"{class_name}_{method.name}"
        this_type = class_type.as_pointer()
        param_types = [this_type]
        param_names = ["this"]
        for param_info in method.params:
            pname = param_info[0]
            ptype = param_info[1]
            param_types.append(self.get_llvm_type(ptype))
            param_names.append(pname)
            if _is_string_type(ptype):
                param_types.append(ir.IntType(64))
                param_names.append(_string_len_name(pname))
        ret_type = self.get_llvm_type(method.return_type)
        func_type = ir.FunctionType(ret_type, param_types)
        func = ir.Function(self.module, func_type, name=mangled_name)
        func.attributes.add("nounwind")
        decorators = [
            decorator.lstrip("@").lower()
            for decorator in (getattr(method, "decorators", []) or [])
        ]
        if "inline" in decorators:
            func.attributes.add("alwaysinline")
        elif "noinline" in decorators:
            func.attributes.add("noinline")
        else:
            body_size = len(getattr(method, "body", []) or [])
            if body_size <= 10:
                func.attributes.add("alwaysinline")
            elif body_size <= 20:
                func.attributes.add("inlinehint")
        self.functions[mangled_name] = func
        for i, arg in enumerate(func.args):
            arg.name = param_names[i]
        saved_func = self.func
        saved_builder = self.builder
        saved_locals = self.locals.copy()
        saved_local_types = getattr(self, "local_decl_types", {}).copy()
        saved_signedness = self.var_signedness.copy()
        saved_value_signedness = getattr(self, "value_signedness", {}).copy()
        saved_current_class = self.current_class
        saved_current_this = self.current_this
        saved_function_name = getattr(self, "_current_function_name", None)
        saved_function_body = getattr(self, "_current_function_body", None)
        saved_unchecked = getattr(self, "_unchecked_mode", False)
        saved_inline_this = getattr(self, "_inline_this_stack_var", None)
        self.current_class = class_name
        self.func = func
        self.locals = {}
        self.local_decl_types = {}
        self.var_signedness = {}
        self.value_signedness = {}
        block = func.append_basic_block(name="entry")
        self.builder = ir.IRBuilder(block)
        self.current_this = arg_at(func, 0)
        self._current_function_name = mangled_name
        self._current_function_body = method.body
        self._unchecked_mode = "unchecked" in decorators
        self._inline_this_stack_var = None
        self.locals["this"] = arg_at(func, 0)
        for i, param_info in enumerate(method.params):
            pname = param_info[0]
            self.locals[pname] = func.args[param_names.index(pname)]
            self.local_decl_types[pname] = str(param_info[1])
            hidden_len = _string_len_name(pname)
            if hidden_len in param_names:
                self.locals[hidden_len] = func.args[param_names.index(hidden_len)]
        self._maybe_emit_function_entry_guards(mangled_name, decorators)
        for stmt in method.body:
            self.stmt_generator.generate_stmt(stmt)
        self._emit_implicit_return_if_needed(mangled_name)
        self.current_class = saved_current_class
        self.current_this = saved_current_this
        self._current_function_name = saved_function_name
        self._current_function_body = saved_function_body
        self._unchecked_mode = saved_unchecked
        self._inline_this_stack_var = saved_inline_this
        self.func = saved_func
        self.builder = saved_builder
        self.locals = saved_locals
        self.local_decl_types = saved_local_types
        self.var_signedness = saved_signedness
        self.value_signedness = saved_value_signedness
