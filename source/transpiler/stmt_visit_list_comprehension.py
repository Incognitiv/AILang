"""C statement helper for list comprehension lowering."""

from __future__ import annotations

from parser import ast as A


def _generate_list_comprehension(
    self, var_name: str, node: A.ListComprehension
) -> None:
    """Generate code for list comprehension."""
    self.emit(f"{var_name} = array_new(8);")

    if isinstance(node.iterable, A.Range):
        start = self.expr(node.iterable.start)
        end = self.expr(node.iterable.end)
        loop_var = node.var_name
        cond = (
            f"{loop_var} <= {end}" if node.iterable.inclusive else f"{loop_var} < {end}"
        )
        self.emit(f"for (int64_t {loop_var} = {start}; {cond}; {loop_var}++) {{")
        self.indent += 1
    else:
        arr = self.expr(node.iterable)
        loop_var = node.var_name
        idx_var = f"_idx_{id(node)}"
        self.emit(
            f"for (int64_t {idx_var} = 0; {idx_var} < {arr}.length; {idx_var}++) {{"
        )
        self.indent += 1
        self.emit(f"int64_t {loop_var} = {arr}.data[{idx_var}];")

    if node.condition:
        cond_code = self.expr(node.condition)
        self.emit(f"if ({cond_code}) {{")
        self.indent += 1

    expr_code = self.expr(node.expr)
    self.emit(f"{var_name} = array_push({var_name}, {expr_code});")

    if node.condition:
        self.indent -= 1
        self.emit("}")

    self.indent -= 1
    self.emit("}")
