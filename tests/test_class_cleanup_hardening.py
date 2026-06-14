from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPO_ROOT / "source"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from parser import ast as A  # noqa: E402
from parser.parser import Parser  # noqa: E402

from diagnostics.static_analysis import analyze_ast  # noqa: E402
from lexer.scan import tokenize  # noqa: E402


def _parse_program(src: str) -> list[A.ASTNode]:
    tokens = tokenize(src)
    parser = Parser(tokens)
    return parser.parse_program()


def test_class_string_field_without_destructor_is_auto_cleaned() -> None:
    src = """
class Session then
    string token
end
"""
    program = _parse_program(src)
    warnings = analyze_ast(program)
    assert not any(w.category == "class-cleanup" for w in warnings)


def test_class_handle_field_without_destructor_warns() -> None:
    src = """
class Session then
    handle token
end
"""
    program = _parse_program(src)
    warnings = analyze_ast(program)
    assert any(
        w.category == "class-cleanup"
        and "Session" in w.message
        and "token:handle" in w.message
        for w in warnings
    )


def test_class_dynamic_array_without_destructor_warns() -> None:
    src = """
class Bucket then
    [int] values
end
"""
    program = _parse_program(src)
    warnings = analyze_ast(program)
    assert any(
        w.category == "class-cleanup"
        and "Bucket" in w.message
        and "values:[" in w.message
        for w in warnings
    )


def test_class_string_field_with_empty_destructor_is_auto_cleaned() -> None:
    src = """
class Session then
    string token
    ~Session()
end
"""
    program = _parse_program(src)
    warnings = analyze_ast(program)
    assert not any(w.category == "class-cleanup" for w in warnings)


def test_class_handle_field_with_empty_destructor_warns() -> None:
    src = """
class Session then
    handle token
    ~Session()
end
"""
    program = _parse_program(src)
    warnings = analyze_ast(program)
    assert any(
        w.category == "class-cleanup"
        and "destructor does not reference" in w.message
        and "token:handle" in w.message
        for w in warnings
    )


def test_class_resource_field_with_destructor_reference_does_not_warn() -> None:
    src = """
class Session then
    string token
    public def ~Session():
        dealloc(this.token)
    end
end
"""
    program = _parse_program(src)
    warnings = analyze_ast(program)
    assert not any(w.category == "class-cleanup" for w in warnings)


def test_class_scalar_only_without_destructor_does_not_warn() -> None:
    src = """
class Point then
    int x
    int y
end
"""
    program = _parse_program(src)
    warnings = analyze_ast(program)
    assert not any(w.category == "class-cleanup" for w in warnings)
