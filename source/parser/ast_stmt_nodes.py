"""Statement and concurrency AST nodes."""

from __future__ import annotations

from typing import Optional

from .ast_base import ASTNode, ParsedType


class Return(ASTNode):
    def __init__(self, value: Optional[ASTNode]) -> None:
        self.value: Optional[ASTNode] = value


class Break(ASTNode): ...


class Continue(ASTNode): ...


class Assert(ASTNode):
    """Assert statement for testing.
    Syntax: assert condition [, message]
    If condition is false, prints error and exits with code 1.
    """

    def __init__(self, condition: ASTNode, message: Optional[ASTNode] = None) -> None:
        self.condition: ASTNode = condition
        self.message: Optional[ASTNode] = message  # Optional error message


class VarDecl(ASTNode):
    def __init__(
        self,
        type_name: ParsedType,
        var_name: str,
        init_value: ASTNode,
        is_const: bool = False,
        is_public: bool = False,
    ) -> None:
        self.type_name: ParsedType = type_name
        self.var_name: str = var_name
        self.init_value: ASTNode = init_value
        self.is_const: bool = is_const
        self.is_public: bool = is_public


class RangeType(ASTNode):
    """Range type constraint: 0..100 or low..high"""

    def __init__(self, low: ASTNode, high: ASTNode, exclusive: bool = False) -> None:
        self.low: ASTNode = low  # Lower bound (inclusive)
        self.high: ASTNode = high  # Upper bound (inclusive unless exclusive)
        self.exclusive: bool = exclusive  # True for 0...100 (excludes high)


class TypeAlias(ASTNode):
    """Type alias definition: type Percent = 0..100 or typedef int Count"""

    def __init__(self, name: str, target_type: ASTNode | ParsedType) -> None:
        self.name: str = name  # e.g., "Percent"
        self.target_type: ASTNode | ParsedType = target_type  # e.g., RangeType(0, 100)


class RangeVarDecl(ASTNode):
    """Range-constrained variable: x := 0..100 = 50"""

    def __init__(
        self,
        var_name: str,
        range_type: RangeType,
        init_value: Optional[ASTNode] = None,
    ) -> None:
        self.var_name: str = var_name
        self.range_type: RangeType = range_type
        self.init_value: Optional[ASTNode] = init_value  # Initial value (optional)


class Assign(ASTNode):
    def __init__(self, var_name: str, value: ASTNode) -> None:
        self.var_name: str = var_name
        self.value: ASTNode = value


class TupleAssign(ASTNode):
    """Tuple unpacking assignment: a, b = expr or a, b = b, a"""

    def __init__(self, var_names: list[str], values: list[ASTNode]) -> None:
        self.var_names: list[str] = var_names  # Target variable names
        self.values: list[ASTNode] = values  # Right-hand side expressions


# ============================================================================
# Control Flow
# ============================================================================
class If(ASTNode):
    def __init__(
        self, cond: ASTNode, then_body: list[ASTNode], else_body: list[ASTNode]
    ) -> None:
        self.cond: ASTNode = cond
        self.then_body: list[ASTNode] = then_body
        self.else_body: list[ASTNode] = else_body


class While(ASTNode):
    def __init__(
        self,
        cond: ASTNode,
        body: list[ASTNode],
        max_iterations: Optional[ASTNode] = None,
    ) -> None:
        self.cond: ASTNode = cond
        self.body: list[ASTNode] = body
        self.max_iterations: Optional[ASTNode] = max_iterations  # Bounded loop limit


class DoWhile(ASTNode):
    """Do-while loop: body executes at least once, then condition checked.
    Syntax: do then ... end while condition
    This generates LLVM-optimal loop structure (rotated form) without
    requiring the LoopRotate pass to insert guard checks.
    """

    def __init__(
        self,
        body: list[ASTNode],
        cond: ASTNode,
        max_iterations: Optional[ASTNode] = None,
    ) -> None:
        self.body: list[ASTNode] = body
        self.cond: ASTNode = cond
        self.max_iterations: Optional[ASTNode] = max_iterations


class For(ASTNode):
    def __init__(
        self,
        init: Optional[ASTNode],
        cond: ASTNode,
        step: Optional[ASTNode],
        body: list[ASTNode],
        max_iterations: Optional[ASTNode] = None,
    ) -> None:
        self.init: Optional[ASTNode] = init
        self.cond: ASTNode = cond
        self.step: Optional[ASTNode] = step
        self.body: list[ASTNode] = body
        self.max_iterations: Optional[ASTNode] = max_iterations


class Loop(ASTNode):
    def __init__(
        self, body: list[ASTNode], max_iterations: Optional[ASTNode] = None
    ) -> None:
        self.body: list[ASTNode] = body
        self.max_iterations: Optional[ASTNode] = max_iterations


class Foreach(ASTNode):
    def __init__(
        self,
        var_name: str,
        iterable: ASTNode,
        body: list[ASTNode],
        max_iterations: Optional[ASTNode] = None,
    ) -> None:
        self.var_name: str = var_name
        self.iterable: ASTNode = iterable
        self.body: list[ASTNode] = body
        self.max_iterations: Optional[ASTNode] = max_iterations


