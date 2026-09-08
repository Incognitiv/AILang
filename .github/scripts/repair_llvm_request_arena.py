from pathlib import Path


def replace_once(path: str, old: str, new: str, label: str) -> None:
    p = Path(path)
    s = p.read_text(encoding="utf-8")
    if old not in s:
        raise SystemExit(f"{label} anchor missing")
    p.write_text(s.replace(old, new, 1), encoding="utf-8")


replace_once(
    "source/codegen/codegen_function_mixin.py",
    '''        if getattr(self, "_module_uses_string_arena", True):
            i8_ptr = ir.IntType(8).as_pointer()
            self._request_arena_slot = self.builder.alloca(
                i8_ptr, name="request_arena_slot"
            )
            self.builder.store(ir.Constant(i8_ptr, None), self._request_arena_slot)
''',
    '''        if getattr(self, "_module_uses_string_arena", True):
            i8_ptr = ir.IntType(8).as_pointer()
            # arena_use() is request/thread context, not function-local state.
            # Keep one TLS slot in the module so callees observe the arena
            # selected by their caller, matching the C backend contract.
            slot = self.module.globals.get("__ailang_request_arena")
            if slot is None:
                slot = ir.GlobalVariable(
                    self.module, i8_ptr, "__ailang_request_arena"
                )
                slot.linkage = "internal"
                slot.initializer = ir.Constant(i8_ptr, None)
                slot.thread_local = "general"
            self._request_arena_slot = slot
''',
    "request arena alloca",
)

replace_once(
    "source/codegen/codegen_support_mixin.py",
    '''        # Always use malloc for concat (arena would accumulate loop intermediates)
        new_str = self.current_builder.call(
            self.get_malloc(), [total_len], name="concat_str"
        )
''',
    '''        # Route through the same string allocator as str/substr/chr. With an
        # active request arena the caller can reclaim the whole request with
        # arena_reset(); without one this falls back to the existing heap/main
        # arena policy.
        new_str = self.string_alloc(total_len, "concat_str")
''',
    "concat malloc",
)

replace_once(
    "source/codegen/codegen_support_mixin.py",
    '''        # Free intermediate temporaries from prior concatenations
        left_name = getattr(left_str, "name", "")
        right_name = getattr(right_str, "name", "")
        if left_name in self.temp_strings:
            self.current_builder.call(self._get_free(), [left_str])
            self.temp_strings.discard(left_name)
        if right_name in self.temp_strings:
            self.current_builder.call(self._get_free(), [right_str])
            self.temp_strings.discard(right_name)
''',
    '''        # Free heap intermediates, but never individually free pointers owned
        # by the main/request arena. Request-arena temps are reclaimed by
        # arena_reset() and main-arena temps by arena_destroy().
        def release_temp_if_heap(value: Any) -> None:
            name = getattr(value, "name", "")
            if name not in self.temp_strings:
                return
            self.temp_strings.discard(name)
            if self._string_arena is not None:
                return
            request_slot = getattr(self, "_request_arena_slot", None)
            if request_slot is None:
                self.current_builder.call(self._get_free(), [value])
                return
            i8_ptr = ir.IntType(8).as_pointer()
            active = self.current_builder.load(
                request_slot, name="concat_active_request_arena"
            )
            is_heap = self.current_builder.icmp_unsigned(
                "==", active, ir.Constant(i8_ptr, None), name="concat_temp_is_heap"
            )
            free_block = self.current_function.append_basic_block("concat_temp_free")
            done_block = self.current_function.append_basic_block("concat_temp_done")
            self.current_builder.cbranch(is_heap, free_block, done_block)
            self.current_builder.position_at_end(free_block)
            self.current_builder.call(self._get_free(), [value])
            self.current_builder.branch(done_block)
            self.current_builder.position_at_end(done_block)

        release_temp_if_heap(left_str)
        release_temp_if_heap(right_str)
''',
    "concat temporary cleanup",
)

replace_once(
    "source/codegen/builtin_string.py",
    '''        result = self.current_builder.call(
            self.get_malloc(), [alloc_size], name="concat_buf"
        )
''',
    '''        result = self.string_alloc(alloc_size, "concat_buf")
''',
    "builtin concat malloc",
)

p = Path("tests/test_arena_routing_llvm.py")
s = p.read_text(encoding="utf-8")
if "test_request_arena_slot_is_module_tls_not_function_alloca" not in s:
    s += r'''


def test_request_arena_slot_is_module_tls_not_function_alloca() -> None:
    src = """
def make_value(): string
    return "value=" + str(123456)
end

def main(): int
    a = arena_create(4096)
    arena_use(a)
    s = make_value()
    print(s)
    return arena_used(a)
end
"""
    ir_text = _to_ir(src)
    assert '__ailang_request_arena' in ir_text
    assert 'thread_local' in ir_text
    assert 'request_arena_slot = alloca' not in ir_text
    assert ir_text.count('__ailang_request_arena') >= 3
    assert 'str_alloc_req_arena' in ir_text


def test_string_concat_routes_through_request_arena_allocator() -> None:
    src = """
def make_value(a: string, b: string): string
    return a + b
end

def main(): int
    arena = arena_create(4096)
    arena_use(arena)
    s = make_value("left", "right")
    n = arena_used(arena)
    arena_reset(arena)
    arena_destroy(arena)
    return n
end
"""
    ir_text = _to_ir(src)
    assert 'str_alloc_req_arena' in ir_text
    assert '__ailang_request_arena' in ir_text
'''
p.write_text(s, encoding="utf-8")
