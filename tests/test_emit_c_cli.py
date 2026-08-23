from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
AILANG = REPO_ROOT / "ailang.py"


def test_emit_c_writes_c_source_without_linking(tmp_path: Path) -> None:
    src = tmp_path / "native_unit.ail"
    out_c = tmp_path / "native_unit.c"
    src.write_text(
        """\
@export("native_answer")
def answer(): i32
    return 42
end
""",
        encoding="utf-8",
    )

    proc = subprocess.run(
        [sys.executable, str(AILANG), str(src), "--emit-c", "-o", str(out_c)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr or proc.stdout
    text = out_c.read_text(encoding="utf-8")
    assert "int32_t native_answer(void)" in text
    assert "int main(" not in text
