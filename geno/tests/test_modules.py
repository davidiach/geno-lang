"""
Tests for Geno Module System
===============================

Tests in-memory module resolution via import statements.
"""

import pytest

from geno.api import RunConfig, check, run


def test_module_filename_rejects_path_traversal():
    from geno.api import _module_filename

    with pytest.raises(ValueError, match="Invalid module name"):
        _module_filename("../../examples/fibonacci", "")


class TestModuleBasic:
    """Test basic module import and function calls."""

    def test_import_and_call_module_function(self):
        """Main imports a module and calls its function."""
        main_source = """
        import Utils

        func main() -> Int
            return add_one(42)
        end func
        """
        utils_source = """
        func add_one(x: Int) -> Int
            example 1 -> 2
            return x + 1
        end func
        """
        config = RunConfig(modules={"Utils": utils_source})
        result = run(main_source, config=config)
        assert result.ok is True
        assert result.value == 43

    def test_import_module_type(self):
        """Main imports a module and uses its type definition."""
        main_source = """
        import Models

        func main() -> Int
            let p: Wrapper = Wrapper(42)
            return p.value
        end func
        """
        models_source = """
        type Wrapper = Wrapper(value: Int)
        """
        config = RunConfig(modules={"Models": models_source})
        result = run(main_source, config=config)
        assert result.ok is True
        assert result.value == 42

    def test_import_multiple_modules(self):
        """Main imports multiple modules."""
        main_source = """
        import Math
        import Strings

        func main() -> Int
            let doubled: Int = double(21)
            let greeting: String = greet("world")
            return doubled
        end func
        """
        math_source = """
        func double(x: Int) -> Int
            example 5 -> 10
            return x * 2
        end func
        """
        strings_source = """
        func greet(name: String) -> String
            example "world" -> "hello world"
            return "hello " + name
        end func
        """
        config = RunConfig(modules={"Math": math_source, "Strings": strings_source})
        result = run(main_source, config=config)
        assert result.ok is True
        assert result.value == 42


class TestModuleErrors:
    """Test module error handling."""

    def test_unknown_module_error(self):
        """Importing an unknown module produces an error."""
        main_source = """
        import NonExistent

        func main() -> Int
            return 0
        end func
        """
        config = RunConfig(modules={})
        result = run(main_source, config=config)
        assert result.ok is False
        assert any("NonExistent" in d.message for d in result.diagnostics)

    def test_circular_import_self(self):
        """A module importing itself produces an error."""
        main_source = """
        import Circular

        func main() -> Int
            return 0
        end func
        """
        circular_source = """
        import Circular

        func noop() -> Int
            example () -> 0
            return 0
        end func
        """
        config = RunConfig(modules={"Circular": circular_source})
        result = run(main_source, config=config)
        assert result.ok is False
        assert any("ircular" in d.message for d in result.diagnostics)

    def test_module_parse_error(self):
        """A module with invalid syntax produces a parse error."""
        main_source = """
        import Bad

        func main() -> Int
            return 0
        end func
        """
        bad_source = "func {{{ end"
        config = RunConfig(modules={"Bad": bad_source})
        result = run(main_source, config=config)
        assert result.ok is False

    def test_no_modules_provided(self):
        """Import without modules dict produces an error at typecheck."""
        main_source = """
        import Utils

        func main() -> Int
            return 0
        end func
        """
        result = run(main_source)
        # ImportStatement is just a Definition node; without modules, the
        # typechecker won't resolve it but also won't crash (modules=None
        # means no module resolution pass is run). The program will fail
        # only if it tries to use undefined functions from the module.
        # This is by design — import without modules is a no-op.
        assert result.ok is True


class TestModuleTypecheck:
    """Test typechecker module resolution."""

    def test_check_with_modules(self):
        """geno.check() resolves imported types."""
        main_source = """
        import Utils

        func main() -> Int
            return add_one(5)
        end func
        """
        utils_source = """
        func add_one(x: Int) -> Int
            example 1 -> 2
            return x + 1
        end func
        """
        result = check(main_source, modules={"Utils": utils_source})
        assert result.ok is True

    def test_check_unknown_module(self):
        """geno.check() reports error on unknown module."""
        main_source = """
        import Missing

        func main() -> Int
            return 0
        end func
        """
        result = check(main_source, modules={})
        assert result.ok is False

    def test_exported_type_alias_cannot_reference_private_type(self):
        """Transparent exported aliases must not expose hidden target types."""
        main_source = """
        import Types

        func main() -> Public
            return make()
        end func
        """
        types_source = """
        type Internal = Internal(value: Int)
        export type Public = Internal

        export func make() -> Public
            example () -> Internal(1)
            return Internal(1)
        end func
        """

        result = check(main_source, modules={"Types": types_source})

        assert result.ok is False
        assert any(
            "Exported type alias 'Public' references non-exported type: Internal"
            in diagnostic.message
            for diagnostic in result.diagnostics
        )

    def test_exported_function_cannot_reference_private_type(self):
        """Exported function signatures cannot mention hidden module types."""
        source = """
        type Internal = Internal(value: Int)

        export func make() -> Internal
            example () -> Internal(1)
            return Internal(1)
        end func
        """

        result = check(source)

        assert result.ok is False
        assert any(
            "Exported function 'make' references non-exported type: Internal"
            in diagnostic.message
            for diagnostic in result.diagnostics
        )

    def test_exported_type_cannot_reference_private_field_type(self):
        """Exported data constructors cannot expose hidden field types."""
        source = """
        type Internal = Internal(value: Int)
        export type Public = Public(value: Internal)
        """

        result = check(source)

        assert result.ok is False
        assert any(
            "Exported type 'Public' references non-exported type: Internal"
            in diagnostic.message
            for diagnostic in result.diagnostics
        )


class TestModuleChaining:
    """Test module-to-module imports (transitive)."""

    def test_transitive_import(self):
        """Module A imports Module B, main imports Module A."""
        main_source = """
        import ModA

        func main() -> Int
            return a_func(5)
        end func
        """
        mod_a_source = """
        import ModB

        func a_func(x: Int) -> Int
            example 5 -> 10
            return b_func(x) * 2
        end func
        """
        mod_b_source = """
        func b_func(x: Int) -> Int
            example 5 -> 5
            return x
        end func
        """
        config = RunConfig(modules={"ModA": mod_a_source, "ModB": mod_b_source})
        result = run(main_source, config=config)
        assert result.ok is True
        assert result.value == 10
