from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPO_ROOT / "source"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from lexer.scan import tokenize  # noqa: E402
from parser import ast as A  # noqa: E402
from parser.parser import Parser  # noqa: E402
from transpiler.import_resolver import ImportResolver  # noqa: E402


def _resolve_source(path: Path) -> list[A.ASTNode]:
    parser = Parser(tokenize(path.read_text(encoding="utf-8")))
    return ImportResolver().run(parser.parse_program(), str(path))


def test_parser_accepts_cimport_directive() -> None:
    parser = Parser(tokenize('#cimport "native.probe.json"\n'))
    nodes = parser.parse_program()
    assert len(nodes) == 1
    assert isinstance(nodes[0], A.CImport)
    assert nodes[0].path == "native.probe.json"


def test_cimport_consumes_generated_probe_json(tmp_path: Path) -> None:
    probe = tmp_path / "native.probe.json"
    probe.write_text(
        json.dumps(
            {
                "ok": True,
                "spec_name": "native",
                "compiler": "fixture",
                "headers": [{"path": "stdint.h", "system": True}],
                "link_flags": ["-lnative"],
                "constants": [{"name": "NATIVE_ANSWER", "expr": "NATIVE_ANSWER", "value": 42}],
                "macros": [{"name": "NATIVE_MASK", "expr": "NATIVE_MASK", "kind": "constant", "value": 8}],
                "enums": [
                    {
                        "name": "NativeMode",
                        "variants": [
                            {"name": "NATIVE_MODE_A", "expr": "NATIVE_MODE_A", "value": 1},
                            {"name": "NATIVE_MODE_B", "expr": "NATIVE_MODE_B", "value": 2},
                        ],
                    }
                ],
                "records": [
                    {
                        "name": "NativeBits",
                        "c_name": "struct NativeBits",
                        "kind": "extern",
                        "size": 8,
                        "align": 4,
                        "fields": [
                            {
                                "name": "flag",
                                "offset": 0,
                                "size": 0,
                                "type": "uint",
                                "bit_width": 1,
                                "bit_offset": 0,
                            },
                            {"name": "count", "offset": 4, "size": 4, "type": "uint"},
                        ],
                    }
                ],
                "functions": [
                    {
                        "name": "native_tick",
                        "return_type": "int",
                        "params": [{"name": "value", "type": "int"}],
                    }
                ],
                "wrappers": [],
                "errors": [],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    source = tmp_path / "uses_cimport.ail"
    source.write_text(
        f"""\
#cimport "{probe.name}"

def main(): int
    return NATIVE_ANSWER
end
""",
        encoding="utf-8",
    )

    nodes = _resolve_source(source)
    assert not any(isinstance(node, A.CImport) for node in nodes)
    assert any(isinstance(node, A.CInclude) and node.path == "stdint.h" for node in nodes)
    assert any(isinstance(node, A.LinkDirective) and node.flags == "-lnative" for node in nodes)
    constants = {
        node.var_name: int(node.init_value.value)
        for node in nodes
        if isinstance(node, A.VarDecl)
    }
    assert constants["NATIVE_ANSWER"] == 42
    assert constants["NATIVE_MASK"] == 8
    assert constants["SIZEOF_NativeBits"] == 8
    assert constants["ALIGNOF_NativeBits"] == 4
    assert constants["OFFSETOF_NativeBits_count"] == 4
    assert constants["BITWIDTH_NativeBits_flag"] == 1
    assert constants["BITOFFSET_NativeBits_flag"] == 0
    assert any(isinstance(node, A.EnumDef) and node.name == "NativeMode" for node in nodes)
    records = [node for node in nodes if isinstance(node, A.ExternRecordDef)]
    assert records[0].name == "NativeBits"
    assert records[0].bitfields["flag"]["width"] == 1
    assert any(isinstance(node, A.ExternFn) and node.name == "native_tick" for node in nodes)


def test_cimport_consumes_raw_cbind_spec_directly(tmp_path: Path) -> None:
    if shutil.which("gcc") is None and shutil.which("clang") is None:
        pytest.skip("no C compiler available")

    header = tmp_path / "direct_header.h"
    header.write_text(
        """\
#define DIRECT_ANSWER 77
typedef enum DirectMode { DIRECT_MODE = 5 } DirectMode;
typedef struct DirectRect { int left; int top; } DirectRect;
""",
        encoding="utf-8",
    )
    spec = tmp_path / "direct.cbind.json"
    spec.write_text(
        json.dumps(
            {
                "name": "direct",
                "headers": [{"path": "direct_header.h", "system": False}],
                "macros": ["DIRECT_ANSWER"],
                "enums": [{"name": "DirectMode", "variants": ["DIRECT_MODE"]}],
                "records": [
                    {
                        "name": "DirectRect",
                        "c_name": "DirectRect",
                        "kind": "extern",
                        "fields": [
                            {"name": "left", "type": "short"},
                            {"name": "top", "type": "short"},
                        ],
                    }
                ],
                "wrappers": [
                    {
                        "name": "direct_answer_wrap",
                        "return_type": "int",
                        "expr": "DIRECT_ANSWER",
                    }
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    source = tmp_path / "direct_import.ail"
    source.write_text(
        f"""\
#cimport "{spec.name}"

def main(): int
    return DIRECT_ANSWER
end
""",
        encoding="utf-8",
    )

    nodes = _resolve_source(source)
    constants = {
        node.var_name: int(node.init_value.value)
        for node in nodes
        if isinstance(node, A.VarDecl)
    }
    assert constants["DIRECT_ANSWER"] == 77
    assert constants["SIZEOF_DirectRect"] == 8
    assert constants["ALIGNOF_DirectRect"] == 4
    assert constants["OFFSETOF_DirectRect_left"] == 0
    assert constants["OFFSETOF_DirectRect_top"] == 4
    enum = next(node for node in nodes if isinstance(node, A.EnumDef))
    assert enum.name == "DirectMode"
    assert enum.values == [("DIRECT_MODE", 5)]
    assert any(
        isinstance(node, A.ExternFn) and node.name == "direct_answer_wrap"
        for node in nodes
    )
    direct_rect = next(
        node
        for node in nodes
        if isinstance(node, A.ExternRecordDef) and node.name == "DirectRect"
    )
    assert direct_rect.c_name == "DirectRect"
    assert direct_rect.c_name_explicit is True
    c_units = [node for node in nodes if isinstance(node, A.TemplateBlock)]
    assert c_units
    assert "direct_answer_wrap" in c_units[0].code
