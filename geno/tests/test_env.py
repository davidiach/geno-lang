"""Tests for env_get and env_get_or builtins."""

import json
import subprocess

from geno.api import RunConfig, run
from geno.diagnostics import ErrorCode


class TestEnvGetInterpreter:
    """Test env_get via the embedding API (interpreter path)."""

    def test_env_get_returns_some_when_set(self, monkeypatch):
        monkeypatch.setenv("GENO_TEST_VAR", "hello")
        source = """
        func main() -> String
            return unwrap(env_get(name: "GENO_TEST_VAR"))
        end func
        """
        config = RunConfig(capabilities={"env"})
        result = run(source, config=config)
        assert result.ok is True
        assert result.value == "hello"

    def test_env_get_returns_none_when_unset(self, monkeypatch):
        monkeypatch.delenv("GENO_TEST_MISSING", raising=False)
        source = """
        func main() -> Bool
            return is_none(env_get(name: "GENO_TEST_MISSING"))
        end func
        """
        config = RunConfig(capabilities={"env"})
        result = run(source, config=config)
        assert result.ok is True
        assert result.value is True

    def test_env_get_or_returns_value_when_set(self, monkeypatch):
        monkeypatch.setenv("GENO_TEST_VAR", "world")
        source = """
        func main() -> String
            return env_get_or(name: "GENO_TEST_VAR", default: "fallback")
        end func
        """
        config = RunConfig(capabilities={"env"})
        result = run(source, config=config)
        assert result.ok is True
        assert result.value == "world"

    def test_env_get_or_returns_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("GENO_TEST_MISSING", raising=False)
        source = """
        func main() -> String
            return env_get_or(name: "GENO_TEST_MISSING", default: "fallback")
        end func
        """
        config = RunConfig(capabilities={"env"})
        result = run(source, config=config)
        assert result.ok is True
        assert result.value == "fallback"


