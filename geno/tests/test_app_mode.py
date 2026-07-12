"""
Tests for app mode features: with expression, graphics/input builtins,
app lifecycle, array helpers, and the build command.
"""

import pytest

from geno.api import RunConfig, run
from geno.js_compiler import compile_to_html, compile_to_js
from geno.lexer import Lexer
from geno.parser import Parser
from geno.target_profile import TargetProfile
from geno.typechecker import TypeChecker, TypeError

# =============================================================================
# Helper
# =============================================================================


def typecheck(source: str) -> TypeChecker:
    """Parse and typecheck source code, returning the checker."""
    lexer = Lexer(source, "<test>")
    tokens = lexer.tokenize()
    parser = Parser(tokens)
    program = parser.parse_program()
    checker = TypeChecker()
    checker.check_program(program)
    return checker


def run_geno(source: str):
    """Run geno source code and return the result value."""
    result = run(source, config=RunConfig(timeout=5.0))
    assert result.ok, f"Run failed: {result.diagnostics}"
    return result.value


# =============================================================================
# With Expression
# =============================================================================


class TestWithExpression:
    """Tests for the `with` expression."""

    def test_with_single_field_update(self):
        """Update one field of a constructor value."""
        source = """
        type Point = Point(x: Int, y: Int)

        func move_right(p: Point) -> Point
            example Point(0, 0) -> Point(1, 0)
            return p with (x: p.x + 1)
        end func move_right

        func main() -> Point
            return move_right(Point(0, 0))
        end func main
        """
        result = run_geno(source)
        assert result == {"_constructor": "Point", "fields": {"x": 1, "y": 0}}

    def test_with_multiple_field_updates(self):
        """Update multiple fields at once."""
        source = """
        type Point = Point(x: Int, y: Int)

        func move(p: Point, dx: Int, dy: Int) -> Point
            example (Point(0, 0), 1, 2) -> Point(1, 2)
            return p with (x: p.x + dx, y: p.y + dy)
        end func move

        func main() -> Point
            return move(p: Point(3, 4), dx: 10, dy: 20)
        end func main
        """
        result = run_geno(source)
        assert result == {"_constructor": "Point", "fields": {"x": 13, "y": 24}}

    def test_with_preserves_unchanged_fields(self):
        """Fields not mentioned in `with` keep their values."""
        source = """
        type Rect = Rect(x: Int, y: Int, w: Int, h: Int)

        func set_size(r: Rect, new_w: Int, new_h: Int) -> Rect
            example (Rect(1, 2, 3, 4), 10, 20) -> Rect(1, 2, 10, 20)
            return r with (w: new_w, h: new_h)
        end func set_size

        func main() -> Rect
            return set_size(r: Rect(1, 2, 3, 4), new_w: 10, new_h: 20)
        end func main
        """
        result = run_geno(source)
        assert result == {
            "_constructor": "Rect",
            "fields": {"x": 1, "y": 2, "w": 10, "h": 20},
        }

    def test_with_type_error_wrong_field_type(self):
        """Type error when updating field with wrong type."""
        source = """
        type Point = Point(x: Int, y: Int)

        func bad(p: Point) -> Point
            example Point(0, 0) -> Point(0, 0)
            return p with (x: "hello")
        end func bad
        """
        with pytest.raises(TypeError):
            typecheck(source)

    def test_with_type_error_unknown_field(self):
        """Type error when updating non-existent field."""
        source = """
        type Point = Point(x: Int, y: Int)

        func bad(p: Point) -> Point
            example Point(0, 0) -> Point(0, 0)
            return p with (z: 5)
        end func bad
        """
        with pytest.raises(TypeError):
            typecheck(source)

    def test_with_does_not_conflict_with_match(self):
        """Ensure `with` in match-with doesn't trigger with-expression parsing."""
        source = """
        func check(opt: Option[Int]) -> Int
            example Some(5) -> 5
            match opt with
                | Some(x) -> return x
                | None -> return 0
            end match
        end func check

        func main() -> Int
            return check(Some(42))
        end func main
        """
        assert run_geno(source) == 42


# =============================================================================
# Graphics Builtins (type checking)
# =============================================================================


