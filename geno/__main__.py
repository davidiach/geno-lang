"""
Geno Command-Line Interface
===========================

Usage:
    geno                    # Start REPL
    geno run <file>         # Run a Geno file
    geno compile <file>     # Compile to Python
    geno check <file>       # Type check only
    geno constrain [prefix] # Inspect next-token constraints for a prefix
    geno install            # Install dependencies from geno.toml
    geno add <name> [url]   # Add a git dependency or curated package
    geno update [name]      # Update dependencies
    geno lsp                # Start LSP server
    geno serve              # Run the hosted HTTP runtime
"""

import argparse
import math
import os
from typing import Any

from .capabilities import CapabilityParseError, normalize_capability_values
from .cli._util import (
    _check_python_version,
)
from .cli._util import (
    _emit_unsupported_python_error as _emit_unsupported_python_error,
)
from .cli._util import (
    _format_source_snippet as _format_source_snippet,
)
from .cli._util import (
    _print_error as _print_error,
)
from .cli._util import (
    _print_runtime_error as _print_runtime_error,
)
from .execution_limits import (
    DEFAULT_INTERPRETER_MAX_STEPS,
    DEFAULT_PROCESS_MAX_MEMORY_BYTES,
    DEFAULT_TEST_MAX_STEPS,
    DEFAULT_TEST_TIMEOUT,
)

# Subcommand implementations are imported lazily: eagerly importing every
# subcommand made each CLI invocation pay for all of them. dispatch_args
# imports the selected command at its call site, and the module-level
# __getattr__ below preserves ``from geno.__main__ import run_file``-style
# re-exports for existing callers (tests, scripts).
_LAZY_REEXPORTS = {
    "format_files": "geno._cli_format",
    "build_app": "geno.cli.build",
    "bundle_project": "geno.cli.build",
    "check_file": "geno.cli.check",
    "compile_file": "geno.cli.compile",
    "constrain_cli": "geno.cli.constrain",
    "_resolve_doc_modules": "geno.cli.docs",
    "generate_docs": "geno.cli.docs",
    "init_project": "geno.cli.init",
    "start_lsp": "geno.cli.lsp",
    "pkg_add": "geno.cli.pkg",
    "pkg_install": "geno.cli.pkg",
    "pkg_search": "geno.cli.pkg",
    "pkg_update": "geno.cli.pkg",
    "start_repl": "geno.cli.repl",
    "run_file": "geno.cli.run",
    "_build_dev_server_html": "geno.cli.serve",
    "dev_server": "geno.cli.serve",
    "serve_runtime": "geno.cli.serve",
    "_print_test_results": "geno.cli.test",
    "_run_test_suite_once": "geno.cli.test",
    "_run_tests_watch": "geno.cli.test",
    "run_tests": "geno.cli.test",
    "_resolve_watch_files": "geno.cli.watch",
    "_snapshot_watch_mtimes": "geno.cli.watch",
    "watch_run": "geno.cli.watch",
}


def __getattr__(name: str) -> Any:
    module_name = _LAZY_REEXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    return getattr(importlib.import_module(module_name), name)


def _command(name: str) -> Any:
    """Resolve a subcommand implementation for dispatch.

    Module globals win so tests and scripts that monkeypatch
    ``geno.__main__.<name>`` still intercept dispatch; otherwise the
    implementation is imported lazily via __getattr__.
    """
    override = globals().get(name)
    if override is not None:
        return override
    return __getattr__(name)


