import nox

nox.options.sessions = ["tests", "lint", "typecheck"]


@nox.session(python=["3.10", "3.11", "3.12", "3.13"])
def tests(session: nox.Session) -> None:
    """Run the test suite with coverage."""
    session.install("-e", ".[dev]")
    session.run(
        "pytest",
        "geno/tests/",
        "-v",
        "--tb=short",
        "--cov=geno",
        "--cov-fail-under=80",
        "--timeout=60",
    )


@nox.session
def lint(session: nox.Session) -> None:
    """Run ruff linter and formatter checks."""
    session.install("-e", ".[dev]")
    session.run("ruff", "check", "geno/", "benchmark/", "experiment/", "analysis/")
    session.run(
        "ruff",
        "format",
        "--check",
        "geno/",
        "benchmark/",
        "experiment/",
        "analysis/",
    )


@nox.session
def typecheck(session: nox.Session) -> None:
    """Run mypy type checking."""
    session.install("-e", ".[dev]")
    session.run("mypy", "geno/")
