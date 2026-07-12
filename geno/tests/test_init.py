"""Tests for geno init project scaffolding."""

import sys
from pathlib import Path

import pytest

from geno.init import create_project


class TestCreateProject:
    def test_minimal_template(self, tmp_path):
        project = tmp_path / "myproject"
        files = create_project(project, "minimal")

        assert project.exists()
        assert (project / "geno.toml").exists()
        assert (project / "Main.geno").exists()
        assert len(files) == 2

    def test_app_template(self, tmp_path):
        project = tmp_path / "myapp"
        files = create_project(project, "app")

        assert (project / "geno.toml").exists()
        assert (project / "Main.geno").exists()
        toml_content = (project / "geno.toml").read_text()
        assert 'entrypoint = "Main"' in toml_content

    def test_lib_template(self, tmp_path):
        project = tmp_path / "mylib"
        create_project(project, "lib")

        assert (project / "geno.toml").exists()
        assert (project / "Lib.geno").exists()
        toml_content = (project / "geno.toml").read_text()
        assert "entrypoint" not in toml_content

    def test_unknown_template(self, tmp_path):
        project = tmp_path / "bad"
        with pytest.raises(ValueError, match="Unknown template"):
            create_project(project, "nonexistent")

    def test_existing_empty_directory_is_allowed(self, tmp_path):
        project = tmp_path / "exists"
        project.mkdir()
        files = create_project(project, "minimal")

        assert (project / "geno.toml").exists()
        assert (project / "Main.geno").exists()
        assert len(files) == 2

    def test_existing_conflicting_file_raises_without_overwriting(self, tmp_path):
        project = tmp_path / "exists"
        project.mkdir()
        main_file = project / "Main.geno"
        main_file.write_text("ORIGINAL", encoding="utf-8")

        with pytest.raises(
            FileExistsError, match="Refusing to overwrite existing files"
        ):
            create_project(project, "minimal")

        assert main_file.read_text(encoding="utf-8") == "ORIGINAL"

    def test_cli_template(self, tmp_path):
        project = tmp_path / "mycli"
        files = create_project(project, "cli")

        assert (project / "geno.toml").exists()
        assert (project / "Main.geno").exists()
        assert (project / ".github" / "workflows" / "ci.yml").exists()
        assert (project / "README.md").exists()
        toml_content = (project / "geno.toml").read_text()
        assert 'entrypoint = "Main"' in toml_content
        assert len(files) == 4

    def test_web_template(self, tmp_path):
        project = tmp_path / "myweb"
        files = create_project(project, "web")

        assert (project / "geno.toml").exists()
        assert (project / "Main.geno").exists()
        assert (project / ".github" / "workflows" / "ci.yml").exists()
        assert (project / "README.md").exists()
        main = (project / "Main.geno").read_text()
        assert "func init()" in main
        assert "update" in main
        assert "render" in main

    def test_api_template(self, tmp_path):
        project = tmp_path / "myapi"
        files = create_project(project, "api")

        assert (project / "geno.toml").exists()
        assert (project / "Main.geno").exists()
        assert (project / "Routes.geno").exists()
        assert (project / ".github" / "workflows" / "ci.yml").exists()
        assert len(files) == 5

    def test_geno_toml_compatible_with_bundle(self, tmp_path):
        """Verify geno.toml format is compatible with the bundle command."""
        project = tmp_path / "bundleable"
        create_project(project, "minimal")

        toml_text = (project / "geno.toml").read_text()
        assert 'entrypoint = "Main"' in toml_text
        assert '"Main"' in toml_text

    def test_templates_include_name_and_version(self, tmp_path):
        """All templates should generate geno.toml with name and version."""
        for template in ["minimal", "app", "cli", "lib"]:
            project = tmp_path / f"proj-{template}"
            create_project(project, template)
            toml_text = (project / "geno.toml").read_text()
            assert f'name = "proj-{template}"' in toml_text
            assert 'version = "0.1.0"' in toml_text

    def test_name_derived_from_directory(self, tmp_path):
        """Project name in geno.toml should match the directory name."""
        project = tmp_path / "my-cool-app"
        create_project(project, "minimal")
        toml_text = (project / "geno.toml").read_text()
        assert 'name = "my-cool-app"' in toml_text

    def test_generated_code_parses(self, tmp_path):
        """Verify the generated .geno files are valid Geno source."""
        from geno.lexer import Lexer
        from geno.parser import Parser

        for template in ["minimal", "app", "cli", "lib"]:
            project = tmp_path / template
            create_project(project, template)
            for geno_file in project.glob("*.geno"):
                source = geno_file.read_text()
                tokens = Lexer(source, str(geno_file)).tokenize()
                program = Parser(tokens).parse_program()
                assert len(program.definitions) > 0