def _positive_int_arg(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _non_negative_int_arg(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a non-negative integer") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be a non-negative integer")
    return parsed


def _positive_float_arg(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive number") from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive number")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    """Build the Geno command-line parser without executing a command."""
    from . import __version__

    parser = argparse.ArgumentParser(
        description="Geno - LLM-Native Programming Language", prog="geno"
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )

    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # Run command
    run_parser = subparsers.add_parser("run", help="Run a Geno file")
    run_parser.add_argument("file", help="Source file to run")
    run_parser.add_argument(
        "--no-check-examples", action="store_true", help="Skip example verification"
    )
    run_parser.add_argument(
        "--unsafe",
        action="store_true",
        help="Disable process sandbox (use direct interpreter)",
    )
    run_parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="Execution timeout in seconds (default: 30)",
    )
    run_parser.add_argument(
        "--max-steps",
        type=int,
        default=None,
        help=(
            "Maximum interpreter steps for --unsafe/--json "
            f"(default: {DEFAULT_INTERPRETER_MAX_STEPS})"
        ),
    )
    run_parser.add_argument(
        "--max-recursion-depth",
        type=_positive_int_arg,
        default=500,
        help="Maximum Geno call depth (default: 500)",
    )
    run_parser.add_argument(
        "--max-output-length",
        type=_non_negative_int_arg,
        default=100_000,
        help="Maximum captured output characters (default: 100000)",
    )
    run_parser.add_argument(
        "--max-collection-size",
        type=_non_negative_int_arg,
        default=10_000_000,
        help="Maximum string/list collection size (default: 10000000)",
    )
    run_parser.add_argument(
        "--max-integer-bits",
        type=_positive_int_arg,
        default=33_219,
        help="Maximum integer arithmetic bit length (default: 33219)",
    )
    run_parser.add_argument(
        "--max-memory-bytes",
        type=_non_negative_int_arg,
        default=DEFAULT_PROCESS_MAX_MEMORY_BYTES,
        help=(
            "Process sandbox memory limit in bytes (Darwin: VM growth budget "
            "above worker bootstrap); 0 disables "
            f"(default: {DEFAULT_PROCESS_MAX_MEMORY_BYTES})"
        ),
    )
    run_parser.add_argument(
        "--max-cpu-time",
        type=_positive_float_arg,
        default=None,
        help="Process sandbox CPU time limit in seconds (default: none)",
    )
    run_parser.add_argument(
        "--max-file-size-bytes",
        type=_non_negative_int_arg,
        default=0,
        help="Process sandbox file-size limit in bytes (default: 0)",
    )
    run_parser.add_argument(
        "--max-processes",
        type=_positive_int_arg,
        default=1,
        help="Process sandbox process/thread limit (default: 1)",
    )
    run_parser.add_argument(
        "--target",
        choices=["python-cli", "node-cli", "browser", "python-hosted"],
        help="Target platform for availability checking",
    )
    run_parser.add_argument(
        "--cap",
        action="append",
        dest="capabilities",
        metavar="CAPABILITY",
        help=(
            "Grant a capability (e.g. --cap print or --cap fs,print). "
            "Repeatable and comma-separable. Requires --unsafe or --json."
        ),
    )
    run_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output result as JSON (uses embedding API)",
    )

    # Compile command
    compile_parser = subparsers.add_parser("compile", help="Compile to Python or JS")
    compile_parser.add_argument("file", help="Source file to compile")
    compile_parser.add_argument("-o", "--output", help="Output file (default: stdout)")
    compile_parser.add_argument(
        "--target",
        choices=["python", "js"],
        default="python",
        help="Compilation target (default: python)",
    )
    compile_parser.add_argument(
        "--profile",
        choices=["python-cli", "node-cli", "browser", "python-hosted"],
        help="Execution profile used for target-aware compile validation",
    )
    compile_parser.add_argument(
        "--esm",
        action="store_true",
        help="Emit ES module format (JS target only)",
    )
    compile_parser.add_argument(
        "--source-map",
        action="store_true",
        help="Emit a JavaScript source map sidecar (JS target with -o only)",
    )

    # Check command
    check_parser = subparsers.add_parser(
        "check", help="Check types and target compatibility"
    )
    check_parser.add_argument("file", help="Source file to check")
    check_parser.add_argument(
        "--target",
        choices=["python-cli", "node-cli", "browser", "python-hosted"],
        help="Target platform for availability and backend validation",
    )

    # Constraints command
    constrain_parser = subparsers.add_parser(
        "constrain",
        help="Inspect next-token constraints for a partial Geno prefix",
    )
    constrain_parser.add_argument(
        "prefix",
        nargs="?",
        help="Partial Geno source prefix. Reads from stdin if omitted.",
    )
    constrain_parser.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Output structured JSON",
    )
    constrain_parser.add_argument(
        "--validate",
        action="store_true",
        help="Only validate whether the prefix can still be extended",
    )

    # Build command (compile to HTML for app mode)
    build_parser = subparsers.add_parser(
        "build", help="Build a Geno app (default: dist/ directory)"
    )
    build_parser.add_argument("file", help="Source file to build")
    build_parser.add_argument("-o", "--output", help="Output path (default: dist/)")
    build_parser.add_argument(
        "--single-file",
        action="store_true",
        help="Output a single self-contained HTML file instead of dist/",
    )
    build_parser.add_argument(
        "--width", type=int, default=800, help="Canvas width (default: 800)"
    )
    build_parser.add_argument(
        "--height", type=int, default=600, help="Canvas height (default: 600)"
    )
    build_parser.add_argument(
        "--title", default="Geno App", help="HTML page title (default: 'Geno App')"
    )
    build_parser.add_argument(
        "--source-map",
        action="store_true",
        help="Emit JavaScript source maps for browser debugging",
    )

    # Bundle command
    bundle_parser = subparsers.add_parser(
        "bundle", help="Bundle a Geno project into a JSON artifact"
    )
    bundle_parser.add_argument(
        "--config", default="geno.toml", help="Path to geno.toml (default: geno.toml)"
    )
    bundle_parser.add_argument("-o", "--output", help="Output file (default: stdout)")

    # Init command
    init_parser = subparsers.add_parser("init", help="Create a new Geno project")
    init_parser.add_argument("name", help="Project name (becomes directory name)")
    init_parser.add_argument(
        "--template",
        choices=["minimal", "cli", "web", "api", "lib"],
        default="minimal",
        help="Project template (default: minimal)",
    )

    # Test command
    test_parser = subparsers.add_parser(
        "test",
        help="Run interpreter-backed example/test blocks",
        description=(
            "Run example clauses and test blocks through the interpreter. "
            "--target applies platform availability checks; it does not select "
            "a compiled backend."
        ),
    )
    test_parser.add_argument(
        "path", nargs="?", default=".", help="File or directory to test (default: .)"
    )
    test_parser.add_argument(
        "--filter", help="Filter functions by name pattern (glob syntax)"
    )
    test_parser.add_argument(
        "--target",
        choices=["python-cli", "node-cli", "browser", "python-hosted"],
        help=(
            "Typecheck availability against a target profile; tests run in "
            "the interpreter"
        ),
    )
    test_parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TEST_TIMEOUT,
        help=f"Execution timeout in seconds for each test/example (default: {DEFAULT_TEST_TIMEOUT:g})",
    )
    test_parser.add_argument(
        "--max-steps",
        type=int,
        default=DEFAULT_TEST_MAX_STEPS,
        help=(
            "Maximum interpreter steps per project/file test run "
            f"(default: {DEFAULT_TEST_MAX_STEPS})"
        ),
    )
    test_parser.add_argument(
        "--verbose", "-v", action="store_true", help="Show detailed results"
    )
    test_parser.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Machine-readable JSON output",
    )
    test_parser.add_argument(
        "--fail-on-untested",
        action="store_true",
        help="Exit nonzero when any functions are marked @untested",
    )
    test_parser.add_argument(
        "--watch",
        "-w",
        action="store_true",
        help="Re-run tests on file changes",
    )

    # Fmt command
    fmt_parser = subparsers.add_parser("fmt", help="Auto-format Geno source files")
    fmt_parser.add_argument(
        "path", nargs="?", default=".", help="File or directory to format (default: .)"
    )
    fmt_mode = fmt_parser.add_mutually_exclusive_group()
    fmt_mode.add_argument(
        "--check",
        action="store_true",
        help="Check formatting without modifying (exit 1 if changes needed)",
    )
    fmt_mode.add_argument(
        "--diff",
        action="store_true",
        help="Show diff without modifying files",
    )

    # Package manager commands
    subparsers.add_parser("install", help="Install dependencies from geno.toml")

    add_parser = subparsers.add_parser(
        "add", help="Add a dependency to geno.toml (resolves from index if no URL)"
    )
    add_parser.add_argument("name", help="Dependency name")
    add_parser.add_argument(
        "url", nargs="?", default=None, help="Git repository URL (optional if in index)"
    )
    add_parser.add_argument(
        "--branch",
        default=None,
        help="Git branch (default: package index tag, or main for URL installs)",
    )

    search_parser = subparsers.add_parser("search", help="Search the package index")
    search_parser.add_argument("query", help="Search term")

    update_parser = subparsers.add_parser(
        "update", help="Update dependencies to latest commits"
    )
    update_parser.add_argument(
        "name",
        nargs="?",
        default=None,
        help="Dependency name (default: update all)",
    )

    # LSP server command
    lsp_parser = subparsers.add_parser(
        "lsp", help="Start the Language Server Protocol server"
    )
    lsp_parser.add_argument(
        "--tcp", action="store_true", help="Use TCP instead of stdio"
    )
    lsp_parser.add_argument(
        "--port", type=int, default=2087, help="TCP port (default: 2087)"
    )

    # Watch command
    watch_parser = subparsers.add_parser(
        "watch", help="Watch .geno files and re-run on changes"
    )
    watch_parser.add_argument(
        "path", nargs="?", default=".", help="File or directory to watch (default: .)"
    )
    watch_parser.add_argument(
        "--test",
        "-t",
        action="store_true",
        help="Re-run tests instead of running the program",
    )
    watch_parser.add_argument(
        "--filter",
        "-f",
        default=None,
        help="Filter pattern for tests (only with --test)",
    )
    watch_parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Verbose output",
    )
    watch_parser.add_argument(
        "--unsafe",
        action="store_true",
        help="Run changes with the direct interpreter instead of the default process sandbox",
    )

    # Dev server command
    dev_parser = subparsers.add_parser(
        "dev", help="Start dev server with live-reload for browser targets"
    )
    dev_parser.add_argument(
        "path", nargs="?", default=".", help="File or directory (default: .)"
    )
    dev_parser.add_argument(
        "--port", "-p", type=int, default=3000, help="Server port (default: 3000)"
    )
    dev_parser.add_argument(
        "--width", type=int, default=800, help="Canvas width (default: 800)"
    )
    dev_parser.add_argument(
        "--height", type=int, default=600, help="Canvas height (default: 600)"
    )
    dev_parser.add_argument("--title", default="Geno App", help="HTML page title")

    # Doc generator
    doc_parser = subparsers.add_parser("doc", help="Generate HTML documentation")
    doc_parser.add_argument("path", help="Geno file or project directory")
    doc_parser.add_argument(
        "-o",
        "--output",
        default="docs.html",
        help="Output HTML file (default: docs.html)",
    )
    doc_parser.add_argument(
        "--title", default="Geno Documentation", help="Documentation title"
    )

    # REPL command (or default)
    subparsers.add_parser("repl", help="Start interactive REPL")

    # Hosted runtime command (cloud execution API — NOT for local app serving)
    # To run a local app with http_listen/http_route, use:
    # geno run app.geno --unsafe --cap serve
    serve_parser = subparsers.add_parser(
        "serve",
        help="Run the hosted HTTP runtime (cloud API with /healthz, /metrics, /run)",
        description=(
            "Run the hosted HTTP runtime (cloud execution API with /healthz, /metrics, /run). "
            "For local apps with http_listen/http_route, use "
            "'geno run app.geno --unsafe --cap serve' instead."
        ),
    )
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8000)
    serve_parser.add_argument(
        "--service", default=os.getenv("GENO_SERVICE", "geno-api")
    )
    serve_parser.add_argument("--revision", default=os.getenv("GENO_REVISION"))
    serve_parser.add_argument(
        "--allow-capability",
        action="append",
        dest="capabilities",
        help="Capability allowed on POST /run. Repeatable.",
    )
    serve_parser.add_argument(
        "--allow-insecure",
        action="store_true",
        default=False,
        help="Allow non-loopback binding without GENO_API_KEY (unsafe).",
    )
    return parser


