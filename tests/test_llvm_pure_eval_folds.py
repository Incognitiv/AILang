import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "source"))

from parser.parser import Parser

from codegen.codegen import CodeGen
from lexer.scan import tokenize


def _assert_constant_print_call(main_ir: str) -> None:
    # Literal printing may coexist with a printf-based checked-cast trap path.
    # What matters here is that the literal itself uses the constant puts fastpath.
    assert 'call i32 @"puts"' in main_ir


def _llvm_ir(source: str) -> str:
    ast = Parser(tokenize(source)).parse_program()
    return CodeGen().generate(ast, "test_llvm_pure_eval_folds.ail")


@pytest.mark.xfail(strict=False, reason="typed pure-call folding is deferred until the optimization phase")
def test_fixed_array_reduction_call_with_literal_count_folds_to_constant() -> None:
    ir = _llvm_ir(
        """
type Arr8 = [int; 8]

def fixed_array_sum_bench(iterations):
    Arr8 arr = [3, 1, 4, 1, 5, 9, 2, 6]
    acc = 0
    i = 0
    while i < iterations then
        j := 0..7 = 0
        while true then
            acc = acc + arr[j]
            if j == 7 then
                break
            end
            j = j + 1
        end
        i = i + 1
    end
    return acc
end

def main(): int
    iterations = 250000
    result = fixed_array_sum_bench(iterations)
    print(result)
    return 0
end
"""
    )

    main_ir = ir.split('define i32 @"main"', 1)[1]
    assert "7750000" in main_ir
    assert "call_fixed_array_sum_bench" not in main_ir
    _assert_constant_print_call(main_ir)


@pytest.mark.xfail(strict=False, reason="typed pure-call folding is deferred until the optimization phase")
def test_slice_reduction_call_with_literal_count_folds_to_constant() -> None:
    ir = _llvm_ir(
        """
type Arr8 = [int; 8]
type IterCount = 0..250000

def slice_sum_bench(iterations: IterCount):
    Arr8 arr = [3, 1, 4, 1, 5, 9, 2, 6]
    slice[int] view = arr
    acc := 0..7750000 = 0
    i = 0
    while i < iterations then
        j := 0..8 = 0
        while j < 8 then
            acc = acc + view[j]
            j = j + 1
        end
        i = i + 1
    end
    return acc
end

def main(): int
    iterations = 250000
    result = slice_sum_bench(iterations)
    print(result)
    return 0
end
"""
    )

    main_ir = ir.split('define i32 @"main"', 1)[1]
    assert "7750000" in main_ir
    assert "call_slice_sum_bench" not in main_ir
    _assert_constant_print_call(main_ir)


@pytest.mark.xfail(strict=False, reason="typed pure-call folding is deferred until the optimization phase")
def test_recursive_literal_call_folds_with_memoized_pure_eval() -> None:
    ir = _llvm_ir(
        """
def recursive_fib(n: i32): i64
    if n <= 1 then
        return 1
    end
    return recursive_fib(n - 1) + recursive_fib(n - 2)
end

def recursive_bench(iterations: i32): i64
    return recursive_fib(iterations)
end

def main(): int
    depth = 32
    result = recursive_bench(depth)
    print(result)
    return 0
end
"""
    )

    main_ir = ir.split('define i32 @"main"', 1)[1]
    assert "3524578" in main_ir
    assert "call_recursive_bench" not in main_ir
    assert "call_recursive_fib" not in main_ir
    _assert_constant_print_call(main_ir)


def test_main_argv_globals_are_elided_when_unused() -> None:
    ir = _llvm_ir(
        """
def main(): int
    print(123)
    return 0
end
"""
    )

    assert "__ailang_argc" not in ir
    assert "__ailang_argv" not in ir
    main_ir = ir.split('define i32 @"main"', 1)[1]
    _assert_constant_print_call(main_ir)


def test_main_argv_globals_remain_when_argc_is_used() -> None:
    ir = _llvm_ir(
        """
def main(): int
    print(argc())
    return 0
end
"""
    )

    assert "__ailang_argc" in ir
    assert "__ailang_argv" in ir
    main_ir = ir.split('define i32 @"main"', 1)[1]
    assert 'call i32 (i8*, ...) @"printf"' in main_ir
