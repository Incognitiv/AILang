from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "tools" / "make_clean_zip.py"
SPEC = importlib.util.spec_from_file_location("make_clean_zip_tool", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
mcz = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = mcz
SPEC.loader.exec_module(mcz)


def test_clean_zip_excludes_runtime_databases_and_profiles() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        keep = root / "README.md"
        keep.write_text("# ok\n", encoding="utf-8")
        for name in (
            "adapt_bench.db",
            "adapt_bench.db-shm",
            "adapt_bench.db-wal",
            "default.profraw",
            "merged.profdata",
        ):
            (root / name).write_text("generated\n", encoding="utf-8")

        files = mcz._collect_files(
            root,
            root / "out.zip",
            excluded_dir_names={n.lower() for n in mcz.DEFAULT_EXCLUDED_DIR_NAMES},
            excluded_dir_paths={p.lower() for p in mcz.DEFAULT_EXCLUDED_DIR_PATHS},
            excluded_file_paths={p.lower() for p in mcz.DEFAULT_EXCLUDED_FILE_PATHS},
            excluded_file_globs=set(mcz.DEFAULT_EXCLUDED_FILE_GLOBS),
        )

        assert [p.name for p in files] == ["README.md"]


def test_clean_zip_excludes_generated_egg_info_directories() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        keep = root / "source" / "main.py"
        keep.parent.mkdir()
        keep.write_text("print('ok')\n", encoding="utf-8")
        egg = root / "source" / "ailang_pure.egg-info"
        egg.mkdir()
        (egg / "PKG-INFO").write_text("generated\n", encoding="utf-8")

        files = mcz._collect_files(
            root,
            root / "out.zip",
            excluded_dir_names={n.lower() for n in mcz.DEFAULT_EXCLUDED_DIR_NAMES},
            excluded_dir_paths={p.lower() for p in mcz.DEFAULT_EXCLUDED_DIR_PATHS},
            excluded_file_paths={p.lower() for p in mcz.DEFAULT_EXCLUDED_FILE_PATHS},
            excluded_file_globs=set(mcz.DEFAULT_EXCLUDED_FILE_GLOBS),
        )

        assert [p.relative_to(root).as_posix() for p in files] == ["source/main.py"]
