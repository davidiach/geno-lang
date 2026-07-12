"""
Geno REPL
=========

Interactive read-eval-print loop for Geno.

Features:
- Multi-line input (detects unclosed blocks)
- Tab completion (functions, types, builtins)
- Persistent history (~/.geno_history)
- :type, :load, :ast, :clear commands
"""

import os
import sys
import traceback
from pathlib import Path
from typing import Any, Optional, cast

from . import __version__
from .ast_nodes import ReturnStatement
from .builtin_registry import DEFAULT_ALLOWED_CAPABILITIES, all_builtin_names
from .interpreter import Interpreter
from .interpreter import RuntimeError as GenoRuntimeError
from .lexer import Lexer, LexerError
from .parser import ParseError, Parser
from .typechecker import TypeChecker, TypeError

BANNER = f"""
╔═══════════════════════════════════════════════════════════════════╗
║                GENO - LLM-Native Programming Language             ║
║                        Version {__version__:<39s} ║
╠═══════════════════════════════════════════════════════════════════╣
║  Commands:                                                        ║
║    :help     - Show this help message                             ║
║    :load     - Load a file                                        ║
║    :type     - Show type of expression                            ║
║    :ast      - Show AST of expression                             ║
║    :clear    - Clear environment                                  ║
║    :quit     - Exit REPL                                          ║
╚═══════════════════════════════════════════════════════════════════╝
"""

HELP_TEXT = """
Geno REPL Commands:
  :help          Show this help message
  :load <file>   Load and execute a Geno source file
  :type <expr>   Show the type of an expression
  :ast <code>    Show the parsed AST
  :clear         Clear the environment (reset all definitions)
  :quit, :q      Exit the REPL

You can enter:
  - Function definitions: func foo(x: Int) -> Int ... end func
  - Type definitions: type Option[T] = Some(value: T) | None
  - Expressions: 1 + 2, quicksort([3, 1, 2])
  - Statements: let x: Int = 5

Multi-line input:
  End a line with \\ to continue on the next line.
  Or use triple-quoted strings for multi-line text.
"""


