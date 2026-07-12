"""
Project scaffolding for ``geno init``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List

# ---------------------------------------------------------------------------
# CI workflow (shared across all templates)
# ---------------------------------------------------------------------------

_CI_YML = """\
name: Geno CI
on:
  push:
    branches: [main]
  pull_request:
    branches: [main]
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install geno-lang
      - run: geno check .
      - run: geno test .
"""

# ---------------------------------------------------------------------------
# Template definitions
# ---------------------------------------------------------------------------

_TEMPLATES: Dict[str, Dict[str, str]] = {
    # ---- minimal ----
    "minimal": {
        "geno.toml": (
            'name = "{name}"\n'
            'version = "0.1.0"\n'
            'entrypoint = "Main"\n'
            'targets = ["python-cli"]\n'
            'files = [\n    "Main",\n]\n'
        ),
        "Main.geno": (
            "func greet(name: String) -> String\n"
            '    example "World" -> "Hello, World!"\n'
            '    return "Hello, " + name + "!"\n'
            "end func\n"
            "\n"
            '@untested("entry point")\n'
            "func main() -> String\n"
            '    return greet("World")\n'
            "end func\n"
        ),
    },
    # ---- cli ----
    "cli": {
        "geno.toml": (
            'name = "{name}"\n'
            'version = "0.1.0"\n'
            'entrypoint = "Main"\n'
            'targets = ["python-cli"]\n'
            'files = [\n    "Main",\n]\n'
        ),
        "Main.geno": (
            "/// A simple CLI application.\n"
            "\n"
            "func greet(name: String) -> String\n"
            '    example "World" -> "Hello, World!"\n'
            '    example "Geno" -> "Hello, Geno!"\n'
            '    return "Hello, " + name + "!"\n'
            "end func\n"
            "\n"
            "func format_greeting(greeting: String) -> String\n"
            '    example "Hello, World!" -> ">>> Hello, World! <<<"\n'
            '    return ">>> " + greeting + " <<<"\n'
            "end func\n"
            "\n"
            '@untested("entry point")\n'
            "func main() -> Unit\n"
            '    print(format_greeting(greet("World")))\n'
            "    return ()\n"
            "end func\n"
        ),
        ".github/workflows/ci.yml": _CI_YML,
        "README.md": (
            "# {name}\n"
            "\n"
            "A CLI application built with [Geno](https://github.com/davidiach/geno).\n"
            "\n"
            "## Run\n"
            "\n"
            "```bash\n"
            "geno run Main.geno\n"
            "```\n"
            "\n"
            "## Test\n"
            "\n"
            "```bash\n"
            "geno test .\n"
            "```\n"
            "\n"
            "## Compile\n"
            "\n"
            "```bash\n"
            "geno compile Main.geno           # Python\n"
            "geno compile Main.geno --target js  # JavaScript\n"
            "```\n"
            "\n"
            "## Deploy\n"
            "\n"
            "Compile to Python or JS and distribute the output file.\n"
        ),
    },
    # ---- web ----
    "web": {
        "geno.toml": (
            'name = "{name}"\n'
            'version = "0.1.0"\n'
            'entrypoint = "Main"\n'
            'targets = ["browser"]\n'
            'files = [\n    "Main",\n]\n'
        ),
        "Main.geno": (
            "/// A browser app using the init/update/render lifecycle.\n"
            "\n"
            "type Model = Model(count: Int)\n"
            "\n"
            '@untested("lifecycle")\n'
            "func init() -> Model\n"
            "    return Model(0)\n"
            "end func\n"
            "\n"
            '@untested("lifecycle")\n'
            "func update(model: Model, dt: Float) -> Model\n"
            '    if is_key_pressed("ArrowUp") then\n'
            "        return Model(model.count + 1)\n"
            "    end if\n"
            '    if is_key_pressed("ArrowDown") then\n'
            "        return Model(model.count - 1)\n"
            "    end if\n"
            "    return model\n"
            "end func\n"
            "\n"
            '@untested("rendering")\n'
            "func render(model: Model) -> Unit\n"
            '    clear_screen("#111111")\n'
            "    draw_text(\n"
            '        text: "Count: " + to_string(model.count),\n'
            "        x: 350,\n"
            "        y: 280,\n"
            "        size: 32,\n"
            '        color: "#ffffff"\n'
            "    )\n"
            "    draw_text(\n"
            '        text: "Press UP/DOWN arrow keys",\n'
            "        x: 280,\n"
            "        y: 340,\n"
            "        size: 16,\n"
            '        color: "#888888"\n'
            "    )\n"
            "    return ()\n"
            "end func\n"
        ),
        ".github/workflows/ci.yml": _CI_YML,
        "README.md": (
            "# {name}\n"
            "\n"
            "A browser app built with [Geno](https://github.com/davidiach/geno).\n"
            "\n"
            "## Dev Server\n"
            "\n"
            "```bash\n"
            "geno dev Main.geno\n"
            "```\n"
            "\n"
            "Open http://localhost:3000 in your browser.\n"
            "\n"
            "## Test\n"
            "\n"
            "```bash\n"
            "geno test .\n"
            "```\n"
            "\n"
            "## Build\n"
            "\n"
            "```bash\n"
            "geno build Main.geno              # dist/ directory\n"
            "geno build Main.geno --single-file # single HTML file\n"
            "```\n"
            "\n"
            "## Deploy\n"
            "\n"
            "Upload the `dist/` directory to any static host (Netlify, Vercel,\n"
            "GitHub Pages, S3, etc.).\n"
        ),
    },
    # ---- api ----
    "api": {
        "geno.toml": (
            'name = "{name}"\n'
            'version = "0.1.0"\n'
            'entrypoint = "Main"\n'
            'targets = ["python-cli"]\n'
            'files = [\n    "Main",\n    "Routes",\n]\n'
        ),
        "Main.geno": (
            "/// HTTP API server entry point.\n"
            "\n"
            "import Routes\n"
            "\n"
            '@untested("entry point")\n'
            "func main() -> String\n"
            '    return Routes.handle_request("/hello")\n'
            "end func\n"
        ),
        "Routes.geno": (
            "/// Route handlers for the API.\n"
            "\n"
            "func hello_response() -> String\n"
            '    example () -> "Hello from Geno API!"\n'
            '    return "Hello from Geno API!"\n'
            "end func\n"
            "\n"
            "func health_response() -> String\n"
            '    example () -> "ok"\n'
            '    return "ok"\n'
            "end func\n"
            "\n"
            "func handle_request(path: String) -> String\n"
            '    example "/hello" -> "Hello from Geno API!"\n'
            '    example "/health" -> "ok"\n'
            '    example "/unknown" -> "not found"\n'
            "    match path with\n"
            '        | "/hello" -> return hello_response()\n'
            '        | "/health" -> return health_response()\n'
            '        | _ -> return "not found"\n'
            "    end match\n"
            "end func\n"
        ),
        ".github/workflows/ci.yml": _CI_YML,
        "README.md": (
            "# {name}\n"
            "\n"
            "An API server built with [Geno](https://github.com/davidiach/geno).\n"
            "\n"
            "## Run\n"
            "\n"
            "```bash\n"
            "geno run Main.geno\n"
            "```\n"
            "\n"
            "## Test\n"
            "\n"
            "```bash\n"
            "geno test .\n"
            "```\n"
            "\n"
            "## Compile\n"
            "\n"
            "```bash\n"
            "geno compile Main.geno           # Python\n"
            "geno compile Main.geno --target js  # JavaScript\n"
            "```\n"
            "\n"
            "## Deploy\n"
            "\n"
            "Compile to Python and run behind a reverse proxy, or compile to JS\n"
            "and deploy to a serverless platform (AWS Lambda, Cloudflare Workers).\n"
        ),
    },
    # ---- lib ----
    "lib": {
        "geno.toml": (
            'name = "{name}"\n'
            'version = "0.1.0"\n'
            'targets = ["python-cli", "node-cli"]\n'
            'files = [\n    "Lib",\n]\n'
            "\n"
            "[exports]\n"
            'modules = ["Lib"]\n'
        ),
        "Lib.geno": (
            "/// A reusable library module.\n"
            "\n"
            "func double(x: Int) -> Int\n"
            "    example 3 -> 6\n"
            "    example 0 -> 0\n"
            "    return x * 2\n"
            "end func\n"
            "\n"
            "func clamp(value: Int, lo: Int, hi: Int) -> Int\n"
            "    example 5, 0, 10 -> 5\n"
            "    example -1, 0, 10 -> 0\n"
            "    example 15, 0, 10 -> 10\n"
            "    if value < lo then return lo end if\n"
            "    if value > hi then return hi end if\n"
            "    return value\n"
            "end func\n"
            "\n"
            "func is_between(value: Int, lo: Int, hi: Int) -> Bool\n"
            "    example 5, 0, 10 -> true\n"
            "    example -1, 0, 10 -> false\n"
            "    example 15, 0, 10 -> false\n"
            "    if value < lo then return false end if\n"
            "    if value > hi then return false end if\n"
            "    return true\n"
            "end func\n"
        ),
        ".github/workflows/ci.yml": _CI_YML,
        "README.md": (
            "# {name}\n"
            "\n"
            "A library built with [Geno](https://github.com/davidiach/geno).\n"
            "\n"
            "## Test\n"
            "\n"
            "```bash\n"
            "geno test .\n"
            "```\n"
            "\n"
            "## Use in Another Project\n"
            "\n"
            "Add as a dependency in your project's `geno.toml`:\n"
            "\n"
            "```toml\n"
            "[dependencies]\n"
            '{name} = {{ git = "https://github.com/user/{name}.git" }}\n'
            "```\n"
            "\n"
            "Then import in your Geno code:\n"
            "\n"
            "```\n"
            "import Lib\n"
            "let x: Int = Lib.double(5)\n"
            "```\n"
        ),
    },
    # ---- app (legacy, kept for backwards compatibility) ----
    "app": {
        "geno.toml": (
            'name = "{name}"\n'
            'version = "0.1.0"\n'
            'entrypoint = "Main"\n'
            'targets = ["browser"]\n'
            'files = [\n    "Main",\n]\n'
        ),
        "Main.geno": (
            "type Model = Model(count: Int)\n"
            "\n"
            '@untested("scaffold")\n'
            "func init_model() -> Model\n"
            "    return Model(0)\n"
            "end func\n"
            "\n"
            "func update(model: Model, key: String) -> Model\n"
            '    example Model(0), "up" -> Model(1)\n'
            "    match key with\n"
            '        | "up" -> return Model(model.count + 1)\n'
            '        | "down" -> return Model(model.count - 1)\n'
            "        | _ -> return model\n"
            "    end match\n"
            "end func\n"
            "\n"
            "func view(model: Model) -> String\n"
            '    example Model(5) -> "Count: 5"\n'
            '    return f"Count: {model.count}"\n'
            "end func\n"
            "\n"
            '@untested("entry point")\n'
            "func main() -> String\n"
            "    return view(Model(0))\n"
            "end func\n"
        ),
    },
}


def create_project(project_path: Path, template: str = "minimal") -> List[str]:
    """Create a project directory from a template. Returns list of created file paths."""
    if template not in _TEMPLATES:
        available = ", ".join(sorted(k for k in _TEMPLATES if k != "app"))
        raise ValueError(f"Unknown template: {template}. Available: {available}")

    if project_path.exists() and not project_path.is_dir():
        raise FileExistsError(f"{project_path} exists and is not a directory")

    conflicts = []
    for filename in _TEMPLATES[template]:
        filepath = project_path / filename
        if filepath.exists():
            conflicts.append(str(filepath))
    if conflicts:
        conflict_list = ", ".join(conflicts)
        raise FileExistsError(f"Refusing to overwrite existing files: {conflict_list}")

    project_path.mkdir(parents=True, exist_ok=True)
    project_name = project_path.resolve().name

    created = []
    for filename, content in _TEMPLATES[template].items():
        filepath = project_path / filename
        # Create subdirectories if needed (e.g. .github/workflows/)
        filepath.parent.mkdir(parents=True, exist_ok=True)
        if filename == "geno.toml" or filename == "README.md":
            content = content.format(name=project_name)
        filepath.write_text(content, encoding="utf-8")
        created.append(str(project_path / filename))

    return created
