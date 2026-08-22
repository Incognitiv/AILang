from __future__ import annotations

import sys
from pathlib import Path

import pytest
from llvmlite import ir

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPO_ROOT / "source"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from codegen.codegen import CodeGen  # noqa: E402
from codegen.codegen_errors import CodeGenError  # noqa: E402


def test_bigint_runtime_does_not_synthesize_missing_native_symbol() -> None:
    cg = CodeGen()

    with pytest.raises(CodeGenError, match="will not synthesize a hidden native BigInt runtime"):
        cg._get_bigint_add()

    assert "ailang_bigint_add" not in cg.module.globals
    assert "ailang_bigint_add" not in cg.functions


def test_bigint_runtime_accepts_predeclared_ailang_implementation() -> None:
    cg = CodeGen()
    bigint_ptr = cg._get_bigint_type()
    fn_ty = ir.FunctionType(bigint_ptr, [bigint_ptr, bigint_ptr])
    fn = ir.Function(cg.module, fn_ty, name="ailang_bigint_add")
    cg.functions["ailang_bigint_add"] = fn

    resolved = cg._get_bigint_add()

    assert resolved is fn
    assert cg._bigint_add_func is fn


def test_bigint_runtime_rejects_wrong_ailang_abi() -> None:
    cg = CodeGen()
    bigint_ptr = cg._get_bigint_type()
    wrong_ty = ir.FunctionType(bigint_ptr, [bigint_ptr])
    fn = ir.Function(cg.module, wrong_ty, name="ailang_bigint_add")
    cg.functions["ailang_bigint_add"] = fn

    with pytest.raises(CodeGenError, match="incompatible ABI"):
        cg._get_bigint_add()
