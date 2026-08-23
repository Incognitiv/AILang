"""
AILang REPL - Interactive mode
"""

import re

from codegen.fast_jit import compile_to_ir_fast, fast_jit_compile
from version import __version__


class REPL:
    def __init__(self):
        self.history = []
        self.definitions: list[str] = []
        self.block_depth = 0

    def run(self):
        print(f"AILang REPL v{__version__}")
        print("Type 'exit' or 'quit' to leave, 'help' for commands")
        print("-" * 40)

        buffer: list[str] = []

        while True:
            try:
                prompt = "... " if self.block_depth > 0 else ">>> "
                line = input(prompt)
                stripped = line.strip()

                if self.block_depth == 0:
                    if stripped in ("exit", "quit"):
                        print("Goodbye!")
                        break
                    if stripped == "help":
                        self.show_help()
                        continue
                    if stripped == "clear":
                        self.definitions.clear()
                        self.history.clear()
                        print("State cleared.")
                        continue

                buffer.append(line)
                self._update_block_depth(stripped)

                if self.block_depth > 0:
                    continue

                snippet = "\n".join(buffer).strip()
                buffer.clear()
                if not snippet:
                    continue

                self.execute(snippet)
                self.history.append(snippet)

            except KeyboardInterrupt:
                print("\nInterrupted. Type 'exit' to quit.")
                buffer.clear()
                self.block_depth = 0
            except EOFError:
                print("\nGoodbye!")
                break

    def _update_block_depth(self, stripped: str) -> None:
        openers = (
            "def ",
            "if ",
            "while ",
            "for ",
            "foreach ",
            "loop",
            "repeat ",
            "match ",
            "try",
            "record ",
            "enum ",
        )
        if any(stripped.startswith(kw) for kw in openers) or stripped.endswith("then"):
            self.block_depth += 1
        if stripped == "end":
            self.block_depth = max(0, self.block_depth - 1)

    def execute(self, code: str) -> None:
        lines = code.split("\n")
        first_line = lines[0].lstrip()

        if first_line.startswith(("def ", "record ", "enum ")):
            try:
                test_program = "\n".join([*self.definitions, code])
                compile_to_ir_fast(test_program)
                self.definitions.append(code)
                name_match = re.match(r"(def|record|enum)\s+(\w+)", first_line)
                label = name_match.group(2) if name_match else "definition"
                print(f"Defined: {label}")
            except SyntaxError as e:
                print(f"Syntax Error: {e}")
            except (TypeError, ValueError, RuntimeError, OSError) as e:
                print(f"Error: {type(e).__name__}: {e}")
            return

        try:
            program_parts = []
            if self.definitions:
                program_parts.append("\n".join(self.definitions))

            # M21 fix: Detect if input is a pure expression and print its result
            # Simple heuristic: if it's a single line without keywords, it's an expression
            is_expr = len(lines) == 1 and not any(
                lines[0].strip().startswith(kw)
                for kw in (
                    "print",
                    "if ",
                    "while ",
                    "for ",
                    "foreach ",
                    "return ",
                    "let ",
                    "var ",
                    "const ",
                )
            )

            if is_expr and lines[0].strip():
                # Wrap expression in print() to show result
                expr = lines[0].strip()
                main_body = f"    print({expr})"
            else:
                main_body = "\n    ".join(lines)

            program_parts.append(f"def main():\n    {main_body}\n    return 0\nend")
            program = "\n\n".join(program_parts)

            result = fast_jit_compile(program, optimize=False)
            if not is_expr:
                print(f"=> {result}")
        except SyntaxError as e:
            print(f"Syntax Error: {e}")
        except (TypeError, ValueError, RuntimeError, OSError) as e:
            print(f"Error: {type(e).__name__}: {e}")

    def show_help(self) -> None:
        print(
            """
AILang REPL Commands:
  exit, quit  - Exit the REPL
  help        - Show this help
  clear       - Reset REPL state

Syntax:
  print "Hello"            # statement form
  print("Hi " + name)      # call form

Define a function:
  def greet(name):
      print "Hi " + name
      return 0
  end

Then call it:
  greet("World")
"""
        )


if __name__ == "__main__":
    repl = REPL()
    repl.run()
