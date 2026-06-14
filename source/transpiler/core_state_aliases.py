"""CTranspiler state aliases backed by runtime/type-info containers."""

from __future__ import annotations

from typing import Any, Dict, List, Set, Tuple

ClassField = Tuple[str, str, str]
RecordField = Tuple[str, str]


class _CTranspilerStateAliasMixin:
    @property
    def used_helpers(self: Any) -> Set[str]:
        return self.runtime_needs.helpers

    @used_helpers.setter
    def used_helpers(self: Any, value: Set[str]) -> None:
        self.runtime_needs.helpers = value

    @property
    def _spawn_targets(self: Any) -> Dict[str, List[str]]:
        return self.runtime_needs.spawn_targets

    @_spawn_targets.setter
    def _spawn_targets(self: Any, value: Dict[str, List[str]]) -> None:
        self.runtime_needs.spawn_targets = value

    @property
    def _needs_arrays(self: Any) -> bool:
        return self.runtime_needs.arrays

    @_needs_arrays.setter
    def _needs_arrays(self: Any, value: bool) -> None:
        self.runtime_needs.arrays = value

    @property
    def _needs_dicts(self: Any) -> bool:
        return self.runtime_needs.dicts

    @_needs_dicts.setter
    def _needs_dicts(self: Any, value: bool) -> None:
        self.runtime_needs.dicts = value

    @property
    def _needs_dynamic_arrays(self: Any) -> bool:
        return self.runtime_needs.dynamic_arrays

    @_needs_dynamic_arrays.setter
    def _needs_dynamic_arrays(self: Any, value: bool) -> None:
        self.runtime_needs.dynamic_arrays = value

    @property
    def _needs_threading(self: Any) -> bool:
        return self.runtime_needs.threading

    @_needs_threading.setter
    def _needs_threading(self: Any, value: bool) -> None:
        self.runtime_needs.threading = value

    @property
    def _needs_atomics(self: Any) -> bool:
        return self.runtime_needs.atomics

    @_needs_atomics.setter
    def _needs_atomics(self: Any, value: bool) -> None:
        self.runtime_needs.atomics = value

    @property
    def _needs_channels(self: Any) -> bool:
        return self.runtime_needs.channels

    @_needs_channels.setter
    def _needs_channels(self: Any, value: bool) -> None:
        self.runtime_needs.channels = value

    @property
    def _needs_inline_asm(self: Any) -> bool:
        return self.runtime_needs.inline_asm

    @_needs_inline_asm.setter
    def _needs_inline_asm(self: Any, value: bool) -> None:
        self.runtime_needs.inline_asm = value

    @property
    def _needs_sync(self: Any) -> bool:
        return self.runtime_needs.sync

    @_needs_sync.setter
    def _needs_sync(self: Any, value: bool) -> None:
        self.runtime_needs.sync = value

    @property
    def _needs_exceptions(self: Any) -> bool:
        return self.runtime_needs.exceptions

    @_needs_exceptions.setter
    def _needs_exceptions(self: Any, value: bool) -> None:
        self.runtime_needs.exceptions = value

    @property
    def _needs_stream_cleanup(self: Any) -> bool:
        return self.runtime_needs.stream_cleanup

    @_needs_stream_cleanup.setter
    def _needs_stream_cleanup(self: Any, value: bool) -> None:
        self.runtime_needs.stream_cleanup = value

    @property
    def records(self: Any) -> Dict[str, List[RecordField]]:
        return self.type_info.records

    @records.setter
    def records(self: Any, value: Dict[str, List[RecordField]]) -> None:
        self.type_info.records = value

    @property
    def unions(self: Any) -> Dict[str, List[RecordField]]:
        return self.type_info.unions

    @unions.setter
    def unions(self: Any, value: Dict[str, List[RecordField]]) -> None:
        self.type_info.unions = value

    @property
    def enums(self: Any) -> Dict[str, List[Tuple[str, int]]]:
        return self.type_info.enums

    @enums.setter
    def enums(self: Any, value: Dict[str, List[Tuple[str, int]]]) -> None:
        self.type_info.enums = value
