"""Threading, synchronization, and system-call builtins for ``ExprGenerator``.

Extracted from ``emit_expressions.py`` as part of the LLVM expression
split.
"""

from __future__ import annotations

from typing import Any

from .expr_threading_core import _builtin_atomic_add as _m_builtin_atomic_add
from .expr_threading_core import _builtin_atomic_cmpxchg as _m_builtin_atomic_cmpxchg
from .expr_threading_core import _builtin_atomic_exchange as _m_builtin_atomic_exchange
from .expr_threading_core import _builtin_atomic_load as _m_builtin_atomic_load
from .expr_threading_core import _builtin_atomic_store as _m_builtin_atomic_store
from .expr_threading_core import _builtin_atomic_sub as _m_builtin_atomic_sub
from .expr_threading_core import _builtin_num_cpus as _m_builtin_num_cpus
from .expr_threading_core import _builtin_sleep_ms as _m_builtin_sleep_ms
from .expr_threading_core import _builtin_thread_id as _m_builtin_thread_id
from .expr_threading_core import _builtin_yield_thread as _m_builtin_yield_thread
from .expr_threading_core import _get_variable_ptr as _m_get_variable_ptr
from .expr_threading_core import _join_pthread as _m_join_pthread
from .expr_threading_core import _join_windows as _m_join_windows
from .expr_threading_core import _spawn_pthread as _m_spawn_pthread
from .expr_threading_core import _spawn_windows as _m_spawn_windows
from .expr_threading_core import visit_AtomicOp as _m_visit_AtomicOp
from .expr_threading_core import visit_Await as _m_visit_Await
from .expr_threading_core import visit_Join as _m_visit_Join
from .expr_threading_core import visit_Spawn as _m_visit_Spawn
from .expr_threading_socket import _build_sockaddr_in as _m_build_sockaddr_in
from .expr_threading_socket import _builtin_tcp_accept as _m_builtin_tcp_accept
from .expr_threading_socket import _builtin_tcp_close as _m_builtin_tcp_close
from .expr_threading_socket import _builtin_tcp_listen as _m_builtin_tcp_listen
from .expr_threading_socket import _builtin_tcp_recv as _m_builtin_tcp_recv
from .expr_threading_socket import _builtin_tcp_send as _m_builtin_tcp_send
from .expr_threading_socket import _emit_htons as _m_emit_htons
from .expr_threading_socket import _emit_libc_memset as _m_emit_libc_memset
from .expr_threading_socket import (
    _emit_socket_close_native as _m_emit_socket_close_native,
)
from .expr_threading_socket import _emit_socket_nodelay as _m_emit_socket_nodelay
from .expr_threading_socket import _ensure_wsa_init as _m_ensure_wsa_init
from .expr_threading_socket import _socket_handle_to_i64 as _m_socket_handle_to_i64
from .expr_threading_socket import _socket_handle_ty as _m_socket_handle_ty
from .expr_threading_socket import _socket_invalid_const as _m_socket_invalid_const
from .expr_threading_socket import _socket_is_windows as _m_socket_is_windows
from .expr_threading_sync import _builtin_cond_broadcast as _m_builtin_cond_broadcast
from .expr_threading_sync import _builtin_cond_create as _m_builtin_cond_create
from .expr_threading_sync import _builtin_cond_destroy as _m_builtin_cond_destroy
from .expr_threading_sync import _builtin_cond_signal as _m_builtin_cond_signal
from .expr_threading_sync import _builtin_cond_wait as _m_builtin_cond_wait
from .expr_threading_sync import _builtin_mutex_create as _m_builtin_mutex_create
from .expr_threading_sync import _builtin_mutex_destroy as _m_builtin_mutex_destroy
from .expr_threading_sync import _builtin_mutex_lock as _m_builtin_mutex_lock
from .expr_threading_sync import _builtin_mutex_unlock as _m_builtin_mutex_unlock
from .expr_threading_sync import _builtin_rwlock_create as _m_builtin_rwlock_create
from .expr_threading_sync import _builtin_rwlock_destroy as _m_builtin_rwlock_destroy
from .expr_threading_sync import (
    _builtin_rwlock_read_lock as _m_builtin_rwlock_read_lock,
)
from .expr_threading_sync import (
    _builtin_rwlock_read_unlock as _m_builtin_rwlock_read_unlock,
)
from .expr_threading_sync import (
    _builtin_rwlock_write_lock as _m_builtin_rwlock_write_lock,
)
from .expr_threading_sync import (
    _builtin_rwlock_write_unlock as _m_builtin_rwlock_write_unlock,
)
from .expr_threading_sync import _builtin_system as _m_builtin_system
from .expr_threading_sync import _sync_alloc_and_init as _m_sync_alloc_and_init
from .expr_threading_sync import _sync_call_void as _m_sync_call_void
from .expr_threading_sync import _sync_get_free as _m_sync_get_free
from .expr_threading_tcp_connect import _addrinfo_layout as _m_addrinfo_layout
from .expr_threading_tcp_connect import _builtin_tcp_connect as _m_builtin_tcp_connect
from .expr_threading_tcp_connect import _emit_i32_store_at as _m_emit_i32_store_at
from .expr_threading_tcp_connect import _emit_load_at as _m_emit_load_at