class REPL:
    """Interactive REPL for Geno.

    The REPL runs in local/trusted mode.  A 30-second sandbox timeout is
    applied as UX protection against accidental infinite loops, not as a
    security boundary.
    """

    _HISTORY_FILE = Path.home() / ".geno_history"
    _HISTORY_SIZE = 1000

    # Block-opening keywords that require a matching "end".  ``type`` is
    # *not* included — Geno type declarations are single-expression
    # ``type Foo = ...`` with optional ``|`` variant continuation lines
    # and never use ``end type``.  Including it made the REPL swallow
    # one-line aliases by waiting forever for an ``end`` that would
    # never come (F-0024 in #663).
    _BLOCK_OPENERS = {"func", "if", "while", "for", "match", "trait", "impl"}

    def __init__(self):
        from .sandbox import SandboxConfig

        # Timeout protects against accidental infinite loops in interactive use.
        # The REPL is a local/trusted surface -- it is NOT a production sandbox.
        self.interpreter = Interpreter(
            check_examples=False,
            sandbox_config=SandboxConfig(timeout=30.0),
            capabilities=DEFAULT_ALLOWED_CAPABILITIES,
        )
        self.type_checker = TypeChecker()
        self.running = True
        self.history: list[str] = []
        self.accumulated_source: list[str] = []
        self._setup_readline()

    def _setup_readline(self) -> None:
        """Configure readline for tab completion and persistent history."""
        try:
            import readline as readline_module
        except ImportError:
            return
        readline = cast(Any, readline_module)

        # Tab completion
        readline.set_completer(self._complete)
        readline.parse_and_bind("tab: complete")
        readline.set_completer_delims(" \t\n(,)=:")

        # Load history
        try:
            if self._HISTORY_FILE.exists():
                readline.read_history_file(str(self._HISTORY_FILE))
        except OSError:
            pass
        readline.set_history_length(self._HISTORY_SIZE)

    def _save_history(self) -> None:
        """Save readline history to disk."""
        try:
            import readline as readline_module

            readline = cast(Any, readline_module)
            readline.write_history_file(str(self._HISTORY_FILE))
        except (ImportError, OSError):
            pass

    def _complete(self, text: str, state: int) -> str | None:
        """Tab completion callback for readline."""
        if state == 0:
            self._completions = self._get_completions(text)
        if state < len(self._completions):
            return self._completions[state]
        return None

    _completions: list[str] = []

    def _get_completions(self, prefix: str) -> list[str]:
        """Generate completions matching prefix."""
        candidates: set[str] = set(all_builtin_names())

        # Keywords
        candidates.update(REPL._BLOCK_OPENERS)
        for kw in (
            "let",
            "var",
            "return",
            "end",
            "example",
            "import",
            "true",
            "false",
            "and",
            "or",
            "not",
            "do",
            "then",
            "else",
            "in",
            "with",
            "async",
            "await",
            "try",
            "catch",
            "throw",
            "break",
            "continue",
        ):
            candidates.add(kw)

        # User-defined names from interpreter
        candidates.update(self.interpreter.functions.keys())

        # User-defined types and variant constructors
        for type_name, type_def in self.interpreter.type_defs.items():
            candidates.add(type_name)
            for variant in type_def.variants:
                candidates.add(variant.name)

        # REPL commands
        for cmd in (":help", ":load", ":type", ":ast", ":clear", ":quit", ":q"):
            candidates.add(cmd)

        return sorted(c for c in candidates if c.startswith(prefix))

    def run(self) -> None:
        """Run the REPL loop."""
        print(BANNER)

        try:
            while self.running:
                try:
                    line = self._read_input()
                    if line is None:
                        continue

                    self._process_input(line)

                except KeyboardInterrupt:
                    self.accumulated_source = []
                    print("\nInterrupted. Type :quit to exit.")
                except EOFError:
                    print("\nGoodbye!")
                    break
        finally:
            self._save_history()

    def _read_input(self) -> str | None:
        """Read input from the user, handling multi-line.

        Detects unclosed blocks (func/if/while/for/type/match without
        matching end) and continues reading lines until balanced.
        """
        try:
            prompt = ">>> " if not self.accumulated_source else "... "
            line = input(prompt)

            # Handle explicit line continuation
            if line.endswith("\\"):
                self.accumulated_source.append(line[:-1])
                return None

            self.accumulated_source.append(line)

            # Check if blocks are balanced
            full = "\n".join(self.accumulated_source)
            if self._has_unclosed_block(full):
                return None

            self.accumulated_source = []
            return full

        except EOFError:
            raise

    @staticmethod
    def _strip_comment(line: str) -> str:
        """Remove the // line-comment portion that is not inside a string."""
        in_str = False
        quote_char = ""
        i = 0
        while i < len(line):
            ch = line[i]
            if in_str:
                if ch == "\\" and i + 1 < len(line):
                    i += 2  # skip escaped character
                    continue
                if ch == quote_char:
                    in_str = False
            else:
                if ch == '"' or ch == "'":
                    in_str = True
                    quote_char = ch
                elif ch == "/" and i + 1 < len(line) and line[i + 1] == "/":
                    return line[:i]
            i += 1
        return line

    @staticmethod
    def _strip_triple_quotes(line: str, in_triple: bool) -> tuple:
        """Strip triple-quoted content from *line*.

        Returns ``(code_outside_strings, still_in_triple)`` where
        *code_outside_strings* contains only the portions of the line
        that are outside triple-quoted regions.
        """
        result: list[str] = []
        i = 0
        while i < len(line):
            pos = line.find('"""', i)
            if in_triple:
                if pos == -1:
                    break  # rest of line is inside triple-quote
                in_triple = False
                i = pos + 3
            else:
                if pos == -1:
                    result.append(line[i:])
                    break
                result.append(line[i:pos])
                in_triple = True
                i = pos + 3
        return "".join(result), in_triple

    @staticmethod
    def _has_unclosed_block(source: str) -> bool:
        """Return True if source has more block openers than 'end' closers."""
        depth = 0
        in_triple_quote = False
        for line in source.splitlines():
            # Strip line comments before any further analysis
            code = REPL._strip_comment(line)

            # Remove triple-quoted content, tracking state across lines
            code, in_triple_quote = REPL._strip_triple_quotes(code, in_triple_quote)

            stripped = code.strip()
            # Skip empty lines
            if not stripped:
                continue
            first_word = stripped.split()[0] if stripped.split() else ""
            classification_line = stripped
            if first_word == "export":
                classification_line = stripped[len("export") :].lstrip()
                first_word = (
                    classification_line.split()[0]
                    if classification_line.split()
                    else ""
                )

            if (
                first_word == "async" and classification_line.startswith("async func")
            ) or first_word in REPL._BLOCK_OPENERS:
                depth += 1
            elif first_word == "end":
                depth -= 1
        return depth > 0 or in_triple_quote

    def _process_input(self, line: str) -> None:
        """Process a line of input."""
        line = line.strip()

        if not line:
            return

        # Handle commands
        if line.startswith(":"):
            self._handle_command(line)
            return

        # Try to parse and execute
        self._execute(line)

    def _handle_command(self, line: str) -> None:
        """Handle a REPL command."""
        parts = line.split(None, 1)
        command = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        if command in (":quit", ":q"):
            print("Goodbye!")
            self.running = False

        elif command == ":help":
            print(HELP_TEXT)

        elif command == ":clear":
            from .sandbox import SandboxConfig

            self.interpreter = Interpreter(
                check_examples=False,
                sandbox_config=SandboxConfig(timeout=30.0),
                capabilities=DEFAULT_ALLOWED_CAPABILITIES,
            )
            self.type_checker = TypeChecker()
            print("Environment cleared.")

        elif command == ":load":
            self._load_file(arg)

        elif command == ":type":
            self._show_type(arg)

        elif command == ":ast":
            self._show_ast(arg)

        else:
            print(f"Unknown command: {command}")
            print("Type :help for available commands.")

    def _execute(self, source: str) -> None:
        """Execute source code."""
        # Each REPL input runs with a fresh execution budget. The interpreter's
        # step counter and cumulative output length persist for the lifetime of
        # the interpreter, and nothing resets them between inputs, so a
        # long-lived session would eventually fail every input with
        # StepLimitExceeded (or an output-limit error) — recoverable only via
        # :clear, which discards all definitions. Reset them per input so each
        # entry is bounded independently. (The wall-clock deadline is already
        # applied per run by Interpreter.run.)
        self.interpreter.steps = 0
        self.interpreter._output_length = 0
        self.interpreter.output_buffer.clear()
        try:
            # Try parsing as a program (definitions)
            lexer = Lexer(source, "<repl>")
            tokens = lexer.tokenize()
            parser = Parser(tokens)

            # Check if it looks like a definition
            if tokens and tokens[0].type.name in ("FUNC", "TYPE"):
                program = parser.parse_program()

                # Type check
                self.type_checker.check_program(program)

                # Execute
                result = self.interpreter.run(program)
                if result is not None:
                    print(f"=> {self.interpreter._format_value(result)}")
                else:
                    print("Defined.")

            else:
                # Try as expression
                result = self._eval_expression(source)
                if result is not None:
                    print(f"=> {self.interpreter._format_value(result)}")

            self.history.append(source)

        except (LexerError, ParseError) as e:
            print(f"Syntax Error: {e.message}")
            if hasattr(e, "location"):
                print(f"  at {e.location}")

        except TypeError as e:
            print(f"Type Error: {e.message}")
            if hasattr(e, "location"):
                print(f"  at {e.location}")

        except GenoRuntimeError as e:
            print(f"Runtime Error: {e.message}")
            if e.location:
                print(f"  at {e.location}")

        except Exception as e:
            print(f"Error: {e}")
            if "--debug" in sys.argv:
                traceback.print_exc()

    def _eval_expression(self, source: str) -> Any:
        """Evaluate a single expression.

        The ``-> Int`` return type and example are parser-required placeholders;
        this path skips type checking so the annotation is never enforced.
        """
        wrapped = f"""
func __repl_eval__() -> Int
    example () -> 0
    return {source}
end func __repl_eval__
"""
        lexer = Lexer(wrapped, "<repl>")
        tokens = lexer.tokenize()
        parser = Parser(tokens)
        program = parser.parse_program()

        # Execute
        result = self.interpreter.run(program)

        # Call the eval function
        from .values import _UNBOUND

        func = self.interpreter.global_env.lookup("__repl_eval__")
        if func is not _UNBOUND:
            return self.interpreter._call_function(func, [])
        return result

    def _load_file(self, filename: str) -> None:
        """Load and execute a file."""
        if not filename:
            print("Usage: :load <filename>")
            return

        try:
            with open(filename, encoding="utf-8") as f:
                source = f.read()

            print(f"Loading {filename}...")

            lexer = Lexer(source, filename)
            tokens = lexer.tokenize()
            parser = Parser(tokens)
            program = parser.parse_program()

            self.type_checker.check_program(program)
            result = self.interpreter.run(program)

            print(f"Loaded {len(program.definitions)} definitions.")
            if result is not None:
                print(f"=> {self.interpreter._format_value(result)}")

        except FileNotFoundError:
            print(f"File not found: {filename}")
        except Exception as e:  # Boundary: diverse parse/typecheck/runtime errors
            print(f"Error loading file: {e}")

    def _show_type(self, source: str) -> None:
        """Show the type of an expression."""
        if not source:
            print("Usage: :type <expression>")
            return

        try:
            # Wrap in a dummy function so the parser can handle bare expressions.
            # The return type annotation is ignored — we only parse, then run
            # _check_expression on the extracted expression directly.
            wrapped = f"func __type_query__() -> Int\n    example () -> 0\n    return {source}\nend func __type_query__\n"
            lexer = Lexer(wrapped, "<repl>")
            tokens = lexer.tokenize()
            parser = Parser(tokens)
            program = parser.parse_program()

            # Extract the return expression from the dummy function
            func_def = program.definitions[0]
            assert hasattr(func_def, "body"), "Expected FunctionDef"
            for stmt in reversed(func_def.body):  # type: ignore[attr-defined]
                if isinstance(stmt, ReturnStatement):
                    expr = stmt.value
                    break
            else:
                print("Error: could not extract expression")
                return

            # Use the type checker's environment to infer the type
            inferred = self.type_checker._check_expression(
                expr, self.type_checker.global_env
            )
            print(f"{source} : {inferred}")

        except (LexerError, ParseError) as e:
            print(f"Syntax Error: {e.message}")
        except TypeError as e:
            print(f"Type Error: {e.message}")
        except Exception as e:
            print(f"Error: {e}")

    def _show_ast(self, source: str) -> None:
        """Show the AST of source code."""
        if not source:
            print("Usage: :ast <code>")
            return

        try:
            lexer = Lexer(source, "<repl>")
            tokens = lexer.tokenize()

            print("Tokens:")
            for tok in tokens:
                print(f"  {tok}")

            parser = Parser(tokens)
            if tokens[0].type.name in ("FUNC", "TYPE"):
                tree = parser.parse_program()
            else:
                # Wrap bare expression so the parser can handle it
                wrapped = f"func __ast_query__() -> Int\n    example () -> 0\n    return {source}\nend func __ast_query__\n"
                tree = Parser(Lexer(wrapped, "<repl>").tokenize()).parse_program()

            print("\nAST:")
            self._print_ast(tree, indent=0)

        except Exception as e:
            print(f"Error: {e}")

    def _print_ast(self, node, indent: int = 0) -> None:
        """Pretty print an AST node."""
        prefix = "  " * indent
        node_type = type(node).__name__

        if hasattr(node, "__dataclass_fields__"):
            print(f"{prefix}{node_type}:")
            for field_name in node.__dataclass_fields__:
                value = getattr(node, field_name)
                if field_name == "location":
                    continue
                print(f"{prefix}  {field_name}:", end="")
                if isinstance(value, list):
                    if value:
                        print()
                        for item in value:
                            self._print_ast(item, indent + 2)
                    else:
                        print(" []")
                elif hasattr(value, "__dataclass_fields__"):
                    print()
                    self._print_ast(value, indent + 2)
                else:
                    print(f" {value!r}")
        else:
            print(f"{prefix}{node!r}")


def run_repl() -> None:
    """Entry point for the REPL."""
    repl = REPL()
    repl.run()


if __name__ == "__main__":
    run_repl()