class Repeat(ASTNode):
    def __init__(self, count: ASTNode, body: list[ASTNode]) -> None:
        self.count: ASTNode = count
        self.body: list[ASTNode] = body


# ============================================================================
# Concurrency/Threading
# ============================================================================
class Spawn(ASTNode):
    """Spawn a new thread to execute a function call.
    Syntax: spawn function_name(args)
    Returns: A thread handle that can be joined later.
    Example:
        handle = spawn compute_heavy(1000)
        result = join(handle)
    """

    def __init__(self, func_call: ASTNode) -> None:
        self.func_call: ASTNode = (
            func_call  # The function call to execute in new thread
        )


class Join(ASTNode):
    """Wait for a spawned thread to complete and get its result.
    Syntax: join(handle)
    Returns: The return value of the spawned function.
    """

    def __init__(self, handle: ASTNode) -> None:
        self.handle: ASTNode = handle  # The thread handle from spawn


class Await(ASTNode):
    """Await an async operation to complete.
    Syntax: await async_call()
    Returns: The result of the async operation.
    Example:
        result = await fetch_data(url)
        data = await parse_json(result)
    """

    def __init__(self, expr: ASTNode) -> None:
        self.expr: ASTNode = expr  # The async expression to await


class AtomicOp(ASTNode):
    """Atomic operation for lock-free programming.
    Syntax:
        atomic_load(ptr)
        atomic_store(ptr, value)
        atomic_add(ptr, value)
        atomic_sub(ptr, value)
        atomic_exchange(ptr, value)
        atomic_compare_exchange(ptr, expected, desired)
    """

    def __init__(
        self,
        op: str,
        ptr: ASTNode,
        value: Optional[ASTNode] = None,
        expected: Optional[ASTNode] = None,
    ) -> None:
        self.op: str = op  # "load", "store", "add", "sub", "exchange", "cmpxchg"
        self.ptr: ASTNode = ptr
        self.value: Optional[ASTNode] = value
        self.expected: Optional[ASTNode] = expected


class ChannelCreate(ASTNode):
    """Create a new channel for message passing.
    Syntax: channel(type, capacity)
    Example:
        ch = channel(int, 10)  // Buffered channel with capacity 10
        ch = channel(int, 0)   // Unbuffered (synchronous) channel
    """

    def __init__(self, elem_type: str, capacity: ASTNode) -> None:
        self.elem_type: str = elem_type
        self.capacity: ASTNode = capacity


class ChannelSend(ASTNode):
    """Send a value to a channel.
    Syntax: channel_send(ch, value) or ch <- value
    Blocks if channel is full (bounded) or no receiver (unbuffered).
    """

    def __init__(self, channel: ASTNode, value: ASTNode) -> None:
        self.channel: ASTNode = channel
        self.value: ASTNode = value


class ChannelRecv(ASTNode):
    """Receive a value from a channel.
    Syntax: channel_recv(ch) or <- ch
    Blocks if channel is empty.
    """

    def __init__(self, channel: ASTNode) -> None:
        self.channel: ASTNode = channel


class ChannelTrySend(ASTNode):
    """Try to send without blocking. Returns true if sent."""

    def __init__(self, channel: ASTNode, value: ASTNode) -> None:
        self.channel: ASTNode = channel
        self.value: ASTNode = value


class ChannelTryRecv(ASTNode):
    """Try to receive without blocking. Returns (value, success)."""

    def __init__(self, channel: ASTNode) -> None:
        self.channel: ASTNode = channel


class ChannelClose(ASTNode):
    """Close a channel (no more sends allowed)."""

    def __init__(self, channel: ASTNode) -> None:
        self.channel: ASTNode = channel


class TryExcept(ASTNode):
    """
    Try/catch/except/finally statement
    Syntax:
      try [expr] then
          body
      catch ErrorType then
          handler
      catch AnotherType then
          handler2
      except error_var then
          generic_handler
      finally then
          cleanup
      end
    """

    def __init__(
        self,
        try_expr: Optional[ASTNode],
        try_body: list[ASTNode],
        catch_blocks: list[tuple[str, Optional[str], list[ASTNode]]],
        except_block: Optional[tuple[str, list[ASTNode]]],
        finally_block: Optional[list[ASTNode]],
    ) -> None:
        self.try_expr: Optional[ASTNode] = try_expr  # Optional expression to try
        self.try_body: list[ASTNode] = try_body  # Statements in try block
        self.catch_blocks: list[tuple[str, Optional[str], list[ASTNode]]] = (
            catch_blocks  # [(error_type, var_name, body), ...]
        )
        self.except_block: Optional[tuple[str, list[ASTNode]]] = (
            except_block  # (var_name, body) or None
        )
        self.finally_block: Optional[list[ASTNode]] = (
            finally_block  # Statements or None
        )


class Throw(ASTNode):
    """Throw an exception: throw "message" or throw ErrorType("msg")"""

    def __init__(
        self,
        error_type: Optional[str],
        message: Optional[ASTNode],
    ) -> None:
        self.error_type: Optional[str] = error_type
        self.message: Optional[ASTNode] = message


# ============================================================================
# Functions
# ============================================================================