class ExprBuiltinThreadingEmitter:
    """Threading/concurrency and low-level OS process/sync/socket builtins."""

    def __init__(self, exprgen: Any) -> None:
        self._e = exprgen

    def __getattr__(self, name: str) -> Any:
        return getattr(self._e, name)

    visit_Spawn = _m_visit_Spawn
    _spawn_windows = _m_spawn_windows
    _spawn_pthread = _m_spawn_pthread
    visit_Join = _m_visit_Join
    _join_windows = _m_join_windows
    _join_pthread = _m_join_pthread
    visit_Await = _m_visit_Await
    visit_AtomicOp = _m_visit_AtomicOp
    _get_variable_ptr = _m_get_variable_ptr
    _builtin_atomic_load = _m_builtin_atomic_load
    _builtin_atomic_store = _m_builtin_atomic_store
    _builtin_atomic_add = _m_builtin_atomic_add
    _builtin_atomic_sub = _m_builtin_atomic_sub
    _builtin_atomic_exchange = _m_builtin_atomic_exchange
    _builtin_atomic_cmpxchg = _m_builtin_atomic_cmpxchg
    _builtin_thread_id = _m_builtin_thread_id
    _builtin_num_cpus = _m_builtin_num_cpus
    _builtin_yield_thread = _m_builtin_yield_thread
    _builtin_sleep_ms = _m_builtin_sleep_ms

    _sync_alloc_and_init = _m_sync_alloc_and_init
    _sync_call_void = _m_sync_call_void
    _sync_get_free = _m_sync_get_free
    _builtin_mutex_create = _m_builtin_mutex_create
    _builtin_mutex_lock = _m_builtin_mutex_lock
    _builtin_mutex_unlock = _m_builtin_mutex_unlock
    _builtin_mutex_destroy = _m_builtin_mutex_destroy
    _builtin_cond_create = _m_builtin_cond_create
    _builtin_cond_wait = _m_builtin_cond_wait
    _builtin_cond_signal = _m_builtin_cond_signal
    _builtin_cond_broadcast = _m_builtin_cond_broadcast
    _builtin_cond_destroy = _m_builtin_cond_destroy
    _builtin_rwlock_create = _m_builtin_rwlock_create
    _builtin_rwlock_read_lock = _m_builtin_rwlock_read_lock
    _builtin_rwlock_write_lock = _m_builtin_rwlock_write_lock
    _builtin_rwlock_read_unlock = _m_builtin_rwlock_read_unlock
    _builtin_rwlock_write_unlock = _m_builtin_rwlock_write_unlock
    _builtin_rwlock_destroy = _m_builtin_rwlock_destroy
    _builtin_system = _m_builtin_system

    _socket_is_windows = _m_socket_is_windows
    _socket_handle_ty = _m_socket_handle_ty
    _socket_handle_to_i64 = _m_socket_handle_to_i64
    _socket_invalid_const = _m_socket_invalid_const
    _ensure_wsa_init = _m_ensure_wsa_init
    _emit_htons = _m_emit_htons
    _emit_libc_memset = _m_emit_libc_memset
    _addrinfo_layout = _m_addrinfo_layout
    _emit_i32_store_at = _m_emit_i32_store_at
    _emit_load_at = _m_emit_load_at
    _emit_socket_close_native = _m_emit_socket_close_native
    _emit_socket_nodelay = _m_emit_socket_nodelay
    _build_sockaddr_in = _m_build_sockaddr_in
    _builtin_tcp_connect = _m_builtin_tcp_connect
    _builtin_tcp_listen = _m_builtin_tcp_listen
    _builtin_tcp_accept = _m_builtin_tcp_accept
    _builtin_tcp_recv = _m_builtin_tcp_recv
    _builtin_tcp_send = _m_builtin_tcp_send
    _builtin_tcp_close = _m_builtin_tcp_close
