"""Side-effect-free compiler validation for target-aware checks."""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Mapping

if TYPE_CHECKING:
    from .ast_nodes import Program
    from .dependency_graph import DependencyGraph
    from .target_profile import TargetProfile


class TargetValidationError(Exception):
    """Raised when checked source cannot be lowered for an execution target."""

    def __init__(self, target: str, message: str):
        self.target = target
        self.backend_message = message
        self.message = f"Target '{target}': {message}"
        super().__init__(self.message)


def _validate(action: Callable[[], object], profile: TargetProfile) -> None:
    """Run one compiler action and normalize backend-specific failures."""
    from .compiler import CompileError
    from .js_compiler import JSCompileError

    try:
        action()
    except (CompileError, JSCompileError) as exc:
        raise TargetValidationError(profile.target, str(exc)) from None


def validate_program_for_target(
    program: Program,
    profile: TargetProfile,
) -> None:
    """Validate one already-typechecked program without writing an artifact."""
    if profile.backend_kind == "python":
        from .compiler import Compiler

        _validate(lambda: Compiler().compile(program), profile)
        return
    if profile.backend_kind == "javascript":
        from .js_compiler import JSCompiler

        _validate(lambda: JSCompiler().compile(program), profile)
        return
    raise RuntimeError(
        f"Target '{profile.target}' has no compiler backend in target metadata."
    )


def validate_program_collection_for_target(
    program: Program,
    modules: Mapping[str, Program],
    profile: TargetProfile,
    *,
    entrypoint_name: str | None = None,
) -> None:
    """Validate an in-memory module collection with project lowering rules."""
    from .ast_nodes import ImportStatement
    from .dependency_graph import DependencyGraph, _topological_sort
    from .project_graph import ProjectGraph

    use_project_lowering = bool(modules) or profile.target == "browser"
    if not use_project_lowering:
        validate_program_for_target(program, profile)
        return

    entrypoint = entrypoint_name or "_geno_api_entrypoint"
    while entrypoint in modules:
        entrypoint += "_"

    parsed = dict(modules)
    parsed[entrypoint] = program
    edges = {
        name: [
            definition.module_name
            for definition in module_program.definitions
            if isinstance(definition, ImportStatement)
        ]
        for name, module_program in parsed.items()
    }
    project = ProjectGraph(root=None, entrypoint=entrypoint, files=[], dependencies={})
    graph = DependencyGraph(
        project=project,
        edges=edges,
        parsed=parsed,
        sorted_modules=_topological_sort(edges),
    )
    validate_project_for_target(graph, profile)


def validate_project_for_target(
    dependency_graph: DependencyGraph,
    profile: TargetProfile,
) -> None:
    """Validate an already-typechecked project, including module namespaces."""
    # Raw Python/Node compilation uses the standalone compiler for one module,
    # while multi-module artifacts (and browser builds) use project lowering.
    use_project_lowering = (
        len(dependency_graph.sorted_modules) > 1 or profile.target == "browser"
    )
    if profile.backend_kind == "python":
        from .compiler import Compiler

        python_compiler = Compiler()
        if use_project_lowering:
            _validate(
                lambda: python_compiler.compile_project(dependency_graph), profile
            )
        else:
            _validate(
                lambda: python_compiler.compile(
                    dependency_graph.parsed[dependency_graph.sorted_modules[0]]
                ),
                profile,
            )
        return
    if profile.backend_kind == "javascript":
        from .js_compiler import JSCompiler

        js_compiler = JSCompiler()
        if use_project_lowering:
            _validate(lambda: js_compiler.compile_project(dependency_graph), profile)
        else:
            _validate(
                lambda: js_compiler.compile(
                    dependency_graph.parsed[dependency_graph.sorted_modules[0]]
                ),
                profile,
            )
        return
    raise RuntimeError(
        f"Target '{profile.target}' has no compiler backend in target metadata."
    )
