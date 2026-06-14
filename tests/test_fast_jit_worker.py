from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPO_ROOT / "source"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from codegen.fast_jit_worker import (  # noqa: E402
    JIT_WORKER_MARKER,
    _checksum_from_worker_stdout,
    _frozen_worker_entrypoint,
    _jit_worker_cmd,
)


def test_frozen_worker_entrypoint_prefers_real_argv0(
    monkeypatch, tmp_path: Path
) -> None:
    fake_bin = tmp_path / "ailang.bin"
    fake_bin.write_bytes(b"bin")
    missing_python = tmp_path / "python"

    monkeypatch.setattr(sys, "argv", [str(fake_bin)])
    monkeypatch.setattr(sys, "executable", str(missing_python))

    resolved = _frozen_worker_entrypoint()
    assert resolved == str(fake_bin.resolve())


def test_jit_worker_cmd_uses_frozen_entrypoint(monkeypatch, tmp_path: Path) -> None:
    fake_bin = tmp_path / "ailang.bin"
    fake_bin.write_bytes(b"bin")
    source_file = tmp_path / "prog.ail"
    source_file.write_text("main(): int\n  return 0\nend\n", encoding="utf-8")

    monkeypatch.setattr(sys, "argv", [str(fake_bin)])
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python"))
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    cmd = _jit_worker_cmd(
        str(source_file),
        run_count=2,
        warmup_count=1,
        optimize=True,
        profile=False,
        flame_path="",
        sample_hz=0,
        capture_output=True,
    )
    assert cmd[0] == str(fake_bin.resolve())
    assert "__jit_worker__" in cmd
    assert "--capture-output" in cmd
    assert "--jit-opt" in cmd


def test_jit_worker_cmd_treats_missing_sys_executable_as_packaged(
    monkeypatch, tmp_path: Path
) -> None:
    fake_bin = tmp_path / "ailang.bin"
    fake_bin.write_bytes(b"bin")
    source_file = tmp_path / "prog.ail"
    source_file.write_text("main(): int\n  return 0\nend\n", encoding="utf-8")

    monkeypatch.setattr(sys, "argv", [str(fake_bin)])
    monkeypatch.setattr(sys, "executable", str(tmp_path / "missing_python"))
    monkeypatch.setattr(sys, "frozen", False, raising=False)

    cmd = _jit_worker_cmd(
        str(source_file),
        run_count=1,
        warmup_count=0,
        optimize=False,
        profile=False,
        flame_path="",
        sample_hz=0,
        capture_output=False,
    )
    assert cmd[0] == str(fake_bin.resolve())


def test_jit_worker_cmd_passes_opt_level_and_ir_dump(
    monkeypatch, tmp_path: Path
) -> None:
    fake_bin = tmp_path / "ailang.bin"
    fake_bin.write_bytes(b"bin")
    source_file = tmp_path / "prog.ail"
    dump_file = tmp_path / "jit.ll"
    source_file.write_text("main(): int\n  return 0\nend\n", encoding="utf-8")

    monkeypatch.setattr(sys, "argv", [str(fake_bin)])
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python"))
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    cmd = _jit_worker_cmd(
        str(source_file),
        run_count=1,
        warmup_count=0,
        optimize=True,
        profile=False,
        flame_path="",
        sample_hz=0,
        capture_output=False,
        jit_opt=2,
        dump_ir_path=str(dump_file),
    )

    assert cmd[cmd.index("--jit-opt") + 1] == "2"
    assert cmd[cmd.index("--jit-dump-ir") + 1] == str(dump_file.resolve())


def test_checksum_from_worker_stdout_uses_measured_runs_only() -> None:
    stdout = "\n".join(
        [
            "111",
            "222",
            "222",
            f'{JIT_WORKER_MARKER}{{"status":"ok","checksum":null}}',
        ]
    )

    checksum, note = _checksum_from_worker_stdout(
        stdout, run_count=2, warmup_count=1
    )

    assert checksum == 222
    assert note is None


def test_checksum_from_worker_stdout_reports_nondeterminism() -> None:
    stdout = "\n".join(
        [
            "111",
            "222",
            "333",
            f'{JIT_WORKER_MARKER}{{"status":"ok","checksum":null}}',
        ]
    )

    checksum, note = _checksum_from_worker_stdout(
        stdout, run_count=2, warmup_count=1
    )

    assert checksum == 222
    assert note is not None
    assert "Non-deterministic" in note
