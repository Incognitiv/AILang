# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path.cwd()
VERIFIER = ROOT / 'verifier'

hiddenimports = ['pyflakes', 'pyflakes.api', 'pyflakes.checker', 'pylint', 'pylint.lint', 'pylint.reporters', 'pylint.reporters.text', 'astroid', 'mypy', 'mypy.api', 'bandit', 'bandit.core', 'bandit.core.node_visitor', 'radon', 'radon.complexity', 'radon.metrics', 'black', 'isort', 'isort.main', 'ruff', 'vulture', 'vulture.core', 'cohesion', 'cohesion.parser', 'pip_audit', 'detect_secrets', 'detect_secrets.core', 'detect_secrets.core.scan', 'click', 'pathspec', 'tomlkit', 'platformdirs', 'dill', 'mccabe', 'stevedore', 'typing_extensions', 'tools', 'tools.common', 'tools.complexity', 'tools.formatters', 'tools.linters', 'tools.quality', 'tools.security']
hiddenimports += collect_submodules('mypy')
hiddenimports += collect_submodules('mypy_extensions')


a = Analysis(
    [str(VERIFIER / 'core.py')],
    pathex=[],
    binaries=[],
    datas=[
        (str(VERIFIER / 'tools'), 'tools'),
        (str(VERIFIER / '.pylintrc'), '.'),
        (str(VERIFIER / 'pyproject.toml'), '.'),
    ],
    hiddenimports=hiddenimports,
    hookspath=[str(VERIFIER / 'pyinstaller_hooks')],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='verifier_windows',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
