"""
CollectionHelpers - service for LLVM channel/dict runtime helper builders.

Phase A6 continuation of the LLVM-side architectural pivot.
"""

from __future__ import annotations

from typing import Any

from ast_access import arg_at
from llvmlite import ir

from .collection_helpers_types import get_channel_type as _get_channel_type


class CollectionHelpers:
    """Channel/dict helper builders for the LLVM backend."""

    def __init__(self, codegen: Any) -> None:
        self._cg = codegen

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cg, name)

    def get_channel_type(self) -> ir.LiteralStructType:
        return _get_channel_type(self)

    # ------------------------------------------------------------------
    # Dictionary runtime functions (Smart Dict with auto-type detection)
    # Type tags: 0=int, 1=float, 2=string, 3=bool, 4=pointer/array
    # ------------------------------------------------------------------

    # Type tag constants for dict values
    DICT_TYPE_INT = 0
    DICT_TYPE_FLOAT = 1
    DICT_TYPE_STRING = 2
    DICT_TYPE_BOOL = 3
    DICT_TYPE_POINTER = 4
    DICT_FIELD_CAPACITY = 0
    DICT_FIELD_SIZE = 1
    DICT_FIELD_KEYS = 2
    DICT_FIELD_VALUES = 3
    DICT_FIELD_TYPES = 4

    def get_dict_type(self) -> ir.LiteralStructType:
        """Get the dict struct type: {capacity, size, keys**, values*, types*}"""
        if self._cg.dict_type is None:
            char_ptr = ir.IntType(8).as_pointer()
            # Dict struct: { i64 capacity, i64 size, i8** keys, i64* values, i8* types }
            self._cg.dict_type = ir.LiteralStructType(
                [
                    ir.IntType(64),  # capacity
                    ir.IntType(64),  # size
                    char_ptr.as_pointer(),  # keys (array of string pointers)
                    ir.IntType(
                        64
                    ).as_pointer(),  # values (array of i64 - stores all types)
                    ir.IntType(8).as_pointer(),  # types (array of type tags)
                ]
            )
        return self._cg.dict_type

    def _dict_field_ptr(
        self,
        builder: ir.IRBuilder,
        dict_ptr: ir.Value,
        zero: ir.Constant,
        field_index: int,
        name: str,
    ) -> ir.Value:
        return builder.gep(
            dict_ptr,
            [zero, ir.Constant(ir.IntType(32), field_index)],
            name=name,
        )

    def _load_dict_fields(
        self,
        builder: ir.IRBuilder,
        dict_ptr: ir.Value,
        zero: ir.Constant,
        fields: list[tuple[str, int, str]],
    ) -> dict[str, ir.Value]:
        loaded: dict[str, ir.Value] = {}
        for field_name, field_index, load_name in fields:
            ptr = self._dict_field_ptr(builder, dict_ptr, zero, field_index, field_name)
            loaded[field_name] = ptr
            loaded[load_name] = builder.load(ptr, name=load_name)
        return loaded

    def _emit_dict_key_search(
        self,
        func: ir.Function,
        builder: ir.IRBuilder,
        size: ir.Value,
        keys_ptr: ir.Value,
        key: ir.Value,
        found_block: ir.Block,
        not_found_block: ir.Block,
        prefix: str,
    ) -> ir.Value:
        strcmp = self._cg.get_strcmp()
        loop_block = func.append_basic_block(f"{prefix}_loop")
        check_block = func.append_basic_block(f"{prefix}_check")
        next_block = func.append_basic_block(f"{prefix}_next")

        i_ptr = builder.alloca(ir.IntType(64), name=f"{prefix}_i")
        builder.store(ir.Constant(ir.IntType(64), 0), i_ptr)
        builder.branch(loop_block)

        builder.position_at_end(loop_block)
        index = builder.load(i_ptr, name=f"{prefix}_i_val")
        in_range = builder.icmp_signed("<", index, size)
        builder.cbranch(in_range, check_block, not_found_block)

        builder.position_at_end(check_block)
        key_i_ptr = builder.gep(keys_ptr, [index])
        key_i = builder.load(key_i_ptr)
        cmp_result = builder.call(strcmp, [key_i, key])
        is_equal = builder.icmp_signed("==", cmp_result, ir.Constant(ir.IntType(32), 0))
        builder.cbranch(is_equal, found_block, next_block)

        builder.position_at_end(next_block)
        i_next = builder.add(index, ir.Constant(ir.IntType(64), 1))
        builder.store(i_next, i_ptr)
        builder.branch(loop_block)
        return i_ptr

    def _create_dict_key_lookup_func(
        self,
        func_name: str,
        prefix: str,
        field_specs: list[tuple[str, int, str]],
    ) -> tuple[
        ir.Function,
        ir.IRBuilder,
        dict[str, ir.Value],
        ir.Value,
        ir.Block,
        ir.Block,
    ]:
        dict_type = self._cg.get_dict_type()
        dict_ptr_type = dict_type.as_pointer()
        char_ptr = ir.IntType(8).as_pointer()
        func_type = ir.FunctionType(ir.IntType(64), [dict_ptr_type, char_ptr])
        func = ir.Function(self._cg.module, func_type, func_name)
        block = func.append_basic_block("entry")
        builder = ir.IRBuilder(block)
        dict_ptr = arg_at(func, 0)
        key = arg_at(func, 1)
        zero = ir.Constant(ir.IntType(32), 0)
        fields = self._load_dict_fields(builder, dict_ptr, zero, field_specs)
        found_block = func.append_basic_block("found")
        not_found_block = func.append_basic_block("not_found")
        i_ptr = self._emit_dict_key_search(
            func,
            builder,
            fields["size"],
            fields["keys"],
            key,
            found_block,
            not_found_block,
            prefix,
        )
        return func, builder, fields, i_ptr, found_block, not_found_block

    def _emit_dict_index_bounds(
        self,
        func: ir.Function,
        builder: ir.IRBuilder,
        dict_ptr: ir.Value,
        index: ir.Value,
        zero: ir.Constant,
    ) -> tuple[ir.Block, ir.Block]:
        size_ptr = self._dict_field_ptr(
            builder, dict_ptr, zero, self.DICT_FIELD_SIZE, "size_ptr"
        )
        size = builder.load(size_ptr, name="size")
        in_bounds = builder.icmp_signed("<", index, size, name="in_bounds")
        ok_block = func.append_basic_block("ok")
        err_block = func.append_basic_block("oob")
        builder.cbranch(in_bounds, ok_block, err_block)
        return ok_block, err_block

    def _create_dict_index_func(
        self, func_name: str, return_type: ir.Type
    ) -> tuple[ir.Function, ir.IRBuilder, ir.Value, ir.Value, ir.Constant]:
        dict_type = self._cg.get_dict_type()
        dict_ptr_type = dict_type.as_pointer()
        func_type = ir.FunctionType(return_type, [dict_ptr_type, ir.IntType(64)])
        func = ir.Function(self._cg.module, func_type, func_name)
        block = func.append_basic_block("entry")
        builder = ir.IRBuilder(block)
        dict_ptr = arg_at(func, 0)
        index = arg_at(func, 1)
        zero = ir.Constant(ir.IntType(32), 0)
        return func, builder, dict_ptr, index, zero

    def get_dict_create_func(self) -> ir.Function:
        """Get or create the dict_create function.

        dict_create(capacity) -> dict*
        Allocates a new dictionary with given capacity.
        """
        if self._cg.dict_create_func is not None:
            return self._cg.dict_create_func

        dict_type = self._cg.get_dict_type()
        dict_ptr_type = dict_type.as_pointer()
        char_ptr = ir.IntType(8).as_pointer()

        # Define function type: dict* dict_create(i64 capacity)
        func_type = ir.FunctionType(dict_ptr_type, [ir.IntType(64)])
        func = ir.Function(self._cg.module, func_type, "_ailang_dict_create")
        self._cg.dict_create_func = func

        # Build function body
        block = func.append_basic_block("entry")
        builder = ir.IRBuilder(block)

        capacity = arg_at(func, 0)

        # Allocate dict struct with null check (5 fields x 8 bytes = 40)
        dict_size = ir.Constant(ir.IntType(64), 40)
        dict_mem = self._cg._checked_malloc_with_builder(
            builder, func, dict_size, "dict_mem"
        )
        dict_ptr = builder.bitcast(dict_mem, dict_ptr_type, name="dict_ptr")

        # Allocate keys array: i8** of size capacity
        key_arr_size = builder.mul(capacity, ir.Constant(ir.IntType(64), 8))
        keys_mem = self._cg._checked_malloc_with_builder(
            builder, func, key_arr_size, "keys_mem"
        )
        keys_ptr = builder.bitcast(keys_mem, char_ptr.as_pointer(), name="keys_ptr")

        # Allocate values array: i64* of size capacity
        val_arr_size = builder.mul(capacity, ir.Constant(ir.IntType(64), 8))
        vals_mem = self._cg._checked_malloc_with_builder(
            builder, func, val_arr_size, "vals_mem"
        )
        vals_ptr = builder.bitcast(
            vals_mem, ir.IntType(64).as_pointer(), name="vals_ptr"
        )

        # Allocate types array: i8* of size capacity (1 byte per entry)
        types_mem = self._cg._checked_malloc_with_builder(
            builder, func, capacity, "types_mem"
        )
        types_ptr = builder.bitcast(types_mem, char_ptr, name="types_ptr")

        # Initialize struct fields
        zero = ir.Constant(ir.IntType(32), 0)
        # dict->capacity = capacity
        cap_ptr = builder.gep(dict_ptr, [zero, ir.Constant(ir.IntType(32), 0)])
        builder.store(capacity, cap_ptr)
        # dict->size = 0
        size_ptr = builder.gep(dict_ptr, [zero, ir.Constant(ir.IntType(32), 1)])
        builder.store(ir.Constant(ir.IntType(64), 0), size_ptr)
        # dict->keys = keys_ptr
        keys_field = builder.gep(dict_ptr, [zero, ir.Constant(ir.IntType(32), 2)])
        builder.store(keys_ptr, keys_field)
        # dict->values = vals_ptr
        vals_field = builder.gep(dict_ptr, [zero, ir.Constant(ir.IntType(32), 3)])
        builder.store(vals_ptr, vals_field)
        # dict->types = types_ptr
        types_field = builder.gep(dict_ptr, [zero, ir.Constant(ir.IntType(32), 4)])
        builder.store(types_ptr, types_field)

        builder.ret(dict_ptr)
        return func

    def get_dict_set_func(self) -> ir.Function:
        """Get or create dict_set function.

        dict_set(dict*, key, value, type_tag) -> void
        Sets key to value with type tag in the dictionary.
        Type tags: 0=int, 1=float, 2=string, 3=bool, 4=pointer
        """
        if self._cg.dict_set_func is not None:
            return self._cg.dict_set_func

        dict_type = self._cg.get_dict_type()
        dict_ptr_type = dict_type.as_pointer()
        char_ptr = ir.IntType(8).as_pointer()

        # dict_set(dict*, i8* key, i64 value, i8 type_tag) -> void
        func_type = ir.FunctionType(
            ir.VoidType(), [dict_ptr_type, char_ptr, ir.IntType(64), ir.IntType(8)]
        )
        func = ir.Function(self._cg.module, func_type, "_ailang_dict_set")
        self._cg.dict_set_func = func

        block = func.append_basic_block("entry")
        builder = ir.IRBuilder(block)

        dict_ptr = arg_at(func, 0)
        key = arg_at(func, 1)
        value = arg_at(func, 2)
        type_tag = arg_at(func, 3)

        zero = ir.Constant(ir.IntType(32), 0)
        # Load dict fields
        fields = self._load_dict_fields(
            builder,
            dict_ptr,
            zero,
            [
                ("size_ptr", self.DICT_FIELD_SIZE, "size"),
                ("keys_field", self.DICT_FIELD_KEYS, "keys"),
                ("vals_field", self.DICT_FIELD_VALUES, "vals"),
                ("types_field", self.DICT_FIELD_TYPES, "types"),
            ],
        )
        size_ptr = fields["size_ptr"]
        size = fields["size"]
        keys_field = fields["keys_field"]
        keys_ptr = fields["keys"]
        vals_field = fields["vals_field"]
        vals_ptr = fields["vals"]
        types_field = fields["types_field"]
        types_ptr = fields["types"]
        strcmp = self._cg.get_strcmp()

        # Linear search for existing key
        loop_cond_block = func.append_basic_block("loop_cond")
        loop_body_block = func.append_basic_block("loop_body")
        loop_inc_block = func.append_basic_block("loop_inc")
        found_block = func.append_basic_block("found")
        not_found_block = func.append_basic_block("not_found")
        end_block = func.append_basic_block("end")

        # Initialize loop counter
        i_ptr = builder.alloca(ir.IntType(64), name="i")
        builder.store(ir.Constant(ir.IntType(64), 0), i_ptr)
        builder.branch(loop_cond_block)

        # Loop condition: i < size
        builder.position_at_end(loop_cond_block)
        i = builder.load(i_ptr, name="i")
        cond = builder.icmp_signed("<", i, size)
        builder.cbranch(cond, loop_body_block, not_found_block)

        # Loop body: check if keys[i] == key
        builder.position_at_end(loop_body_block)
        i_body = builder.load(i_ptr)
        key_i_ptr = builder.gep(keys_ptr, [i_body])
        key_i = builder.load(key_i_ptr)
        cmp_result = builder.call(strcmp, [key_i, key])
        is_equal = builder.icmp_signed("==", cmp_result, ir.Constant(ir.IntType(32), 0))
        builder.cbranch(is_equal, found_block, loop_inc_block)

        # Loop increment: i++
        builder.position_at_end(loop_inc_block)
        i_inc = builder.load(i_ptr)
        i_next = builder.add(i_inc, ir.Constant(ir.IntType(64), 1))
        builder.store(i_next, i_ptr)
        builder.branch(loop_cond_block)

        # Found: update value and type at index i
        builder.position_at_end(found_block)
        i_found = builder.load(i_ptr)
        val_i_ptr = builder.gep(vals_ptr, [i_found])
        builder.store(value, val_i_ptr)
        type_i_ptr = builder.gep(types_ptr, [i_found])
        builder.store(type_tag, type_i_ptr)
        builder.branch(end_block)

        # Not found: append new key-value pair (resize if at capacity)
        builder.position_at_end(not_found_block)
        cap_ptr_check = self._dict_field_ptr(
            builder, dict_ptr, zero, self.DICT_FIELD_CAPACITY, "cap_ptr_check"
        )
        capacity = builder.load(cap_ptr_check, name="cap")
        at_capacity = builder.icmp_unsigned(">=", size, capacity)
        resize_block = func.append_basic_block("resize")
        insert_block = func.append_basic_block("insert")
        builder.cbranch(at_capacity, resize_block, insert_block)

        # Resize: double capacity and realloc keys, values, and types arrays
        builder.position_at_end(resize_block)
        two = ir.Constant(ir.IntType(64), 2)
        new_cap = builder.mul(capacity, two, name="new_cap")

        realloc_fn = self._cg.get_realloc()
        # Realloc keys array (i8** -> each element is i8*, 8 bytes)
        keys_raw = builder.bitcast(keys_ptr, ir.IntType(8).as_pointer())
        keys_bytes = builder.mul(new_cap, ir.Constant(ir.IntType(64), 8))
        new_keys_raw = builder.call(realloc_fn, [keys_raw, keys_bytes], name="rkeys")
        new_keys = builder.bitcast(
            new_keys_raw, ir.IntType(8).as_pointer().as_pointer()
        )
        builder.store(new_keys, keys_field)

        # Realloc values array (i64* -> each element is 8 bytes)
        vals_raw = builder.bitcast(vals_ptr, ir.IntType(8).as_pointer())
        vals_bytes = builder.mul(new_cap, ir.Constant(ir.IntType(64), 8))
        new_vals_raw = builder.call(realloc_fn, [vals_raw, vals_bytes], name="rvals")
        new_vals = builder.bitcast(new_vals_raw, ir.IntType(64).as_pointer())
        builder.store(new_vals, vals_field)

        # Realloc types array (i8* -> each element is 1 byte)
        types_raw = types_ptr  # Already i8*
        new_types_raw = builder.call(realloc_fn, [types_raw, new_cap], name="rtypes")
        builder.store(new_types_raw, types_field)

        # Update capacity
        builder.store(new_cap, cap_ptr_check)
        builder.branch(insert_block)

        builder.position_at_end(insert_block)
        # Reload pointers (may have changed after realloc)
        cur_keys = builder.load(keys_field, name="cur_keys")
        cur_vals = builder.load(vals_field, name="cur_vals")
        cur_types = builder.load(types_field, name="cur_types")
        # keys[size] = strdup(key) - make a safe copy to prevent dangling pointers
        strdup_fn = self._cg.get_strdup()
        key_copy = builder.call(strdup_fn, [key], name="key_copy")
        key_new_ptr = builder.gep(cur_keys, [size])
        builder.store(key_copy, key_new_ptr)
        # values[size] = value
        val_new_ptr = builder.gep(cur_vals, [size])
        builder.store(value, val_new_ptr)
        # types[size] = type_tag
        type_new_ptr = builder.gep(cur_types, [size])
        builder.store(type_tag, type_new_ptr)
        # size++
        new_size = builder.add(size, ir.Constant(ir.IntType(64), 1))
        builder.store(new_size, size_ptr)
        builder.branch(end_block)

        builder.position_at_end(end_block)
        builder.ret_void()

        return func

    def get_dict_get_type_func(self) -> ir.Function:
        """Get or create dict_get_type function.

        dict_get_type(dict*, key) -> i8
        Returns type tag for key, or 255 if not found.
        Type tags: 0=int, 1=float, 2=string, 3=bool, 4=pointer
        """
        if self._cg.dict_get_type_func is not None:
            return self._cg.dict_get_type_func

        dict_type = self._cg.get_dict_type()
        dict_ptr_type = dict_type.as_pointer()
        char_ptr = ir.IntType(8).as_pointer()

        func_type = ir.FunctionType(ir.IntType(8), [dict_ptr_type, char_ptr])
        func = ir.Function(self._cg.module, func_type, "_ailang_dict_get_type")
        self._cg.dict_get_type_func = func

        block = func.append_basic_block("entry")
        builder = ir.IRBuilder(block)

        dict_ptr = arg_at(func, 0)
        key = arg_at(func, 1)

        zero = ir.Constant(ir.IntType(32), 0)
        fields = self._load_dict_fields(
            builder,
            dict_ptr,
            zero,
            [
                ("size_ptr", self.DICT_FIELD_SIZE, "size"),
                ("keys_field", self.DICT_FIELD_KEYS, "keys"),
                ("types_field", self.DICT_FIELD_TYPES, "types"),
            ],
        )
        size = fields["size"]
        keys_ptr = fields["keys"]
        types_ptr = fields["types"]

        found = func.append_basic_block("found")
        not_found = func.append_basic_block("not_found")
        i_ptr = self._emit_dict_key_search(
            func, builder, size, keys_ptr, key, found, not_found, "type_search"
        )

        builder.position_at_end(found)
        i_found = builder.load(i_ptr)
        type_i_ptr = builder.gep(types_ptr, [i_found])
        type_val = builder.load(type_i_ptr, name="type_val")
        builder.ret(type_val)

        builder.position_at_end(not_found)
        builder.ret(ir.Constant(ir.IntType(8), 255))

        return self._cg.dict_get_type_func

    def get_dict_get_func(self) -> ir.Function:
        """Get or create dict_get function.

        dict_get(dict*, key) -> i64
        Returns value for key, or 0 if not found.
        """
        if self._cg.dict_get_func is not None:
            return self._cg.dict_get_func

        dict_type = self._cg.get_dict_type()
        dict_ptr_type = dict_type.as_pointer()
        char_ptr = ir.IntType(8).as_pointer()

        # dict_get(dict*, i8* key) -> i64
        func_type = ir.FunctionType(ir.IntType(64), [dict_ptr_type, char_ptr])
        func = ir.Function(self._cg.module, func_type, "_ailang_dict_get")
        self._cg.dict_get_func = func

        block = func.append_basic_block("entry")
        builder = ir.IRBuilder(block)

        dict_ptr = arg_at(func, 0)
        key = arg_at(func, 1)

        zero = ir.Constant(ir.IntType(32), 0)
        # Load dict fields
        fields = self._load_dict_fields(
            builder,
            dict_ptr,
            zero,
            [
                ("size_ptr", self.DICT_FIELD_SIZE, "size"),
                ("keys_field", self.DICT_FIELD_KEYS, "keys"),
                ("vals_field", self.DICT_FIELD_VALUES, "vals"),
            ],
        )
        size = fields["size"]
        keys_ptr = fields["keys"]
        vals_ptr = fields["vals"]

        found_block = func.append_basic_block("found")
        not_found_block = func.append_basic_block("not_found")
        i_ptr = self._emit_dict_key_search(
            func, builder, size, keys_ptr, key, found_block, not_found_block, "get"
        )

        builder.position_at_end(found_block)
        i_found = builder.load(i_ptr)
        val_ptr = builder.gep(vals_ptr, [i_found])
        result = builder.load(val_ptr, name="result")
        builder.ret(result)

        builder.position_at_end(not_found_block)
        builder.ret(ir.Constant(ir.IntType(64), 0))

        return func

    def get_dict_has_key_func(self) -> ir.Function:
        """Get or create dict_has_key function.

        dict_has_key(dict*, key) -> bool (i1)
        Returns 1 if key exists, 0 otherwise.
        Fixes bug M4: dict_get returns 0 for missing keys (indistinguishable from stored 0).
        """
        func_name = "_ailang_dict_has_key"
        if func_name in self._cg.module.globals:
            return self._cg.module.globals[func_name]

        func, builder, _, _, found_block, not_found_block = (
            self._create_dict_key_lookup_func(
                func_name,
                "has",
                [
                    ("size_ptr", self.DICT_FIELD_SIZE, "size"),
                    ("keys_field", self.DICT_FIELD_KEYS, "keys"),
                ],
            )
        )

        builder.position_at_end(found_block)
        builder.ret(ir.Constant(ir.IntType(64), 1))  # True - key exists

        builder.position_at_end(not_found_block)
        builder.ret(ir.Constant(ir.IntType(64), 0))  # False - key doesn't exist

        return func

    def get_dict_size_func(self) -> ir.Function:
        """Get or create dict_size function: dict_size(dict*) -> i64."""
        func_name = "_ailang_dict_size"
        if func_name in self._cg.module.globals:
            return self._cg.module.globals[func_name]

        dict_type = self._cg.get_dict_type()
        dict_ptr_type = dict_type.as_pointer()

        func_type = ir.FunctionType(ir.IntType(64), [dict_ptr_type])
        func = ir.Function(self._cg.module, func_type, func_name)

        block = func.append_basic_block("entry")
        builder = ir.IRBuilder(block)

        dict_ptr = arg_at(func, 0)
        zero = ir.Constant(ir.IntType(32), 0)
        size_ptr = self._dict_field_ptr(
            builder, dict_ptr, zero, self.DICT_FIELD_SIZE, "size_ptr"
        )
        size = builder.load(size_ptr, name="size")
        builder.ret(size)

        return func

    def get_dict_key_at_func(self) -> ir.Function:
        """Get or create dict_key_at function: dict_key_at(dict*, i64) -> i8*.

        Returns the key string at position i.  The LLVM backend stores
        keys contiguously at indices 0..size-1, so this is a direct index.
        """
        func_name = "_ailang_dict_key_at"
        if func_name in self._cg.module.globals:
            return self._cg.module.globals[func_name]

        char_ptr = ir.IntType(8).as_pointer()
        func, builder, dict_ptr, index, zero = self._create_dict_index_func(
            func_name, char_ptr
        )
        ok_block, err_block = self._emit_dict_index_bounds(
            func, builder, dict_ptr, index, zero
        )

        builder.position_at_end(err_block)
        # Return empty string on out-of-bounds
        empty_str = self._cg.create_string_constant("")
        builder.ret(empty_str)

        builder.position_at_end(ok_block)
        keys_field = self._dict_field_ptr(
            builder, dict_ptr, zero, self.DICT_FIELD_KEYS, "keys_field"
        )
        keys_ptr = builder.load(keys_field, name="keys")
        key_i_ptr = builder.gep(keys_ptr, [index], name="key_i_ptr")
        key_i = builder.load(key_i_ptr, name="key_i")
        builder.ret(key_i)

        return func

    def get_dict_value_at_func(self) -> ir.Function:
        """Get or create dict_value_at function: dict_value_at(dict*, i64) -> i64."""
        func_name = "_ailang_dict_value_at"
        if func_name in self._cg.module.globals:
            return self._cg.module.globals[func_name]

        func, builder, dict_ptr, index, zero = self._create_dict_index_func(
            func_name, ir.IntType(64)
        )
        ok_block, err_block = self._emit_dict_index_bounds(
            func, builder, dict_ptr, index, zero
        )

        builder.position_at_end(err_block)
        builder.ret(ir.Constant(ir.IntType(64), 0))

        builder.position_at_end(ok_block)
        vals_field = self._dict_field_ptr(
            builder, dict_ptr, zero, self.DICT_FIELD_VALUES, "vals_field"
        )
        vals_ptr = builder.load(vals_field, name="vals")
        val_i_ptr = builder.gep(vals_ptr, [index], name="val_i_ptr")
        val_i = builder.load(val_i_ptr, name="val_i")
        builder.ret(val_i)

        return func

    def get_dict_remove_func(self) -> ir.Function:
        """Get or create dict_remove function: dict_remove(dict*, i8*) -> i64.

        Returns 1 if key was found and removed, 0 otherwise.
        Shifts remaining entries left to fill the gap.
        """
        func_name = "_ailang_dict_remove"
        if func_name in self._cg.module.globals:
            return self._cg.module.globals[func_name]

        func, builder, fields, i_ptr, found_block, not_found_block = (
            self._create_dict_key_lookup_func(
                func_name,
                "remove",
                [
                    ("size_ptr", self.DICT_FIELD_SIZE, "size"),
                    ("keys_field", self.DICT_FIELD_KEYS, "keys"),
                    ("vals_field", self.DICT_FIELD_VALUES, "vals"),
                    ("types_field", self.DICT_FIELD_TYPES, "types"),
                ],
            )
        )
        size_ptr = fields["size_ptr"]
        size = fields["size"]
        keys_ptr = fields["keys"]
        vals_ptr = fields["vals"]
        types_ptr = fields["types"]

        # Found: shift remaining entries left
        builder.position_at_end(found_block)
        i = builder.load(i_ptr, name="remove_index")
        new_size = builder.sub(size, ir.Constant(ir.IntType(64), 1), name="new_size")
        builder.store(new_size, size_ptr)

        # Shift loop: copy [i+1..size-1] to [i..size-2]
        shift_cond = func.append_basic_block("shift_cond")
        shift_body = func.append_basic_block("shift_body")
        shift_done = func.append_basic_block("shift_done")

        j_ptr = builder.alloca(ir.IntType(64), name="j")
        builder.store(i, j_ptr)
        builder.branch(shift_cond)

        builder.position_at_end(shift_cond)
        j = builder.load(j_ptr)
        shift_ok = builder.icmp_signed("<", j, new_size)
        builder.cbranch(shift_ok, shift_body, shift_done)

        builder.position_at_end(shift_body)
        j_plus_1 = builder.add(j, ir.Constant(ir.IntType(64), 1))
        # Copy key[j+1] -> key[j]
        src_key = builder.load(builder.gep(keys_ptr, [j_plus_1]))
        builder.store(src_key, builder.gep(keys_ptr, [j]))
        # Copy value[j+1] -> value[j]
        src_val = builder.load(builder.gep(vals_ptr, [j_plus_1]))
        builder.store(src_val, builder.gep(vals_ptr, [j]))
        # Copy type[j+1] -> type[j]
        src_type = builder.load(builder.gep(types_ptr, [j_plus_1]))
        builder.store(src_type, builder.gep(types_ptr, [j]))
        builder.store(j_plus_1, j_ptr)
        builder.branch(shift_cond)

        builder.position_at_end(shift_done)
        builder.ret(ir.Constant(ir.IntType(64), 1))

        builder.position_at_end(not_found_block)
        builder.ret(ir.Constant(ir.IntType(64), 0))

        return func