class TestGraphicsBuiltins:
    """Tests for graphics built-in function type signatures."""

    def test_draw_rect_typechecks(self):
        """draw_rect accepts correct types."""
        source = """
        func main() -> Unit
            draw_rect(x: 0, y: 0, width: 10, height: 10, color: "red")
            return ()
        end func main
        """
        typecheck(source)

    def test_clear_screen_typechecks(self):
        """clear_screen accepts a string color."""
        source = """
        func main() -> Unit
            clear_screen("#000000")
            return ()
        end func main
        """
        typecheck(source)

    def test_draw_text_typechecks(self):
        """draw_text accepts correct types."""
        source = """
        func main() -> Unit
            draw_text(text: "Score: 0", x: 10, y: 20, size: 16, color: "white")
            return ()
        end func main
        """
        typecheck(source)

    def test_screen_dimensions_return_int(self):
        """screen_width/screen_height return Int."""
        source = """
        func main() -> Int
            return screen_width() + screen_height()
        end func main
        """
        typecheck(source)

    def test_draw_rect_type_error(self):
        """draw_rect rejects wrong argument types."""
        source = """
        func main() -> Unit
            draw_rect(x: "bad", y: 0, width: 10, height: 10, color: "red")
            return ()
        end func main
        """
        with pytest.raises(TypeError):
            typecheck(source)


# =============================================================================
# Input Builtins
# =============================================================================


class TestInputBuiltins:
    """Tests for input built-in function type signatures."""

    def test_is_key_down_typechecks(self):
        """is_key_down accepts String, returns Bool."""
        source = """
        func main() -> Bool
            return is_key_down("ArrowLeft")
        end func main
        """
        typecheck(source)

    def test_is_key_pressed_typechecks(self):
        """is_key_pressed accepts String, returns Bool."""
        source = """
        func main() -> Bool
            return is_key_pressed("Space")
        end func main
        """
        typecheck(source)

    def test_is_key_down_returns_false_in_interpreter(self):
        """In interpreter mode, is_key_down always returns false."""
        source = """
        func main() -> Bool
            return is_key_down("ArrowLeft")
        end func main
        """
        assert run_geno(source) is False


# =============================================================================
# Array Helpers
# =============================================================================


class TestArrayHelpers:
    """Tests for array_fill and array_copy."""

    def test_array_fill(self):
        """array_fill sets all elements to a value."""
        source = """
        func main() -> List[Int]
            let arr: Array[Int] = array_new(3, 0)
            array_fill(arr, 42)
            return array_to_list(arr)
        end func main
        """
        assert run_geno(source) == [42, 42, 42]

    def test_array_copy_is_independent(self):
        """array_copy creates an independent copy."""
        source = """
        func main() -> List[Int]
            let arr: Array[Int] = array_new(3, 0)
            let copy: Array[Int] = array_copy(arr)
            array_set(array: copy, index: 0, value: 99)
            return array_to_list(arr)
        end func main
        """
        # Original should be unchanged
        assert run_geno(source) == [0, 0, 0]

    def test_array_copy_values(self):
        """array_copy preserves element values."""
        source = """
        func main() -> List[Int]
            let arr: Array[Int] = array_from_list([1, 2, 3])
            let copy: Array[Int] = array_copy(arr)
            return array_to_list(copy)
        end func main
        """
        assert run_geno(source) == [1, 2, 3]


# =============================================================================
# App Mode (Typechecker)
# =============================================================================


