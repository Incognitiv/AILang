from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPO_ROOT / "source"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from lexer.scan import tokenize  # noqa: E402
from parser import ast as A  # noqa: E402
from parser.parser import Parser  # noqa: E402
from transpiler.drop_plan import (  # noqa: E402
    DropKind,
    constructor_field_drop_plan,
    drop_kind_for_type,
)


def _parse(src: str) -> list[A.ASTNode]:
    return Parser(tokenize(src)).parse_program()


def _class_maps(nodes: list[A.ASTNode]):
    classes = [node for node in nodes if isinstance(node, A.ClassDef)]
    assert len(classes) == 1
    cls = classes[0]
    record_fields = {
        cls.name: [(str(field[1]), field[2]) for field in cls.fields],
    }
    class_methods = {cls.name: cls.methods}
    return cls, class_methods, record_fields


def _main_vardecl(nodes: list[A.ASTNode]) -> A.VarDecl:
    for node in nodes:
        if isinstance(node, A.Function) and node.name == "main":
            for stmt in node.body:
                if isinstance(stmt, A.VarDecl):
                    return stmt
    raise AssertionError("missing main vardecl")


def test_drop_kind_classifies_compiler_owned_field_types() -> None:
    assert drop_kind_for_type("string") == DropKind.OWNED_STRING
    assert drop_kind_for_type("array") == DropKind.DYNAMIC_ARRAY
    assert drop_kind_for_type("str_array") == DropKind.STR_ARRAY
    assert drop_kind_for_type("dict") == DropKind.DICT
    assert drop_kind_for_type("Handle") is None
    assert drop_kind_for_type("Child", {"Child": object()}) == DropKind.CLASS_VALUE


def test_constructor_drop_plan_tracks_init_transfers_and_field_arrays() -> None:
    nodes = _parse(
        """
class Packet then
    string label
    array values
    public def init(label_arg: string, seed: int):
        this.label = label_arg
        this.values = array_new(4)
        this.values = array_push(this.values, seed)
    end
end

def main(): int
    Packet p = new Packet("pkt_" + str(7), 3)
    return 0
end
"""
    )
    cls, class_methods, record_fields = _class_maps(nodes)
    vardecl = _main_vardecl(nodes)
    assert isinstance(vardecl.init_value, A.NewExpr)

    plan = constructor_field_drop_plan(
        cls.name, vardecl.init_value, class_methods, record_fields
    )

    assert [(field.name, field.kind) for field in plan.fields] == [
        ("label", DropKind.OWNED_STRING),
        ("values", DropKind.DYNAMIC_ARRAY),
    ]


def test_constructor_drop_plan_ignores_borrowed_literals() -> None:
    nodes = _parse(
        """
class Packet then
    string label
    array values
end

def main(): int
    Packet p = new Packet("literal", array_new(4))
    return 0
end
"""
    )
    cls, class_methods, record_fields = _class_maps(nodes)
    vardecl = _main_vardecl(nodes)
    assert isinstance(vardecl.init_value, A.NewExpr)

    plan = constructor_field_drop_plan(
        cls.name, vardecl.init_value, class_methods, record_fields
    )

    assert [(field.name, field.kind) for field in plan.fields] == [
        ("values", DropKind.DYNAMIC_ARRAY),
    ]


def test_constructor_drop_plan_respects_materialization_callback() -> None:
    nodes = _parse(
        """
class Packet then
    string label
    array values
    public def init(label_arg: string, seed: int):
        this.label = label_arg
        this.values = array_new(4)
        this.values = array_push(this.values, seed)
    end
end

def main(): int
    Packet p = new Packet("pkt_" + str(7), 3)
    return 0
end
"""
    )
    cls, class_methods, record_fields = _class_maps(nodes)
    vardecl = _main_vardecl(nodes)
    assert isinstance(vardecl.init_value, A.NewExpr)

    def _is_materialized_arg(
        _class_name: str,
        _method_name: str,
        param_index: int,
        _arg: A.ASTNode,
        _kind: DropKind,
        _field_type: object,
    ) -> bool:
        return param_index != 0

    plan = constructor_field_drop_plan(
        cls.name,
        vardecl.init_value,
        class_methods,
        record_fields,
        is_materialized_constructor_arg=_is_materialized_arg,
    )

    assert [(field.name, field.kind) for field in plan.fields] == [
        ("values", DropKind.DYNAMIC_ARRAY),
    ]
