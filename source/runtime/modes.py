"""
AILang Compilation Modes
Supports freestanding (no libc) and hosted (with OS) compilation
"""

from enum import Enum, auto
from typing import NamedTuple


class CompilationMode(Enum):
    """Compilation target modes"""

    HOSTED = auto()  # Default: libc available, OS present
    FREESTANDING = auto()  # No libc, bare metal / kernel / EFI


class ModeConfig(NamedTuple):
    """Configuration for a compilation mode"""

    name: str
    has_libc: bool
    has_file_io: bool
    has_sqlite: bool
    has_print: bool
    has_input: bool
    linker_flags: list[str]
    defines: list[str]


# Mode configurations
MODES: dict[CompilationMode, ModeConfig] = {
    CompilationMode.HOSTED: ModeConfig(
        name="hosted",
        has_libc=True,
        has_file_io=True,
        has_sqlite=True,
        has_print=True,
        has_input=True,
        linker_flags=[],
        defines=[],
    ),
    CompilationMode.FREESTANDING: ModeConfig(
        name="freestanding",
        has_libc=False,
        has_file_io=False,
        has_sqlite=False,
        has_print=True,  # Allow print in freestanding for JIT debugging
        has_input=False,
        linker_flags=["-nostdlib", "-ffreestanding"],
        defines=["__FREESTANDING__"],
    ),
}


class CompilationContext:
    """Global compilation context - tracks current mode and settings"""

    _mode: CompilationMode = CompilationMode.HOSTED
    _config: ModeConfig = MODES[CompilationMode.HOSTED]
    _is_jit: bool = True  # True when running JIT, False for AOT

    @classmethod
    def set_mode(cls, mode: CompilationMode) -> None:
        """Set the compilation mode"""
        cls._mode = mode
        cls._config = MODES[mode]

    @classmethod
    def set_jit(cls, is_jit: bool) -> None:
        """Set whether we're running JIT (True) or AOT (False)"""
        cls._is_jit = is_jit

    @classmethod
    def is_jit(cls) -> bool:
        """Check if running in JIT mode (on host OS)"""
        return cls._is_jit

    @classmethod
    def get_mode(cls) -> CompilationMode:
        """Get current compilation mode"""
        return cls._mode

    @classmethod
    def get_config(cls) -> ModeConfig:
        """Get current mode configuration"""
        return cls._config

    @classmethod
    def is_freestanding(cls) -> bool:
        """Check if compiling in freestanding mode"""
        return cls._mode == CompilationMode.FREESTANDING

    @classmethod
    def has_feature(cls, feature: str) -> bool:
        """Check if a feature is available in current mode"""
        # In JIT mode, we always have OS features available for debugging
        if cls._is_jit and feature in ("print", "libc"):
            return True
        return getattr(cls._config, f"has_{feature}", False)

    @classmethod
    def require_feature(cls, feature: str, builtin_name: str) -> None:
        """Raise error if feature not available"""
        if not cls.has_feature(feature):
            mode_name = cls._config.name
            if cls._is_jit:
                # In JIT, we're on an OS, so most things work
                return
            raise RuntimeError(
                f"Built-in '{builtin_name}' requires '{feature}' "
                f"which is not available in {mode_name} mode. "
                f"Use --mode=hosted or provide implementation via #template."
            )
