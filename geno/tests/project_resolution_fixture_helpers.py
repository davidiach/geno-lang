"""Shared fixtures for project-resolution consistency tests."""

from pathlib import Path


def write_dependency_collision_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Create a manifest project where a local module collides with a dependency."""
    (tmp_path / "geno.toml").write_text(
        'entrypoint = "App"\nfiles = ["App", "Utils"]\n\n'
        '[dependencies.utils]\ngit = "https://example.com/utils.git"\n'
    )
    app_file = tmp_path / "App.geno"
    local_utils_file = tmp_path / "Utils.geno"
    dep_utils_file = tmp_path / "geno_modules" / "utils" / "Utils.geno"
    dep_utils_file.parent.mkdir(parents=True)

    app_file.write_text(
        "import Utils\n"
        '@untested("entry point")\n'
        "func main() -> Int\n"
        "  return helper()\n"
        "end func\n"
    )
    local_utils_file.write_text(
        "func helper() -> Int\n  example () -> 1\n  return 1\nend func\n"
    )
    dep_utils_file.write_text(
        "func helper() -> Int\n  example () -> 2\n  return 2\nend func\n"
    )
    return app_file, local_utils_file, dep_utils_file


def write_dependency_private_collision_fixture(
    tmp_path: Path,
) -> tuple[Path, Path, Path]:
    """Create two dependencies that each ship a private Utils.geno helper."""
    (tmp_path / "geno.toml").write_text(
        'entrypoint = "App"\nfiles = ["App"]\n\n'
        '[dependencies.alpha]\ngit = "https://example.com/alpha.git"\n'
        '[dependencies.beta]\ngit = "https://example.com/beta.git"\n'
    )
    app_file = tmp_path / "App.geno"
    alpha_utils_file = tmp_path / "geno_modules" / "alpha" / "Utils.geno"
    beta_utils_file = tmp_path / "geno_modules" / "beta" / "Utils.geno"
    alpha_utils_file.parent.mkdir(parents=True)
    beta_utils_file.parent.mkdir(parents=True)

    app_file.write_text(
        "import Alpha\n"
        "import Beta\n"
        "func combined() -> Int\n"
        "  example () -> 12\n"
        "  return alpha_value() * 10 + beta_value()\n"
        "end func\n"
        '@untested("entry point")\n'
        "func main() -> Int\n"
        "  return combined()\n"
        "end func\n"
    )
    (alpha_utils_file.parent / "Alpha.geno").write_text(
        "import Utils\n"
        "func alpha_value() -> Int\n"
        "  example () -> 1\n"
        "  return alpha_helper()\n"
        "end func\n"
    )
    alpha_utils_file.write_text(
        "func alpha_helper() -> Int\n  example () -> 1\n  return 1\nend func\n"
    )
    (beta_utils_file.parent / "Beta.geno").write_text(
        "import Utils\n"
        "func beta_value() -> Int\n"
        "  example () -> 2\n"
        "  return beta_helper()\n"
        "end func\n"
    )
    beta_utils_file.write_text(
        "func beta_helper() -> Int\n  example () -> 2\n  return 2\nend func\n"
    )
    return app_file, alpha_utils_file, beta_utils_file


def write_dependency_private_package_name_collision_fixture(
    tmp_path: Path,
) -> tuple[Path, Path, Path]:
    """Create dependencies whose package names collide under naive sanitization."""
    (tmp_path / "geno.toml").write_text(
        'entrypoint = "App"\nfiles = ["App"]\n\n'
        '[dependencies.foo-bar]\ngit = "https://example.com/foo-bar.git"\n'
        '[dependencies.foo_bar]\ngit = "https://example.com/foo_bar.git"\n'
    )
    app_file = tmp_path / "App.geno"
    hyphen_utils_file = tmp_path / "geno_modules" / "foo-bar" / "Utils.geno"
    underscore_utils_file = tmp_path / "geno_modules" / "foo_bar" / "Utils.geno"
    hyphen_utils_file.parent.mkdir(parents=True)
    underscore_utils_file.parent.mkdir(parents=True)

    app_file.write_text(
        "import FooBar\n"
        "import PkgFooBar\n"
        "func combined() -> Int\n"
        "  example () -> 12\n"
        "  return hyphen_value() * 10 + underscore_value()\n"
        "end func\n"
        '@untested("entry point")\n'
        "func main() -> Int\n"
        "  return combined()\n"
        "end func\n"
    )
    (hyphen_utils_file.parent / "FooBar.geno").write_text(
        "import Utils\n"
        "func hyphen_value() -> Int\n"
        "  example () -> 1\n"
        "  return hyphen_helper()\n"
        "end func\n"
    )
    hyphen_utils_file.write_text(
        "func hyphen_helper() -> Int\n  example () -> 1\n  return 1\nend func\n"
    )

    (underscore_utils_file.parent / "geno.toml").write_text(
        'entrypoint = "PkgFooBar"\n'
    )
    (underscore_utils_file.parent / "PkgFooBar.geno").write_text(
        "import Utils\n"
        "func underscore_value() -> Int\n"
        "  example () -> 2\n"
        "  return underscore_helper()\n"
        "end func\n"
    )
    underscore_utils_file.write_text(
        "func underscore_helper() -> Int\n  example () -> 2\n  return 2\nend func\n"
    )
    return app_file, hyphen_utils_file, underscore_utils_file
