# Contributing to Geno

Thank you for your interest in contributing to Geno!

## Development Setup

1. Clone the repository:
   ```bash
   git clone https://github.com/davidiach/geno-lang.git
   cd geno
   ```

2. Create a virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. Install dependencies:
   ```bash
   pip install -e ".[dev]"
   ```

4. Run tests:
   ```bash
   pytest geno/tests/ -v
   ```

## Project Structure

- `geno/` - Language implementation (lexer, parser, interpreter, compiler, sandbox, API, server)
- `selfhost/` - Self-hosted frontend + interpreter written in Geno (8 modules)
- `benchmark/` - Evaluation problem corpus and runner
- `docs/` - Specifications and documentation
- `experiment/` - Experimental framework (code)
- `analysis/` - Results analysis and reporting

## How to Contribute

### Reporting Issues

- Use GitHub Issues for bug reports and feature requests
- Include reproduction steps for bugs
- Describe expected vs actual behavior

### Pull Requests

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/my-feature`)
3. Make your changes
4. Add tests for new functionality
5. Ensure all tests pass (`pytest`)
6. Submit a pull request

Use the local workflow in
[`docs/operations/local-ci.md`](docs/operations/local-ci.md) before opening a
pull request, and include the commands/results in the PR description.

### Code Style

- Code is formatted and linted with [Ruff](https://github.com/astral-sh/ruff)
- Run `make format` to auto-format, `make lint` to check
- Use type hints where appropriate
- Add docstrings for public functions
- Keep functions focused and small
- Install pre-commit hooks: `pre-commit install`

### Areas for Contribution

- **Language Features**: New constructs, syntax improvements
- **Benchmark Problems**: Additional problems, domains
- **Analysis Tools**: New metrics, visualizations
- **Documentation**: Tutorials, examples, API docs
- **Testing**: Test coverage, edge cases

## Research Contributions

If you're interested in extending the research:

1. New language features and their impact on LLM generation
2. Additional LLM models for evaluation
3. Different programming domains
4. Human factors studies

## Commit Message Format

This project follows [Conventional Commits](https://www.conventionalcommits.org/).
Use the format `type(scope): description`, where type is one of:

- **feat** -- New feature
- **fix** -- Bug fix
- **docs** -- Documentation only
- **test** -- Adding or updating tests
- **refactor** -- Code change that neither fixes a bug nor adds a feature
- **ci** -- CI/CD configuration changes
- **chore** -- Maintenance tasks (dependencies, tooling, etc.)

Example: `feat(parser): add support for string interpolation`

## Branch Naming

Use a descriptive prefix followed by a short kebab-case description:

- `feature/short-description`
- `fix/short-description`
- `docs/short-description`

## PR Requirements

- Must reference an issue (if applicable)
- Must include local validation
- Must include tests for new functionality
- Must receive at least one approval before merging

Local verification workflow:

```bash
# Most PRs
python3 scripts/local_ci.py targeted --paths <changed-paths...> --tests <pytest-targets...>

# Broad or merge-readiness sweep
python3 scripts/local_ci.py full
```

Use `python3 scripts/local_ci.py release` for release-sensitive, benchmark, or
template-gate changes. See [`docs/operations/local-ci.md`](docs/operations/local-ci.md)
for the full verification policy.

## AI-Assisted Development

AI tools are welcome, but they are optional. Human contributors should use this
`CONTRIBUTING.md` as the source of truth for setup, validation, and pull
request expectations. `AGENTS.md` contains supplemental guidance for coding
agents that work in this repository.

## Questions?

Open an issue for discussion or reach out to the maintainers.

## License

By contributing, you agree that your contributions will be licensed under the Apache License 2.0.
