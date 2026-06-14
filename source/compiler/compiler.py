"""
AILang Compiler - High-level compilation functions
Main entry points for compiling and executing AILang programs
"""

import ctypes
from parser.ast import Function
from parser.parser import Parser

from codegen.codegen import CodeGen
from lexer.scan import tokenize
from llvmlite import binding

# Initialize LLVM native target
binding.initialize_native_target()
binding.initialize_native_asmprinter()


def optimize_ir(llvm_ir: str, opt_level: int = 2) -> str:
    """
    Apply LLVM optimization passes to IR using llvmlite's PassBuilder.

    Args:
        llvm_ir: LLVM IR string
        opt_level: 0-3 (0=none, 1=basic, 2=default, 3=aggressive)

    Returns:
        Optimized LLVM IR string
    """
    if opt_level == 0:
        return llvm_ir

    # Parse the IR
    llvm_module = binding.parse_assembly(llvm_ir)
    llvm_module.verify()

    # Create target machine
    target = binding.Target.from_default_triple()
    cpu_name = binding.get_host_cpu_name()
    features = binding.get_host_cpu_features().flatten()
    target_machine = target.create_target_machine(
        cpu=cpu_name, features=features, opt=opt_level
    )

    # Pre-pass: Run SROA on each function to promote allocas to SSA registers
    # This MUST happen before inlining to avoid stacksave/stackrestore overhead
    # when functions with stack allocations are inlined into loops
    if opt_level > 0:
        fpm = binding.create_new_function_pass_manager()
        fpm.add_sroa_pass()  # Promote allocas to SSA registers
        fpm.add_instruction_combine_pass()  # Clean up redundant instructions
        pto_pre = binding.PipelineTuningOptions(speed_level=opt_level)
        pb_pre = binding.create_pass_builder(target_machine, pto_pre)
        for func in llvm_module.functions:
            if not func.is_declaration:
                fpm.run(func, pb_pre)

    # Main pass: Full module optimization (includes inlining, vectorization, etc.)
    pto = binding.PipelineTuningOptions(speed_level=opt_level)
    pb = binding.create_pass_builder(target_machine, pto)
    mpm = pb.getModulePassManager()
    mpm.run(llvm_module, pb)

    return str(llvm_module)


def compile_ail_file(filename: str, optimize: bool = True, opt_level: int = 2) -> str:
    """
    Compile .ail file to .ll and JIT execute if it has main()

    Args:
        filename: Path to .ail source file

    Returns:
        Path to generated .ll file
    """
    print(f"=== Compiling {filename} ===\n")

    # Read source with UTF-8 encoding
    with open(filename, "r", encoding="utf-8") as f:
        source_code = f.read()

    print("Source code:")
    print(source_code)
    print()

    # Tokenize
    print("--- Tokenizing ---")
    tokens = tokenize(source_code)
    print(f"Tokens: {len(tokens)}")

    # Parse
    print("\n--- Parsing ---")
    parser = Parser(tokens)
    ast = parser.parse_program()
    func_names = [f.name for f in ast if isinstance(f, Function)]
    print(f"Functions: {func_names}")

    # Generate IR
    print("\n--- Generating LLVM IR ---")
    codegen = CodeGen()
    ir_code = codegen.generate(ast)

    # Optimize if requested
    if optimize:
        print(f"\n--- Optimizing (Level {opt_level}) ---")
        ir_code = optimize_ir(ir_code, opt_level)

    print(ir_code)

    # Save to .ll file
    output_file = filename.replace(".ail", ".ll")
    with open(output_file, "w") as f:
        f.write(ir_code)

    print(f"\n[Saved to {output_file}]")

    # JIT compile and execute if it has main()
    has_main = any(isinstance(f, Function) and f.name == "main" for f in ast)
    if has_main:
        print("\n--- JIT Execution ---")
        binding.initialize_native_target()
        binding.initialize_native_asmprinter()

        llvm_module = binding.parse_assembly(ir_code)
        llvm_module.verify()

        target_machine = binding.Target.from_default_triple().create_target_machine()
        engine = binding.create_mcjit_compiler(llvm_module, target_machine)

        func_ptr = engine.get_function_address("main")
        cfunc = ctypes.CFUNCTYPE(ctypes.c_int64)(func_ptr)

        result = cfunc()
        print(f"Program returned: {result}")

    return output_file


def compile_to_ir(source_code: str) -> str:
    """
    Compile AILang source code to LLVM IR string

    Args:
        source_code: AILang source code string

    Returns:
        LLVM IR code as string
    """
    # Tokenize
    tokens = tokenize(source_code)

    # Parse
    parser = Parser(tokens)
    ast = parser.parse_program()

    # Generate IR
    codegen = CodeGen()
    ir_code = codegen.generate(ast)

    return ir_code


def jit_execute(source_code: str, optimize: bool = True, opt_level: int = 2) -> int:
    """
    JIT compile and execute AILang source code

    Args:
        source_code: AILang source code string
        optimize: Whether to apply optimizations
        opt_level: Optimization level 0-3

    Returns:
        Exit code from main() function
    """
    # Compile to IR
    ir_code = compile_to_ir(source_code)

    # Optimize if requested
    if optimize:
        ir_code = optimize_ir(ir_code, opt_level)

    # JIT execute
    binding.initialize_native_target()
    binding.initialize_native_asmprinter()

    llvm_module = binding.parse_assembly(ir_code)
    llvm_module.verify()

    target_machine = binding.Target.from_default_triple().create_target_machine()
    engine = binding.create_mcjit_compiler(llvm_module, target_machine)

    func_ptr = engine.get_function_address("main")
    cfunc = ctypes.CFUNCTYPE(ctypes.c_int64)(func_ptr)

    return int(cfunc())