class TestEnvCapabilityGating:
    """Test that env builtins are denied without capability."""

    def test_env_get_denied_without_capability(self):
        source = """
        func main() -> Bool
            return is_none(env_get(name: "PATH"))
        end func
        """
        config = RunConfig(capabilities=set())
        result = run(source, config=config)
        assert result.ok is False
        denied = [
            d
            for d in result.diagnostics
            if d.code == ErrorCode.RUNTIME_CAPABILITY_DENIED
        ]
        assert len(denied) > 0

    def test_env_get_or_denied_without_capability(self):
        source = """
        func main() -> String
            return env_get_or(name: "PATH", default: "none")
        end func
        """
        config = RunConfig(capabilities=set())
        result = run(source, config=config)
        assert result.ok is False
        denied = [
            d
            for d in result.diagnostics
            if d.code == ErrorCode.RUNTIME_CAPABILITY_DENIED
        ]
        assert len(denied) > 0

    def test_env_get_or_denied_when_capabilities_omitted(self, monkeypatch):
        monkeypatch.setenv("GENO_TEST_DEFAULT_DENY", "secret")
        source = """
        func main() -> String
            return env_get_or(name: "GENO_TEST_DEFAULT_DENY", default: "none")
        end func
        """
        result = run(source)
        assert result.ok is False
        denied = [
            d
            for d in result.diagnostics
            if d.code == ErrorCode.RUNTIME_CAPABILITY_DENIED
        ]
        assert len(denied) > 0
        assert "env_get_or" in denied[0].message

    def test_env_get_works_with_env_capability(self, monkeypatch):
        monkeypatch.setenv("GENO_TEST_CAP", "granted")
        source = """
        func main() -> String
            return unwrap(env_get(name: "GENO_TEST_CAP"))
        end func
        """
        config = RunConfig(capabilities={"env"})
        result = run(source, config=config)
        assert result.ok is True
        assert result.value == "granted"

    def test_env_get_respects_allowed_name_policy(self, monkeypatch):
        monkeypatch.setenv("GENO_PUBLIC_ENV", "ok")
        source = """
        func main() -> String
            return unwrap(env_get(name: "GENO_PUBLIC_ENV"))
        end func
        """
        config = RunConfig(
            capabilities={"env"},
            env_allowed_names={"GENO_PUBLIC_ENV"},
        )
        result = run(source, config=config)
        assert result.ok is True
        assert result.value == "ok"

    def test_env_get_denies_unlisted_name_when_policy_enabled(self, monkeypatch):
        monkeypatch.setenv("GENO_PRIVATE_ENV", "secret")
        source = """
        func main() -> String
            return unwrap(env_get(name: "GENO_PRIVATE_ENV"))
        end func
        """
        config = RunConfig(capabilities={"env"}, env_allowed_names={"GENO_PUBLIC_ENV"})
        result = run(source, config=config)
        assert result.ok is False
        denied = [
            d
            for d in result.diagnostics
            if d.code == ErrorCode.RUNTIME_CAPABILITY_DENIED
        ]
        assert len(denied) > 0
        assert "not allowed by the host env policy" in denied[0].message

    def test_env_prefix_policy_denies_sensitive_names_by_default(self, monkeypatch):
        monkeypatch.setenv("GENO_PUBLIC_VALUE", "ok")
        monkeypatch.setenv("GENO_API_KEY", "secret")

        public_source = """
        func main() -> String
            return unwrap(env_get(name: "GENO_PUBLIC_VALUE"))
        end func
        """
        public_result = run(
            public_source,
            config=RunConfig(
                capabilities={"env"},
                env_allowed_prefixes={"GENO_"},
            ),
        )
        assert public_result.ok is True
        assert public_result.value == "ok"

        secret_source = """
        func main() -> String
            return env_get_or(name: "GENO_API_KEY", default: "missing")
        end func
        """
        secret_result = run(
            secret_source,
            config=RunConfig(
                capabilities={"env"},
                env_allowed_prefixes={"GENO_"},
            ),
        )
        assert secret_result.ok is False
        denied = [
            d
            for d in secret_result.diagnostics
            if d.code == ErrorCode.RUNTIME_CAPABILITY_DENIED
        ]
        assert len(denied) > 0

    def test_sensitive_env_denylist_covers_canonical_secrets(self):
        """H-06: the safety-net denylist must catch canonical secret env var
        names, including merged-word and _PWD forms, while leaving common
        non-secret names alone."""
        from geno.api import _is_sensitive_env_name

        for name in (
            "PGPASSWORD",
            "MYSQL_PWD",
            "PASSWORD",
            "PASSWD",
            "TOKEN",
            "SECRET",
            "GITHUB_TOKEN",
            "DB_PASSWORD",
            "STRIPE_SECRET",
            "AWS_SECRET_ACCESS_KEY",
        ):
            assert _is_sensitive_env_name(name), f"{name} should be denied"

        for name in ("GENO_PUBLIC_VALUE", "HOME", "PATH", "PWD", "MONKEY_COUNT"):
            assert not _is_sensitive_env_name(name), f"{name} should be allowed"

    def test_broad_prefix_still_denies_secret_by_safety_net(self, monkeypatch):
        """A broad allow-prefix must not expose a secret the denylist covers."""
        monkeypatch.setenv("APP_PASSWORD", "hunter2")
        source = """
        func main() -> String
            return env_get_or(name: "APP_PASSWORD", default: "missing")
        end func
        """
        result = run(
            source,
            config=RunConfig(capabilities={"env"}, env_allowed_prefixes={"APP_"}),
        )
        assert result.ok is False
        denied = [
            d
            for d in result.diagnostics
            if d.code == ErrorCode.RUNTIME_CAPABILITY_DENIED
        ]
        assert len(denied) > 0

    def test_cli_args_requires_geno_cli_args_policy_entry(self, monkeypatch):
        monkeypatch.setenv("GENO_CLI_ARGS", json.dumps(["secret"]))
        source = """
        func main() -> Int
            return length(cli_args())
        end func
        """
        config = RunConfig(
            capabilities={"env"},
            env_allowed_prefixes={"PUBLIC_"},
        )
        result = run(source, config=config)
        assert result.ok is False
        denied = [
            d
            for d in result.diagnostics
            if d.code == ErrorCode.RUNTIME_CAPABILITY_DENIED
        ]
        assert len(denied) > 0