def dispatch_args(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
    extra: list[str],
) -> None:
    """Dispatch parsed CLI arguments to the selected command implementation."""
    _check_python_version(
        args.command,
        json_output=bool(getattr(args, "json_output", False)),
        path=getattr(args, "path", None),
    )

    # Only the 'run' command tolerates extra args (passed via cli_args()).
    if extra and args.command != "run":
        parser.error(f"unrecognized arguments: {' '.join(extra)}")

    if args.command == "run":
        # Support comma-separated capabilities: --cap fs,print,env
        caps = None
        if args.capabilities:
            try:
                caps = normalize_capability_values(
                    args.capabilities,
                    allow_comma=True,
                )
            except CapabilityParseError as exc:
                parser.error(str(exc))
        # Extract program args (everything after '--')
        program_args: list[str] = []
        if extra:
            try:
                idx = extra.index("--")
                program_args = extra[idx + 1 :]
            except ValueError:
                program_args = extra  # argparse may strip '--'
        run_file = _command("run_file")
        run_file(
            args.file,
            check_examples=not args.no_check_examples,
            unsafe=args.unsafe,
            timeout=args.timeout,
            max_steps=args.max_steps,
            max_recursion_depth=args.max_recursion_depth,
            max_output_length=args.max_output_length,
            max_collection_size=args.max_collection_size,
            max_integer_bits=args.max_integer_bits,
            max_memory_bytes=(
                None if args.max_memory_bytes == 0 else args.max_memory_bytes
            ),
            max_cpu_time=args.max_cpu_time,
            max_file_size_bytes=args.max_file_size_bytes,
            max_processes=args.max_processes,
            capabilities=caps,
            target=getattr(args, "target", None),
            json_output=args.json_output,
            program_args=program_args,
        )
    elif args.command == "compile":
        compile_file = _command("compile_file")
        compile_file(
            args.file,
            args.output,
            target=args.target,
            esm=args.esm,
            source_map=args.source_map,
            profile=args.profile,
        )
    elif args.command == "build":
        build_app = _command("build_app")
        build_app(
            args.file,
            output=args.output,
            width=args.width,
            height=args.height,
            title=args.title,
            single_file=args.single_file,
            source_map=args.source_map,
        )
    elif args.command == "check":
        check_file = _command("check_file")
        check_file(args.file, target=getattr(args, "target", None))
    elif args.command == "constrain":
        constrain_cli = _command("constrain_cli")
        constrain_cli(
            prefix=args.prefix,
            json_output=args.json_output,
            validate_only=args.validate,
        )
    elif args.command == "bundle":
        bundle_project = _command("bundle_project")
        bundle_project(args.config, args.output)
    elif args.command == "init":
        init_project = _command("init_project")
        init_project(args.name, template=args.template)
    elif args.command == "test":
        run_tests = _command("run_tests")
        run_tests(
            args.path,
            filter_pattern=args.filter,
            verbose=args.verbose,
            json_output=args.json_output,
            watch=args.watch,
            target_name=getattr(args, "target", None),
            fail_on_untested=args.fail_on_untested,
            timeout=args.timeout,
            max_steps=args.max_steps,
        )
    elif args.command == "fmt":
        format_files = _command("format_files")
        format_files(args.path, check=args.check, diff=args.diff)
    elif args.command == "dev":
        dev_server = _command("dev_server")
        dev_server(
            args.path,
            port=args.port,
            width=args.width,
            height=args.height,
            title=args.title,
        )
    elif args.command == "install":
        _command("pkg_install")()
    elif args.command == "add":
        pkg_add = _command("pkg_add")
        pkg_add(args.name, args.url, branch=args.branch)
    elif args.command == "search":
        pkg_search = _command("pkg_search")
        pkg_search(args.query)
    elif args.command == "update":
        pkg_update = _command("pkg_update")
        pkg_update(args.name)
    elif args.command == "lsp":
        start_lsp = _command("start_lsp")
        start_lsp(tcp=args.tcp, port=args.port)
    elif args.command == "serve":
        try:
            allowed_capabilities = (
                normalize_capability_values(args.capabilities, allow_comma=True)
                if args.capabilities
                else None
            )
        except CapabilityParseError as exc:
            parser.error(str(exc))
        serve_runtime = _command("serve_runtime")
        serve_runtime(
            host=args.host,
            port=args.port,
            service=args.service,
            revision=args.revision,
            capabilities=allowed_capabilities,
            allow_insecure=args.allow_insecure,
        )
    elif args.command == "watch":
        watch_run = _command("watch_run")
        watch_run(
            args.path,
            test_mode=args.test,
            filter_pattern=args.filter,
            verbose=args.verbose,
            unsafe=args.unsafe,
        )
    elif args.command == "doc":
        generate_docs = _command("generate_docs")
        generate_docs(args.path, args.output, args.title)
    elif args.command == "repl" or args.command is None:
        _command("start_repl")()
    else:
        parser.print_help()


def main(argv: list[str] | None = None) -> None:
    """CLI entrypoint."""
    parser = build_parser()
    args, extra = parser.parse_known_args(argv)
    dispatch_args(parser, args, extra)


if __name__ == "__main__":
    main()