class TestAppModeTypechecker:
    """Tests for app mode lifecycle function support in the typechecker."""

    def test_init_update_render_no_examples_required(self):
        """init, update, render don't require example clauses."""
        source = """
        type State = State(x: Int)

        func init() -> State
            return State(0)
        end func init

        func update(state: State, dt: Float) -> State
            return state with (x: state.x + 1)
        end func update

        func render(state: State) -> Unit
            clear_screen("#000")
            draw_rect(x: state.x, y: 0, width: 10, height: 10, color: "red")
            return ()
        end func render
        """
        typecheck(source)

    def test_stale_init_model_contract_rejected_on_browser_target(self):
        """Legacy init_model browser lifecycle must fail clearly."""
        source = """
        type Model = Model(count: Int)

        func init_model() -> Model
            example () -> Model(0)
            return Model(0)
        end func init_model

        func update(model: Model, key: String) -> Model
            example Model(0), "up" -> Model(1)
            match key with
                | "up" -> return Model(model.count + 1)
                | _ -> return model
            end match
        end func update

        func render(model: Model) -> Unit
            return ()
        end func render
        """
        lexer = Lexer(source, "<test>")
        tokens = lexer.tokenize()
        parser = Parser(tokens)
        program = parser.parse_program()
        checker = TypeChecker(target_profile=TargetProfile.load("browser"))
        with pytest.raises(TypeError, match=r"init\(\).*init_model"):
            checker.check_program(program)

    def test_browser_update_requires_dt_float(self):
        """Browser app update lifecycle must accept dt: Float."""
        source = """
        type Model = Model(count: Int)

        func init() -> Model
            return Model(0)
        end func init

        func update(model: Model, key: String) -> Model
            example Model(0), "up" -> Model(1)
            return model
        end func update

        func render(model: Model) -> Unit
            return ()
        end func render
        """
        lexer = Lexer(source, "<test>")
        tokens = lexer.tokenize()
        parser = Parser(tokens)
        program = parser.parse_program()
        checker = TypeChecker(target_profile=TargetProfile.load("browser"))
        with pytest.raises(TypeError, match="dt: Float"):
            checker.check_program(program)

    def test_legacy_main_based_app_still_allowed(self):
        """Legacy main-based browser programs should still typecheck."""
        source = """
        type Model = Model(count: Int)

        @untested("legacy")
        func init_model() -> Model
            return Model(0)
        end func init_model

        func update(model: Model, key: String) -> Model
            example Model(0), "up" -> Model(1)
            return model
        end func update

        func view(model: Model) -> String
            example Model(0) -> "Count: 0"
            return "Count: 0"
        end func view

        @untested("entry point")
        func main() -> String
            return view(Model(0))
        end func main
        """
        lexer = Lexer(source, "<test>")
        tokens = lexer.tokenize()
        parser = Parser(tokens)
        program = parser.parse_program()
        checker = TypeChecker(target_profile=TargetProfile.load("browser"))
        checker.check_program(program)


# =============================================================================
# JS Compilation (App Mode)
# =============================================================================


class TestJSAppMode:
    """Tests for JS compilation in app mode."""

    def test_app_mode_detected(self):
        """JS compiler detects init/update/render and emits game loop."""
        source = """
        type State = State(x: Int)

        func init() -> State
            return State(0)
        end func init

        func update(state: State, dt: Float) -> State
            return state with (x: state.x + 1)
        end func update

        func render(state: State) -> Unit
            return ()
        end func render
        """
        js = compile_to_js(source)
        assert "requestAnimationFrame" in js
        assert "_geno_frame" in js
        assert "_geno_state = init()" in js

    def test_standard_mode_no_game_loop(self):
        """JS compiler does NOT emit game loop for standard programs."""
        source = """
        func main() -> Int
            return 42
        end func main
        """
        js = compile_to_js(source)
        assert "requestAnimationFrame" not in js
        assert "_main_result = main()" in js

    def test_compile_to_html(self):
        """compile_to_html produces valid HTML with canvas."""
        source = """
        type State = State(x: Int)

        func init() -> State
            return State(0)
        end func init

        func update(state: State, dt: Float) -> State
            return state
        end func update

        func render(state: State) -> Unit
            return ()
        end func render
        """
        html = compile_to_html(source, width=400, height=300, title="Test")
        assert "<!DOCTYPE html>" in html
        assert 'width="400"' in html
        assert 'height="300"' in html
        assert "<title>Test</title>" in html
        assert "geno-canvas" in html
        assert "requestAnimationFrame" in html

    def test_with_expr_compiles_to_js(self):
        """with expression compiles to a mutable object spread."""
        source = """
        type Point = Point(x: Int, y: Int)

        func move(p: Point) -> Point
            example Point(0, 0) -> Point(1, 0)
            return p with (x: p.x + 1)
        end func move

        func main() -> Point
            return move(Point(0, 0))
        end func main
        """
        js = compile_to_js(source)
        assert "Object.freeze({...p, x:" in js
