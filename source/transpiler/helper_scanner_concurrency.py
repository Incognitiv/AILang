from __future__ import annotations

from parser import ast as A


def _scan_concurrency(self, node: A.ASTNode) -> None:
    """Threading / atomics / channels / inline asm fall through here."""
    if isinstance(node, A.Spawn):
        self._needs.threading = True
        # Record the target's parameter types so the runtime-emit phase
        # can generate a boxing struct + thunk. Skip if the target
        # isn't a Call (e.g. spawn of a method call -- not supported)
        # or isn't user-defined (built-ins, externs).
        if isinstance(node.func_call, A.Call):
            fname = node.func_call.name
            call_args = node.func_call.args or []
            if call_args and fname in self._functions:
                param_types, _ret = self._functions[fname]
                self._needs.spawn_targets[fname] = param_types
        self._scan_node(node.func_call)
        return
    if isinstance(node, A.Join):
        self._needs.threading = True
        self._scan_node(node.handle)
        return
    if isinstance(node, A.AtomicOp):
        self._needs.atomics = True
        self._scan_node(node.ptr)
        if node.value:
            self._scan_node(node.value)
        if node.expected:
            self._scan_node(node.expected)
        return
    self._scan_channel(node)


def _scan_channel(self, node: A.ASTNode) -> None:
    if isinstance(node, A.ChannelCreate):
        self._needs.channels = True
        self._scan_node(node.capacity)
        return
    if isinstance(node, A.ChannelSend):
        self._needs.channels = True
        self._scan_node(node.channel)
        self._scan_node(node.value)
        return
    if isinstance(node, A.ChannelRecv):
        self._needs.channels = True
        self._scan_node(node.channel)
        return
    if isinstance(node, A.ChannelTrySend):
        self._needs.channels = True
        self._scan_node(node.channel)
        self._scan_node(node.value)
        return
    if isinstance(node, A.ChannelTryRecv):
        self._needs.channels = True
        self._scan_node(node.channel)
        return
    if isinstance(node, A.ChannelClose):
        self._needs.channels = True
        self._scan_node(node.channel)
        return
    if isinstance(node, A.InlineAsm):
        self._needs.inline_asm = True
