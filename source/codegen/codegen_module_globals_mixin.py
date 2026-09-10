"""Extracted responsibilities for :class:`_CodeGenModuleMixin`."""

from __future__ import annotations

from parser.ast import (
    StringLit,
    UnaryOp,
    VarDecl,
)
from typing import Any

from llvmlite import ir


class _CodeGenModuleGlobalsMixin:
    def generate_global_var(self: Any, node: VarDecl) -> None:
        """Generate a global variable declaration at module level.
        Creates an LLVM GlobalVariable with the specified type and initial value.
        Global variables are stored in self.globals for lookup during codegen.
        """
        # Skip if already defined (e.g., same global imported from multiple modules)
        if node.var_name in self.globals:
            return
        llvm_type = self.get_llvm_type(node.type_name)
        self.global_decl_types[node.var_name] = node.type_name
        # Set initializer based on type and init_value
        from parser.ast import ArrayLit, Bool, Number

        # Handle array literals specially - they need array type, not scalar
        if isinstance(node.init_value, ArrayLit):
            self._generate_global_array(node.var_name, node.init_value, node.is_const)
            return
        # Create the global variable for scalar types
        global_var = ir.GlobalVariable(self.module, llvm_type, node.var_name)
        # Use internal linkage for all globals in JIT mode
        # Public just affects visibility in module system, not LLVM linkage
        global_var.linkage = "internal"
        global_var.global_constant = node.is_const
        numeric_value = None
        if isinstance(node.init_value, Number):
            numeric_value = node.init_value.value
        elif (
            isinstance(node.init_value, UnaryOp)
            and node.init_value.op in {"+", "-"}
            and isinstance(node.init_value.operand, Number)
        ):
            numeric_value = node.init_value.operand.value
            if node.init_value.op == "-":
                numeric_value = -numeric_value

        if numeric_value is not None:
            # Check if target type is floating point
            is_float_type = isinstance(
                llvm_type, (ir.FloatType, ir.DoubleType, ir.HalfType)
            )
            if is_float_type or isinstance(numeric_value, float):
                global_var.initializer = ir.Constant(llvm_type, float(numeric_value))
            else:
                global_var.initializer = ir.Constant(llvm_type, int(numeric_value))
        elif isinstance(node.init_value, Bool):
            global_var.initializer = ir.Constant(
                llvm_type, 1 if node.init_value.value else 0
            )
        elif isinstance(node.init_value, StringLit):
            # For strings, create a global string constant and point to it
            str_const = self.create_string_constant(node.init_value.value)
            global_var.initializer = str_const
        else:
            # Default to zero initializer for complex expressions
            # (Would need runtime init for computed values)
            if isinstance(llvm_type, (ir.FloatType, ir.DoubleType)):
                global_var.initializer = ir.Constant(llvm_type, 0.0)
            else:
                global_var.initializer = ir.Constant(llvm_type, 0)
        # Register in globals dict for lookup
        self.globals[node.var_name] = global_var

    def _generate_global_array(
        self: Any, var_name: str, array_lit: Any, is_const: bool = False
    ) -> None:
        """Generate a global array from an ArrayLit AST node."""
        from parser.ast import Bool, Number

        elem_type: ir.Type
        if not array_lit.elements:
            # Empty array
            elem_type = ir.IntType(64)
            array_type = ir.ArrayType(elem_type, 0)
            global_var = ir.GlobalVariable(self.module, array_type, var_name)
            global_var.initializer = ir.Constant(array_type, [])
            global_var.linkage = "internal"
            global_var.global_constant = is_const
            self.globals[var_name] = global_var
            self.array_metadata[var_name] = (0, elem_type)
            return
        # Determine element type from first element
        first_elem = array_lit.elements[0]
        if isinstance(first_elem, Number):
            elem_type = ir.DoubleType() if first_elem.is_float else ir.IntType(64)
        elif isinstance(first_elem, Bool):
            elem_type = ir.IntType(1)
        elif isinstance(first_elem, StringLit):
            elem_type = ir.IntType(8).as_pointer()
        else:
            elem_type = ir.IntType(64)  # Default
        array_len = len(array_lit.elements)
        array_type = ir.ArrayType(elem_type, array_len)
        # Build initializer values
        init_values = []
        for elem in array_lit.elements:
            if isinstance(elem, Number):
                if isinstance(elem_type, ir.DoubleType):
                    init_values.append(ir.Constant(elem_type, float(elem.value)))
                else:
                    init_values.append(ir.Constant(elem_type, int(elem.value)))
            elif isinstance(elem, Bool):
                init_values.append(ir.Constant(elem_type, 1 if elem.value else 0))
            elif isinstance(elem, StringLit):
                str_const = self.create_string_constant(elem.value)
                init_values.append(str_const)
            else:
                # Default to zero for complex expressions
                init_values.append(ir.Constant(elem_type, 0))
        global_var = ir.GlobalVariable(self.module, array_type, var_name)
        global_var.initializer = ir.Constant(array_type, init_values)
        global_var.linkage = "internal"
        global_var.global_constant = is_const
        self.globals[var_name] = global_var
        self.array_metadata[var_name] = (array_len, elem_type)

    def generate_global_assign(self: Any, node: Any) -> None:
        """Generate a global assignment (for arrays and simple values).
        Handles global scope assignments like:
            arr = [1, 2, 3, 4, 5]
            SIZE = 100
        """
        from parser.ast import ArrayLit, Bool, Number

        var_name = node.var_name
        value = node.value
        elem_type: ir.Type
        llvm_type: ir.Type
        if isinstance(value, ArrayLit):
            # Global array - create as global constant array
            if not value.elements:
                # Empty array - create null pointer
                elem_type = ir.IntType(64)
                array_type = ir.ArrayType(elem_type, 0)
                global_var = ir.GlobalVariable(self.module, array_type, var_name)
                global_var.initializer = ir.Constant(array_type, [])
                global_var.linkage = "internal"
                self.globals[var_name] = global_var
                self.array_metadata[var_name] = (0, elem_type)
                return
            # Determine element type from first element
            first_elem = value.elements[0]
            if isinstance(first_elem, Number):
                elem_type = ir.DoubleType() if first_elem.is_float else ir.IntType(64)
            elif isinstance(first_elem, Bool):
                elem_type = ir.IntType(1)
            elif isinstance(first_elem, StringLit):
                elem_type = ir.IntType(8).as_pointer()
            else:
                elem_type = ir.IntType(64)  # Default
            array_len = len(value.elements)
            array_type = ir.ArrayType(elem_type, array_len)
            # Build initializer values
            init_values = []
            for elem in value.elements:
                if isinstance(elem, Number):
                    if isinstance(elem_type, ir.DoubleType):
                        init_values.append(ir.Constant(elem_type, float(elem.value)))
                    else:
                        init_values.append(ir.Constant(elem_type, int(elem.value)))
                elif isinstance(elem, Bool):
                    init_values.append(ir.Constant(elem_type, 1 if elem.value else 0))
                elif isinstance(elem, StringLit):
                    str_const = self.create_string_constant(elem.value)
                    init_values.append(str_const)
                else:
                    # Default to zero for complex expressions
                    init_values.append(ir.Constant(elem_type, 0))
            global_var = ir.GlobalVariable(self.module, array_type, var_name)
            global_var.initializer = ir.Constant(array_type, init_values)
            global_var.linkage = "internal"
            self.globals[var_name] = global_var
            self.array_metadata[var_name] = (array_len, elem_type)
        elif isinstance(value, Number):
            # Global scalar constant
            if value.is_float:
                llvm_type = ir.DoubleType()
                init_val = ir.Constant(llvm_type, float(value.value))
            else:
                llvm_type = ir.IntType(64)
                init_val = ir.Constant(llvm_type, int(value.value))
            global_var = ir.GlobalVariable(self.module, llvm_type, var_name)
            global_var.initializer = init_val
            global_var.linkage = "internal"
            self.globals[var_name] = global_var
        elif isinstance(value, Bool):
            llvm_type = ir.IntType(1)
            global_var = ir.GlobalVariable(self.module, llvm_type, var_name)
            global_var.initializer = ir.Constant(llvm_type, 1 if value.value else 0)
            global_var.linkage = "internal"
            self.globals[var_name] = global_var
        elif isinstance(value, StringLit):
            # Global string - create as i8* pointing to constant
            str_const = self.create_string_constant(value.value)
            llvm_type = ir.IntType(8).as_pointer()
            global_var = ir.GlobalVariable(self.module, llvm_type, var_name)
            global_var.initializer = str_const
            global_var.linkage = "internal"
            self.globals[var_name] = global_var

    def generate_enum(self: Any, node: Any) -> None:
        """Register enum values as constants, with support for data-carrying enums."""
        enum_name = node.name
        # Check if this enum has data-carrying variants
        has_data = (
            node.has_data_variants() if hasattr(node, "has_data_variants") else False
        )
        if has_data:
            # Data-carrying enum - create tagged union struct
            self._generate_data_enum(node)
        else:
            # Simple enum - store as integer constants
            for value_name, value_int in node.values:
                full_name = f"{enum_name}.{value_name}"
                self.enum_values[full_name] = value_int

    def _generate_data_enum(self: Any, node: Any) -> None:
        """Generate LLVM tagged union type for data-carrying enum."""
        enum_name = node.name
        variant_data: dict[str, list[tuple[str, str]]] = {}
        tag_values: dict[str, int] = {}
        # Collect variant information
        for idx, variant in enumerate(node.variants):
            tag_values[variant.name] = idx
            if variant.fields:
                variant_data[variant.name] = variant.fields
            else:
                variant_data[variant.name] = []
            # Also store simple enum value for backwards compatibility
            full_name = f"{enum_name}.{variant.name}"
            self.enum_values[full_name] = idx
        # Create union of all variant structs
        # Find the largest variant to determine union size
        max_size = 0
        variant_types: dict[str, ir.Type] = {}
        for variant_name, fields in variant_data.items():
            if fields:
                field_types = [self.get_llvm_type(ftype) for _, ftype in fields]
                variant_type = ir.LiteralStructType(field_types)
                variant_types[variant_name] = variant_type
                # Estimate size (rough - just count bytes)
                size = sum(self._type_size(ft) for ft in field_types)
                max_size = max(max_size, size)
            else:
                variant_types[variant_name] = ir.LiteralStructType([])
        # Create the tagged union struct: { i32 tag, [max_size x i8] data }
        # Using byte array for union data to handle different variant sizes
        tag_type = ir.IntType(32)
        data_size = max(max_size, 8)  # Minimum 8 bytes for data
        data_type = ir.ArrayType(ir.IntType(8), data_size)
        enum_struct = ir.LiteralStructType([tag_type, data_type])
        # Store in registries
        self.data_enums[enum_name] = variant_data
        self.data_enum_types[enum_name] = enum_struct
        self.data_enum_tags[enum_name] = tag_values
        # Also register as a record type for field access
        self.record_types[enum_name] = enum_struct

    def _type_size(self: Any, llvm_type: ir.Type) -> int:
        """Estimate size in bytes of an LLVM type."""
        if isinstance(llvm_type, ir.IntType):
            return (llvm_type.width + 7) // 8
        if isinstance(llvm_type, ir.FloatType):
            return 4
        if isinstance(llvm_type, ir.DoubleType):
            return 8
        if isinstance(llvm_type, ir.PointerType):
            return 8  # 64-bit pointers
        if isinstance(llvm_type, ir.ArrayType):
            return llvm_type.count * self._type_size(llvm_type.element)
        if isinstance(llvm_type, ir.LiteralStructType):
            return sum(self._type_size(e) for e in llvm_type.elements)
        return 8  # Default
