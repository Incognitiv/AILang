"""LLVM-side adapters for AILang ``unbounded`` integers.

The language semantics live in ``stdlib/core/bigint.ail``.  This service is
*only* an LLVM representation bridge: codegen keeps a distinct internal
pointer type so arbitrary pointers/strings cannot be mistaken for BigInts,
then bitcasts that marker to the ordinary AILang ``pointer`` ABI of the
stdlib functions.  No arithmetic/runtime implementation is declared as a
foreign symbol here.
"""

from __future__ import annotations

from typing import Any

from llvmlite import ir


class BigIntRuntime:
    """BigInt representation adapters for the LLVM backend."""

    def __init__(self, codegen: Any) -> None:
        self._cg = codegen

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cg, name)

    def _get_bigint_type(self) -> ir.PointerType:
        """Distinct internal marker type; the stdlib ABI itself uses pointer."""
        if self._cg._bigint_type is None:
            self._cg._bigint_type = ir.LiteralStructType(
                [
                    ir.IntType(64),
                    ir.IntType(64),
                    ir.IntType(64),
                    ir.IntType(64).as_pointer(),
                ]
            )
        return self._cg._bigint_type.as_pointer()

    @staticmethod
    def _cast_value(builder: ir.IRBuilder, value: ir.Value, target: ir.Type, name: str) -> ir.Value:
        if value.type == target:
            return value
        if isinstance(value.type, ir.PointerType) and isinstance(target, ir.PointerType):
            return builder.bitcast(value, target, name=name)
        if isinstance(value.type, ir.IntType) and isinstance(target, ir.IntType):
            if value.type.width < target.width:
                # Adapter integer values are ABI/control quantities (sign, bits,
                # count), all represented as language ``int``/u64.  Widening is
                # signed unless the exact source ABI is already u64-width.
                return builder.sext(value, target, name=name)
            if value.type.width > target.width:
                return builder.trunc(value, target, name=name)
        raise TypeError(f"cannot adapt BigInt ABI value {value.type} -> {target}")

    def _language_function(self, symbol: str) -> ir.Function:
        """Return the function compiled from bigint.ail; never synthesize externs."""
        fn = self._cg.functions.get(symbol)
        if fn is None:
            # Imported functions are declared in pass 1 before user bodies are
            # emitted.  If this fails, automatic runtime-module injection is
            # broken; silently declaring a foreign runtime would reintroduce the
            # architecture we explicitly removed.
            raise RuntimeError(
                f"pure-AIL BigInt function {symbol!r} is unavailable; "
                "stdlib/core/bigint.ail must be imported before unbounded codegen"
            )
        return fn

    def _adapter(self, cache_name: str, symbol: str, ret: ir.Type, args: list[ir.Type]) -> ir.Function:
        cached = getattr(self._cg, cache_name, None)
        if cached is not None:
            return cached

        target = self._language_function(symbol)
        wrapper_name = f"__ailang_unbounded_adapter_{symbol.removeprefix('ailang_bigint_')}"
        existing = self._cg.module.globals.get(wrapper_name)
        if isinstance(existing, ir.Function):
            setattr(self._cg, cache_name, existing)
            return existing

        wrapper = ir.Function(self._cg.module, ir.FunctionType(ret, args), wrapper_name)
        wrapper.linkage = "internal"
        block = wrapper.append_basic_block("entry")
        builder = ir.IRBuilder(block)
        target_args = list(target.function_type.args)
        if len(target_args) != len(wrapper.args):
            raise RuntimeError(
                f"pure-AIL BigInt ABI mismatch for {symbol}: expected {len(target_args)} args, "
                f"adapter has {len(wrapper.args)}"
            )
        call_args = [
            self._cast_value(builder, arg, target_ty, f"arg_{idx}")
            for idx, (arg, target_ty) in enumerate(zip(wrapper.args, target_args))
        ]
        result = builder.call(target, call_args, name="call" if not isinstance(target.function_type.return_type, ir.VoidType) else "")
        if isinstance(ret, ir.VoidType):
            builder.ret_void()
        else:
            result = self._cast_value(builder, result, ret, "result")
            builder.ret(result)
        setattr(self._cg, cache_name, wrapper)
        return wrapper

    def _get_bigint_new(self) -> ir.Function:
        p = self._get_bigint_type()
        return self._adapter("_bigint_new_func", "ailang_bigint_new", p, [ir.IntType(64)])

    def _get_bigint_from_int(self) -> ir.Function:
        p = self._get_bigint_type()
        return self._adapter("_bigint_from_int_func", "ailang_bigint_from_int", p, [ir.IntType(64)])

    def _get_bigint_from_u64(self) -> ir.Function:
        p = self._get_bigint_type()
        return self._adapter("_bigint_from_u64_func", "ailang_bigint_from_u64", p, [ir.IntType(64)])

    def _get_bigint_from_decimal(self) -> ir.Function:
        p = self._get_bigint_type()
        return self._adapter("_bigint_from_decimal_func", "ailang_bigint_from_decimal", p, [ir.IntType(8).as_pointer()])

    def _get_bigint_from_words(self) -> ir.Function:
        p = self._get_bigint_type()
        return self._adapter("_bigint_from_words_func", "ailang_bigint_from_words", p, [ir.IntType(64), ir.IntType(64).as_pointer(), ir.IntType(64)])

    def _binary(self, cache_name: str, symbol: str) -> ir.Function:
        p = self._get_bigint_type()
        return self._adapter(cache_name, symbol, p, [p, p])

    def _get_bigint_add(self) -> ir.Function:
        return self._binary("_bigint_add_func", "ailang_bigint_add")

    def _get_bigint_sub(self) -> ir.Function:
        return self._binary("_bigint_sub_func", "ailang_bigint_sub")

    def _get_bigint_mul(self) -> ir.Function:
        return self._binary("_bigint_mul_func", "ailang_bigint_mul")

    def _get_bigint_div(self) -> ir.Function:
        return self._binary("_bigint_div_func", "ailang_bigint_div")

    def _get_bigint_mod(self) -> ir.Function:
        return self._binary("_bigint_mod_func", "ailang_bigint_mod")

    def _get_bigint_and(self) -> ir.Function:
        return self._binary("_bigint_and_func", "ailang_bigint_and")

    def _get_bigint_or(self) -> ir.Function:
        return self._binary("_bigint_or_func", "ailang_bigint_or")

    def _get_bigint_xor(self) -> ir.Function:
        return self._binary("_bigint_xor_func", "ailang_bigint_xor")

    def _get_bigint_pow(self) -> ir.Function:
        p = self._get_bigint_type()
        return self._adapter("_bigint_pow_func", "ailang_bigint_pow", p, [p, p])

    def _get_bigint_shl(self) -> ir.Function:
        p = self._get_bigint_type()
        return self._adapter("_bigint_shl_func", "ailang_bigint_shl", p, [p, ir.IntType(64)])

    def _get_bigint_shr(self) -> ir.Function:
        p = self._get_bigint_type()
        return self._adapter("_bigint_shr_func", "ailang_bigint_shr", p, [p, ir.IntType(64)])

    def _get_bigint_not(self) -> ir.Function:
        p = self._get_bigint_type()
        return self._adapter("_bigint_not_func", "ailang_bigint_not", p, [p])

    def _get_bigint_clone(self) -> ir.Function:
        p = self._get_bigint_type()
        return self._adapter("_bigint_clone_func", "ailang_bigint_clone", p, [p])

    def _get_bigint_cmp(self) -> ir.Function:
        p = self._get_bigint_type()
        return self._adapter("_bigint_cmp_func", "ailang_bigint_cmp", ir.IntType(64), [p, p])

    def _get_bigint_print(self) -> ir.Function:
        p = self._get_bigint_type()
        return self._adapter("_bigint_print_func", "ailang_bigint_print", ir.VoidType(), [p])

    def _get_bigint_digits(self) -> ir.Function:
        p = self._get_bigint_type()
        return self._adapter("_bigint_digits_func", "ailang_bigint_digits", ir.IntType(64), [p])

    def _get_bigint_free(self) -> ir.Function:
        p = self._get_bigint_type()
        return self._adapter("_bigint_free_func", "ailang_bigint_free", ir.VoidType(), [p])

    def _get_bigint_fits_signed(self) -> ir.Function:
        p = self._get_bigint_type()
        return self._adapter("_bigint_fits_signed_func", "ailang_bigint_fits_signed", ir.IntType(64), [p, ir.IntType(64)])

    def _get_bigint_fits_unsigned(self) -> ir.Function:
        p = self._get_bigint_type()
        return self._adapter("_bigint_fits_unsigned_func", "ailang_bigint_fits_unsigned", ir.IntType(64), [p, ir.IntType(64)])

    def _get_bigint_to_i64(self) -> ir.Function:
        p = self._get_bigint_type()
        return self._adapter("_bigint_to_i64_func", "ailang_bigint_to_i64", ir.IntType(64), [p])

    def _get_bigint_to_u64(self) -> ir.Function:
        p = self._get_bigint_type()
        return self._adapter("_bigint_to_u64_func", "ailang_bigint_to_u64", ir.IntType(64), [p])

    def _get_bigint_word_at(self) -> ir.Function:
        p = self._get_bigint_type()
        return self._adapter("_bigint_word_at_func", "ailang_bigint_word_at", ir.IntType(64), [p, ir.IntType(64)])

    def _get_bigint_sign(self) -> ir.Function:
        p = self._get_bigint_type()
        return self._adapter("_bigint_sign_func", "ailang_bigint_sign", ir.IntType(64), [p])

    def _get_bigint_to_decimal(self) -> ir.Function:
        p = self._get_bigint_type()
        return self._adapter("_bigint_to_decimal_func", "ailang_bigint_to_decimal", ir.IntType(8).as_pointer(), [p])

    def _get_bigint_live_count(self) -> ir.Function:
        return self._adapter("_bigint_live_count_func", "ailang_bigint_live_count", ir.IntType(64), [])

    def is_bigint_type(self, llvm_type: ir.Type) -> bool:
        if self._cg._bigint_type is None:
            return False
        return llvm_type == self._cg._bigint_type.as_pointer()
