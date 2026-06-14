"""
AILang Unsafe Operations Registry

Tracks 'unsafe' keyword usage and prompts user for confirmation
during compilation. Supports interactive and batch modes.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class UnsafeMode(Enum):
    """How to handle unsafe operations"""

    INTERACTIVE = "interactive"  # Ask user for each (default)
    ASSUME_YES = "assume-unsafe"  # Auto-accept all
    DENY_ALL = "deny-unsafe"  # Reject all
    FREESTANDING = "freestanding"  # No prompts (kernel mode)


@dataclass
class UnsafeOperation:
    """Record of an unsafe operation in source code"""

    line: int
    description: str
    operation: str  # e.g., "char_at", "array_access", "poke"
    approved: Optional[bool] = None  # None = not yet asked


class UnsafeRegistry:
    """
    Manages unsafe operations throughout compilation.

    Usage:
        registry = UnsafeRegistry(mode=UnsafeMode.INTERACTIVE)

        # During parsing/codegen, register unsafe operations:
        if call.unsafe:
            registry.register(line, "char_at(s, 100, unsafe)", "char_at")

        # Before final compilation, prompt user:
        if not registry.prompt_all():
            sys.exit(1)  # User denied something
    """

    def __init__(self, mode: UnsafeMode = UnsafeMode.INTERACTIVE) -> None:
        self.mode = mode
        self.operations: list[UnsafeOperation] = []
        self._prompted = False

    def register(self, line: int, description: str, operation: str) -> None:
        """Register an unsafe operation found during parsing."""
        self.operations.append(UnsafeOperation(line, description, operation))

    def prompt_all(self) -> bool:
        """
        Prompt user for all registered unsafe operations.

        Returns True if all approved, False if any denied.
        In non-interactive modes, auto-approves or auto-denies.
        """
        if self._prompted:
            return all(op.approved for op in self.operations)
        self._prompted = True

        if not self.operations:
            return True  # Nothing to approve

        if self.mode == UnsafeMode.FREESTANDING:
            # Kernel mode - everything allowed, no prompts
            for op in self.operations:
                op.approved = True
            return True

        if self.mode == UnsafeMode.ASSUME_YES:
            # Batch mode - auto-approve
            print(
                f"\n[WARNING] Auto-approving {len(self.operations)} unsafe operation(s) (--assume-unsafe)"
            )
            for op in self.operations:
                print(f"  Line {op.line}: {op.description}")
                op.approved = True
            print()
            return True

        if self.mode == UnsafeMode.DENY_ALL:
            # Strict mode - auto-deny
            print(
                f"\n[DENIED] Denying {len(self.operations)} unsafe operation(s) (--deny-unsafe)"
            )
            for op in self.operations:
                print(f"  Line {op.line}: {op.description}")
                op.approved = False
            print("\nRemove 'unsafe' keywords or use --assume-unsafe to proceed.\n")
            return False

        # Interactive mode - ask for each
        return self._prompt_interactive()

    def _prompt_interactive(self) -> bool:
        """Interactively prompt for each unsafe operation."""
        print(f"\n[WARNING] Found {len(self.operations)} unsafe operation(s):\n")

        all_approved = True
        for op in self.operations:
            warning = self._get_warning_for_operation(op.operation)
            print(f"Line {op.line}: {op.description}")
            print(f"  {warning}")

            try:
                response = input("  Continue at your own risk? [Y/n] ").strip().lower()
            except EOFError:
                # Non-interactive input (piped), deny by default
                print("  (non-interactive input, denying)")
                response = "n"

            if response in ("", "y", "yes"):
                op.approved = True
                print("  [OK] Approved\n")
            else:
                op.approved = False
                all_approved = False
                print("  [NO] Denied\n")

        if not all_approved:
            print("Error: Unsafe operation(s) denied by user.")
            print("Remove 'unsafe' keywords or accept the risks to proceed.\n")

        return all_approved

    def _get_warning_for_operation(self, operation: str) -> str:
        """Get a human-readable warning for an operation type."""
        warnings = {
            "char_at": "[!] Bypasses string bounds checking - may read garbage or crash",
            "array_access": "[!] Bypasses array bounds checking - may corrupt memory or crash",
            "poke": "[!] Direct memory write - may corrupt system state or crash",
            "peek": "[!] Direct memory read - may read sensitive data or crash",
            "division": "[!] Bypasses division-by-zero check - may cause undefined behavior",
            "shift": "[!] Bypasses shift bounds check - may cause undefined behavior",
            "cast": "[!] Bypasses overflow check - may truncate or wrap unexpectedly",
            "alloc": "[!] Bypasses allocation limits - may exhaust memory",
        }
        return warnings.get(
            operation, "[!] Bypasses safety check - may cause undefined behavior"
        )

    def has_unsafe(self) -> bool:
        """Check if any unsafe operations were registered."""
        return len(self.operations) > 0

    def clear(self) -> None:
        """Clear all registered operations (for REPL reset)."""
        self.operations.clear()
        self._prompted = False


class _RegistryHolder:
    """Holder for the global registry instance to avoid module-level global."""

    instance: Optional[UnsafeRegistry] = None


def get_registry() -> UnsafeRegistry:
    """Get the global unsafe registry, creating if needed."""
    if _RegistryHolder.instance is None:
        _RegistryHolder.instance = UnsafeRegistry()
    return _RegistryHolder.instance


def set_registry(registry: UnsafeRegistry) -> None:
    """Set the global unsafe registry (for mode configuration)."""
    _RegistryHolder.instance = registry


def register_unsafe(line: int, description: str, operation: str) -> None:
    """Convenience function to register an unsafe operation."""
    get_registry().register(line, description, operation)


def prompt_unsafe() -> bool:
    """Convenience function to prompt for all unsafe operations."""
    return get_registry().prompt_all()
