#!/usr/bin/env python3
"""Cython full-module experiment.

Builds a staging tree where nearly the entire `source/` package graph is
cythonized into extension modules, then builds an embedded launcher from
`ailang.py` and runs smoke checks.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from textwrap import dedent

REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class CmdResult:
    returncode: int
    stdout: str
    stderr: str


def _run(
    cmd: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    timeout: int = 1800,
) -> CmdResult:
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    return CmdResult(proc.returncode, proc.stdout or "", proc.stderr or "")


def _discover_modules(source_root: Path) -> list[tuple[str, Path]]:
    modules: list[tuple[str, Path]] = []
    for py_file in sorted(source_root.rglob("*.py")):
        if py_file.name == "__init__.py":
            continue
        rel = py_file.relative_to(source_root).with_suffix("")
        module_name = ".".join(rel.parts)
        modules.append((module_name, py_file))
    return modules


def _count_extensions(source_root: Path) -> int:
    count = 0
    for p in source_root.rglob("*"):
        if p.suffix in {".so", ".pyd"}:
            count += 1
    return count


def _file_size(path: Path) -> int:
    if not path.exists():
        return 0
    return int(path.stat().st_size)


def _make_stage(stage_dir: Path) -> None:
    if stage_dir.exists():
        shutil.rmtree(stage_dir)
    stage_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(REPO_ROOT / "source", stage_dir / "source")
    shutil.copy2(REPO_ROOT / "ailang.py", stage_dir / "ailang.py")
    if (REPO_ROOT / "benchmarks").exists():
        shutil.copytree(REPO_ROOT / "benchmarks", stage_dir / "benchmarks")


def _write_setup(stage_dir: Path) -> Path:
    setup_path = stage_dir / "setup_cython_full.py"
    setup_text = (
        dedent(
            """
        from setuptools import Extension, setup
        from pathlib import Path
        from Cython.Build import cythonize

        SOURCE_ROOT = Path(__file__).resolve().parent / "source"
        EXTENSIONS = []
        for py_file in sorted(SOURCE_ROOT.rglob("*.py")):
            if py_file.name == "__init__.py":
                continue
            rel = py_file.relative_to(SOURCE_ROOT).with_suffix("")
            module_name = ".".join(rel.parts)
            EXTENSIONS.append(Extension(module_name, [str(py_file)]))

        setup(
            name="ailang-cython-full-experiment",
            package_dir={"": "source"},
            ext_modules=cythonize(
                EXTENSIONS,
                language_level=3,
                annotate=False,
                compiler_directives={
                    "boundscheck": False,
                    "wraparound": False,
                    "nonecheck": False,
                },
            )
        )
        """
        ).strip()
        + "\n"
    )
    setup_path.write_text(setup_text, encoding="utf-8")
    return setup_path


def _build_cython_extensions(stage_dir: Path) -> CmdResult:
    return _run(
        [sys.executable, "setup_cython_full.py", "build_ext", "--inplace"],
        cwd=stage_dir,
    )


def _build_embedded_launcher(stage_dir: Path) -> tuple[CmdResult, CmdResult]:
    c_file = stage_dir / "ailang_embed.c"
    exe_file = stage_dir / "ailang_cython_full"
    cython_res = _run(
        [
            sys.executable,
            "-m",
            "cython",
            "--embed",
            "-3",
            str(stage_dir / "ailang.py"),
            "-o",
            str(c_file),
        ],
        cwd=stage_dir,
    )
    if cython_res.returncode != 0:
        return cython_res, CmdResult(1, "", "skip gcc due to cython failure")

    cfg_candidates = [
        sys.executable + "-config",
        "python3-config",
        "python-config",
    ]
    cfg_args: list[str] = []
    for cfg in cfg_candidates:
        if Path(cfg).is_absolute() and not Path(cfg).exists():
            continue
        if not Path(cfg).is_absolute() and shutil.which(cfg) is None:
            continue
        cfg_res = _run([cfg, "--embed", "--cflags", "--ldflags"], cwd=stage_dir)
        if cfg_res.returncode == 0 and cfg_res.stdout.strip():
            cfg_args = cfg_res.stdout.split()
            break
    if not cfg_args:
        return cython_res, CmdResult(2, "", "python-config flags unavailable")

    gcc_cmd = ["gcc", "-O3", str(c_file), "-o", str(exe_file), *cfg_args]
    gcc_res = _run(gcc_cmd, cwd=stage_dir)
    return cython_res, gcc_res


def _smoke(stage_dir: Path) -> list[tuple[str, CmdResult]]:
    exe = stage_dir / "ailang_cython_full"
    if not exe.exists():
        return [("launcher-missing", CmdResult(1, "", "missing launcher"))]

    c_out = stage_dir / "fib_cython_full"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(stage_dir / "source")
    checks: list[tuple[str, CmdResult]] = []
    checks.append(
        (
            "version",
            _run([str(exe), "--version"], cwd=stage_dir, env=env, timeout=120),
        )
    )
    checks.append(
        (
            "check-fib",
            _run(
                [str(exe), "benchmarks/ailang/fib_mix.ail", "--check"],
                cwd=stage_dir,
                env=env,
                timeout=180,
            ),
        )
    )
    checks.append(
        (
            "compile-c-fib",
            _run(
                [
                    str(exe),
                    "benchmarks/ailang/fib_mix.ail",
                    "--backend=c",
                    "-o",
                    str(c_out),
                ],
                cwd=stage_dir,
                env=env,
                timeout=240,
            ),
        )
    )
    return checks


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=REPO_ROOT / "out" / "package" / "cython_full_experiment",
        help="Experiment output directory.",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=REPO_ROOT / "benchmarks" / "results" / "cython_full_experiment.md",
        help="Markdown report output path.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    stage_dir = out_dir / "stage"

    _make_stage(stage_dir)
    modules = _discover_modules(stage_dir / "source")
    setup_path = _write_setup(stage_dir)
    build_res = _build_cython_extensions(stage_dir)
    cython_res, gcc_res = _build_embedded_launcher(stage_dir)
    smoke = _smoke(stage_dir)

    ext_count = _count_extensions(stage_dir / "source")
    launcher_size = _file_size(stage_dir / "ailang_cython_full")
    report_path = args.report.resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    lines.append("# Cython Full Experiment")
    lines.append("")
    lines.append(f"- modules_requested: {len(modules)}")
    lines.append(f"- modules_built: {ext_count}")
    lines.append(f"- launcher_size_bytes: {launcher_size}")
    lines.append(f"- stage_dir: `{stage_dir}`")
    lines.append(f"- setup_script: `{setup_path}`")
    lines.append("")
    lines.append("## Build Results")
    lines.append("")
    lines.append(f"- cythonize_build_ext_rc: {build_res.returncode}")
    lines.append(f"- embed_cython_rc: {cython_res.returncode}")
    lines.append(f"- embed_gcc_rc: {gcc_res.returncode}")
    lines.append("")
    lines.append("## Smoke")
    lines.append("")
    for name, res in smoke:
        lines.append(f"- {name}: rc={res.returncode}")
        tail = (res.stdout + "\n" + res.stderr).strip().splitlines()[-8:]
        for t in tail:
            lines.append(f"  - {t}")
    lines.append("")
    lines.append("## Notes")
    lines.append("")
    lines.append(
        "- This experiment compiles most modules, but Cython still keeps Python"
        " runtime semantics and imports."
    )
    lines.append(
        "- True standalone distribution still depends on bundling strategy"
        " (e.g. PyInstaller/Nuitka)."
    )

    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"report: {report_path}")
    print(f"stage: {stage_dir}")
    print(f"modules_requested={len(modules)} modules_built={ext_count}")
    print(f"launcher_size_bytes={launcher_size}")
    print(
        f"build_rc={build_res.returncode} embed_rc={cython_res.returncode}/{gcc_res.returncode}"
    )
    failed_smoke = [name for name, res in smoke if res.returncode != 0]
    if failed_smoke:
        print("failed_smoke=" + ",".join(failed_smoke))
    return 0 if (build_res.returncode == 0 and gcc_res.returncode == 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
