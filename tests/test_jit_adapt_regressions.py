from __future__ import annotations

import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
AILANG = REPO_ROOT / "ailang.py"


def _run_jit(tmp_path: Path, name: str, source: str) -> subprocess.CompletedProcess[str]:
    src = tmp_path / name
    src.write_text(source, encoding="ascii")
    return subprocess.run(
        [sys.executable, str(AILANG), str(src)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_jit_returned_class_keeps_fields_after_callee_cleanup(tmp_path: Path) -> None:
    proc = _run_jit(
        tmp_path,
        "class_return_escape.ail",
        """\
class Box then
    public int n = 0
    public string s = ""

    public def init():
        this.n = 0
        this.s = ""
    end

    public def ~Box():
        this.n = 0
        this.s = ""
    end
end

def make_box(): Box
    b = new Box()
    b.n = 42
    b.s = "kept"
    return b
end

int main():
    x = make_box()
    print str(x.n) + ":" + x.s
    return 0
end
""",
    )

    combined = proc.stdout + proc.stderr
    assert proc.returncode == 0, combined
    assert "fatal exception" not in combined.lower()
    assert "42:kept" in proc.stdout


def test_jit_split_get_on_inferred_str_array_uses_str_array_layout(
    tmp_path: Path,
) -> None:
    proc = _run_jit(
        tmp_path,
        "split_str_array_interop.ail",
        """\
int main():
    raw = split("what is raman", " ")
    parts = str_array_new(3)
    i = 0
    while i < split_len(raw) then
        p = split_str_get(raw, i)
        parts = str_array_push(parts, p)
        i = i + 1
    end
    print str(split_len(parts)) + ":" + split_str_get(parts, 0)
    return 0
end
""",
    )

    combined = proc.stdout + proc.stderr
    assert proc.returncode == 0, combined
    assert "fatal exception" not in combined.lower()
    assert "3:what" in proc.stdout