class TestEnvPatternMatching:
    """Test env_get with pattern matching on Option."""

    def test_env_get_match_some(self, monkeypatch):
        monkeypatch.setenv("GENO_TEST_MATCH", "found")
        source = """
        func main() -> String
            let val: Option[String] = env_get(name: "GENO_TEST_MATCH")
            match val with
                | Some(v) -> return v
                | None -> return "missing"
            end match
        end func
        """
        config = RunConfig(capabilities={"env"})
        result = run(source, config=config)
        assert result.ok is True
        assert result.value == "found"

    def test_env_get_match_none(self, monkeypatch):
        monkeypatch.delenv("GENO_TEST_MATCH_MISS", raising=False)
        source = """
        func main() -> String
            let val: Option[String] = env_get(name: "GENO_TEST_MATCH_MISS")
            match val with
                | Some(v) -> return v
                | None -> return "missing"
            end match
        end func
        """
        config = RunConfig(capabilities={"env"})
        result = run(source, config=config)
        assert result.ok is True
        assert result.value == "missing"


class TestEnvCompiledPython:
    """Test env builtins in compiled Python output."""

    def test_env_get_compiled(self, monkeypatch):
        monkeypatch.setenv("GENO_TEST_COMPILED", "compiled_val")
        source = """
        func main() -> String
            return unwrap(env_get(name: "GENO_TEST_COMPILED"))
        end func
        """
        from geno.compiler import Compiler

        compiler = Compiler()
        python_code = compiler.compile(_parse(source))
        # Grant env capability for compiled output test
        python_code += '\n_GENO_CAPS.add("env")\n'
        python_code += "__result__ = main()\n"

        env: dict[str, object] = {}
        exec(python_code, env)
        assert env["__result__"] == "compiled_val"

    def test_env_get_or_compiled(self, monkeypatch):
        monkeypatch.delenv("GENO_TEST_COMPILED_MISS", raising=False)
        source = """
        func main() -> String
            return env_get_or(name: "GENO_TEST_COMPILED_MISS", default: "default_val")
        end func
        """
        from geno.compiler import Compiler

        compiler = Compiler()
        python_code = compiler.compile(_parse(source))
        # Grant env capability for compiled output test
        python_code += '\n_GENO_CAPS.add("env")\n'
        python_code += "__result__ = main()\n"

        env: dict[str, object] = {}
        exec(python_code, env)
        assert env["__result__"] == "default_val"


class TestEnvCompiledJS:
    """Test env builtins in compiled JS output."""

    def test_env_get_js(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GENO_TEST_JS", "js_val")
        source = """
        func main() -> String
            return unwrap(env_get(name: "GENO_TEST_JS"))
        end func
        """
        from geno.js_compiler import compile_to_js

        js_code = compile_to_js(source)
        js_file = tmp_path / "env_get.js"
        js_file.write_text(js_code)
        result = subprocess.run(
            ["node", str(js_file), "--cap", "env"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == "js_val"

    def test_env_get_or_js(self, monkeypatch, tmp_path):
        monkeypatch.delenv("GENO_TEST_JS_MISS", raising=False)
        source = """
        func main() -> String
            return env_get_or(name: "GENO_TEST_JS_MISS", default: "js_default")
        end func
        """
        from geno.js_compiler import compile_to_js

        js_code = compile_to_js(source)
        js_file = tmp_path / "env_get_or.js"
        js_file.write_text(js_code)
        result = subprocess.run(
            ["node", str(js_file), "--cap", "env"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == "js_default"


def _parse(source: str):
    """Helper to lex+parse source into a Program AST."""
    from geno.lexer import Lexer
    from geno.parser import Parser

    tokens = Lexer(source).tokenize()
    return Parser(tokens).parse_program()
