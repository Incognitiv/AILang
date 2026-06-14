from __future__ import annotations

import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPO_ROOT / "source"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from codegen.fast_jit import compile_to_ir_fast  # noqa: E402
from transpiler.core import transpile_file  # noqa: E402


SQL_OPEN_SOURCE = """\
def main(): int
    db = sql_open("file:ailang_uri_test?mode=memory&cache=private&mutex=no")
    if db == 0 then
        return 1
    end
    sql_close(db)
    return 0
end
"""


def test_c_backend_sql_open_uses_uri_capable_open_v2() -> None:
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "sqlite_uri.ail"
        src.write_text(SQL_OPEN_SOURCE, encoding="utf-8")
        c_text = transpile_file(str(src))
    assert "sqlite3_open_v2" in c_text
    assert "SQLITE_OPEN_URI" in c_text
    assert "sqlite3_open(path" not in c_text


def test_llvm_sql_open_uses_uri_capable_open_v2() -> None:
    ir_text = compile_to_ir_fast(SQL_OPEN_SOURCE, source_file="sqlite_uri.ail")
    assert '@"sqlite3_open_v2"' in ir_text
    assert '@"sqlite3_open"' not in ir_text
    assert "i32 70" in ir_text
