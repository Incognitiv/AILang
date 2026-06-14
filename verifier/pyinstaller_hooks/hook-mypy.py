# pylint: disable=invalid-name
"""PyInstaller hook for mypy - ensures mypyc-compiled extensions are bundled."""

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
)

hiddenimports = collect_submodules("mypy")
datas = collect_data_files("mypy")
binaries = collect_dynamic_libs("mypy")
