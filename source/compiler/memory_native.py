"""LLVM object generation without materializing IR or object files."""

from __future__ import annotations

from typing import Any

from llvmlite import binding


def compile_ir_object(llvm_ir: str, opt_level: int = 3) -> bytes:
    """Verify and optimize one native module, returning its object in RAM."""
    if opt_level not in range(4):
        raise ValueError("optimization level must be between 0 and 3")
    binding.initialize_native_target()
    binding.initialize_native_asmprinter()
    target = binding.Target.from_default_triple()
    with target.create_target_machine(
        cpu=binding.get_host_cpu_name(),
        features=binding.get_host_cpu_features().flatten(),
        opt=opt_level,
        reloc="pic",
    ) as machine, binding.parse_assembly(llvm_ir) as module:
        module.verify()
        if module.triple and module.triple != binding.get_default_triple():
            raise ValueError("memory object emission supports the native target only")
        module.triple = binding.get_default_triple()
        module.data_layout = str(machine.target_data)
        if opt_level:
            _optimize_module(module, machine, opt_level)
        module.verify()
        return bytes(machine.emit_object(module))


def _optimize_module(module: Any, machine: Any, opt_level: int) -> None:
    """Promote locals before the normal optimization pipeline, as in JIT."""
    with binding.PipelineTuningOptions(speed_level=opt_level) as tuning:
        with binding.create_pass_builder(machine, tuning) as builder:
            _promote_allocas(module, builder)
            with builder.getModulePassManager() as passes:
                passes.run(module, builder)


def _promote_allocas(module: Any, builder: Any) -> None:
    with binding.create_new_function_pass_manager() as functions:
        functions.add_sroa_pass()
        functions.add_instruction_combine_pass()
        for function in module.functions:
            if not function.is_declaration:
                functions.run(function, builder)
