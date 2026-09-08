from __future__ import annotations

from types import SimpleNamespace

import pytest
from llvmlite import ir

from codegen.codegen import CodeGenError
from codegen.type_lowering import TypeLowering
from transpiler.core import CTranspiler


def _dummy_codegen():
    return SimpleNamespace(
        type_aliases={},
        class_types={},
        data_enum_types={},
        opaque_record_names=set(),
        record_types={},
        classes={},
        get_dict_type=lambda: ir.LiteralStructType([]),
        _get_bigint_type=lambda: ir.IntType(8).as_pointer(),
    )


@pytest.mark.parametrize("name,width", [
    ("i128", 128),
    ("i256", 256),
    ("i512", 512),
    ("i1024", 1024),
    ("i2048", 2048),
    ("i4096", 4096),
    ("i8192", 8192),
])
def test_llvm_fixed_wide_types_are_real_llvm_integers(name, width):
    ty = TypeLowering(_dummy_codegen()).get_llvm_type(name)
    assert isinstance(ty, ir.IntType)
    assert ty.width == width


@pytest.mark.parametrize("name,ctype", [
    ("i128", "__int128"),
    ("i256", "ailang_i256"),
    ("i512", "ailang_i512"),
    ("i1024", "ailang_i1024"),
    ("i2048", "ailang_i2048"),
    ("i4096", "ailang_i4096"),
    ("i8192", "ailang_i8192"),
])
def test_c_fixed_wide_types_are_not_i64(name, ctype):
    assert CTranspiler()._ailang_type_to_c(name) == ctype


def test_unknown_llvm_type_fails_closed():
    with pytest.raises(CodeGenError, match="no executable representation"):
        TypeLowering(_dummy_codegen()).get_llvm_type("definitely_not_a_type")


def test_unknown_c_type_fails_closed():
    with pytest.raises(ValueError, match="no executable representation"):
        CTranspiler()._ailang_type_to_c("definitely_not_a_type")


def test_c_i128_formatting_and_i64_boundary_use_complete_fixed_metadata():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    fixed_casts = (root / "source/transpiler/fixed_int_cast_codegen.py").read_text(
        encoding="utf-8"
    )
    runtime_fixed = (root / "source/transpiler/runtime_emit_fixed_int_casts.py").read_text(encoding="utf-8")
    runtime_string = (root / "source/transpiler/runtime_emit_string.py").read_text(encoding="utf-8")
    assert "info_for_c_fixed(transpiler._infer_type(value_node))" in fixed_casts
    assert "ailang_narrow_i64_i128" in runtime_fixed
    assert "ailang_str_i128" in runtime_string