class TestTemplateEndToEnd:
    """CI-style tests: scaffold each template, then check/build/run/test."""

    def test_minimal_check_and_run(self, tmp_path):
        import subprocess

        project = tmp_path / "p"
        create_project(project, "minimal")
        main = str(project / "Main.geno")

        r = subprocess.run(
            [sys.executable, "-m", "geno", "check", main],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert r.returncode == 0, r.stderr

        r = subprocess.run(
            [sys.executable, "-m", "geno", "run", main],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert r.returncode == 0, r.stderr

    def test_app_check_and_build(self, tmp_path):
        import subprocess

        project = tmp_path / "p"
        create_project(project, "app")
        main = str(project / "Main.geno")

        r = subprocess.run(
            [sys.executable, "-m", "geno", "check", main],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert r.returncode == 0, r.stderr

        r = subprocess.run(
            [
                sys.executable,
                "-m",
                "geno",
                "build",
                main,
                "-o",
                str(tmp_path / "out.html"),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert r.returncode == 0, r.stderr
        assert (tmp_path / "out.html").exists()

    def test_cli_check_and_run(self, tmp_path):
        import subprocess

        project = tmp_path / "p"
        create_project(project, "cli")
        main = str(project / "Main.geno")

        r = subprocess.run(
            [sys.executable, "-m", "geno", "check", main],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert r.returncode == 0, r.stderr

        r = subprocess.run(
            [sys.executable, "-m", "geno", "run", main],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert r.returncode == 0, r.stderr

    def test_lib_check_and_test(self, tmp_path):
        import subprocess

        project = tmp_path / "p"
        create_project(project, "lib")
        lib = str(project / "Lib.geno")

        r = subprocess.run(
            [sys.executable, "-m", "geno", "check", lib],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert r.returncode == 0, r.stderr

        r = subprocess.run(
            [sys.executable, "-m", "geno", "test", lib],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert r.returncode == 0, r.stderr
        assert "PASS" in r.stdout


class TestBrowserTemplateSmoke:
    """Verify the web template produces working browser builds with game loop."""

    def test_web_template_compiles_to_js_with_app_mode(self, tmp_path):
        """Web template JS output must contain the requestAnimationFrame game loop."""
        from geno.js_compiler import compile_to_js
        from geno.lexer import Lexer
        from geno.parser import Parser
        from geno.target_profile import TargetProfile
        from geno.typechecker import TypeChecker

        project = tmp_path / "webtest"
        create_project(project, "web")
        source = (project / "Main.geno").read_text()

        tokens = Lexer(source, "Main.geno").tokenize()
        program = Parser(tokens).parse_program()
        browser_profile = TargetProfile.load("browser")
        TypeChecker(target_profile=browser_profile).check_program(program)
        js_code = compile_to_js(source, target_profile=browser_profile)

        # App mode must be detected — game loop bootstrap present
        assert "requestAnimationFrame" in js_code
        assert "_geno_state = init()" in js_code

    def test_web_template_typechecks(self, tmp_path):
        """Web template must pass browser-targeted type checking."""
        from geno.lexer import Lexer
        from geno.parser import Parser
        from geno.target_profile import TargetProfile
        from geno.typechecker import TypeChecker

        project = tmp_path / "webcheck"
        create_project(project, "web")
        source = (project / "Main.geno").read_text()

        tokens = Lexer(source, "Main.geno").tokenize()
        program = Parser(tokens).parse_program()
        # Should not raise
        TypeChecker(target_profile=TargetProfile.load("browser")).check_program(program)


class TestInitTargetsField:
    """Templates should emit targets = [...] in the new manifest shape."""

    def test_minimal_has_targets(self, tmp_path):
        project = tmp_path / "p"
        create_project(project, "minimal")
        toml = (project / "geno.toml").read_text()
        assert "targets" in toml
        assert 'targets = ["python-cli"]' in toml

    def test_cli_has_targets(self, tmp_path):
        project = tmp_path / "p"
        create_project(project, "cli")
        toml = (project / "geno.toml").read_text()
        assert 'targets = ["python-cli"]' in toml

    def test_web_has_browser_target(self, tmp_path):
        project = tmp_path / "p"
        create_project(project, "web")
        toml = (project / "geno.toml").read_text()
        assert 'targets = ["browser"]' in toml

    def test_api_has_targets(self, tmp_path):
        project = tmp_path / "p"
        create_project(project, "api")
        toml = (project / "geno.toml").read_text()
        assert 'targets = ["python-cli"]' in toml

    def test_lib_has_exports_section(self, tmp_path):
        project = tmp_path / "p"
        create_project(project, "lib")
        toml = (project / "geno.toml").read_text()
        assert "[exports]" in toml
        assert 'modules = ["Lib"]' in toml

    def test_lib_manifest_parses_correctly(self, tmp_path):
        from geno.manifest import parse_manifest

        project = tmp_path / "p"
        create_project(project, "lib")
        m = parse_manifest(project / "geno.toml")
        assert m.targets == ["python-cli", "node-cli"]
        assert m.exports == ["Lib"]


class TestScaffoldCommands:
    """Validate that generated CI and README snippets use correct commands."""

    def test_ci_uses_correct_install_command(self, tmp_path):
        """CI should install the ``geno-lang`` distribution."""
        for template in ["cli", "web", "api", "lib"]:
            project = tmp_path / f"proj-{template}"
            create_project(project, template)
            ci = (project / ".github" / "workflows" / "ci.yml").read_text()
            assert "pip install geno-lang" in ci

    def test_readme_uses_target_js_flag(self, tmp_path):
        """README should use --target js, not --js."""
        for template in ["cli", "api"]:
            project = tmp_path / f"proj-{template}"
            create_project(project, template)
            readme = (project / "README.md").read_text()
            assert "--js" not in readme or "--target js" in readme


class TestInitPathTraversal:
    """Test that dangerous project names are rejected by init_project."""

    def test_existing_directory_name_is_allowed(self, tmp_path, monkeypatch, capsys):
        """Existing empty directories should scaffold successfully."""
        from geno.__main__ import init_project

        monkeypatch.chdir(tmp_path)
        project = tmp_path / "existing"
        project.mkdir()

        init_project("existing")

        assert (project / "geno.toml").exists()
        assert (project / "Main.geno").exists()
        assert "Created project 'existing':" in capsys.readouterr().out

    def test_current_directory_is_allowed(self, tmp_path, monkeypatch, capsys):
        """The current working directory should be a valid scaffold target."""
        from geno.__main__ import init_project

        monkeypatch.chdir(tmp_path)

        init_project(".")

        assert (tmp_path / "geno.toml").exists()
        assert (tmp_path / "Main.geno").exists()
        assert f'name = "{tmp_path.name}"' in (tmp_path / "geno.toml").read_text()
        assert "Created project '.':" in capsys.readouterr().out

    def test_existing_conflicting_cli_target_exits(self, tmp_path, monkeypatch):
        """init_project should refuse to overwrite existing files."""
        from geno.__main__ import init_project

        monkeypatch.chdir(tmp_path)
        project = tmp_path / "existing"
        project.mkdir()
        main_file = project / "Main.geno"
        main_file.write_text("ORIGINAL", encoding="utf-8")

        with pytest.raises(SystemExit):
            init_project("existing")

        assert main_file.read_text(encoding="utf-8") == "ORIGINAL"

    @pytest.mark.parametrize(
        "name",
        [
            "../escape",
            "../../etc/evil",
            ".hidden",
            "~nasty",
            "/absolute/path",
        ],
    )
    def test_dangerous_name_rejected(self, name, tmp_path, monkeypatch):
        """Project names with path traversal should be rejected."""
        from geno.__main__ import init_project

        monkeypatch.chdir(tmp_path)
        with pytest.raises(SystemExit):
            init_project(name)
