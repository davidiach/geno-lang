"""
Tests for DependencyGraph — import graph resolution and validation (#124)
=========================================================================
"""

import os
import sys
from typing import Any

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import geno.dependency_graph as dependency_graph
from geno.compiler import Compiler
from geno.dependency_graph import (
    CircularDependencyError,
    DependencyGraph,
    NameCollisionError,
)
from geno.parser import ParseError, ParseErrors
from geno.project_graph import ProjectGraph, ProjectGraphError
from geno.tests.project_resolution_fixture_helpers import (
    write_dependency_private_collision_fixture,
    write_dependency_private_package_name_collision_fixture,
)
from geno.typechecker import TypeChecker

# =========================================================================
# Linear chain: A imports B imports C
# =========================================================================


class TestLinearChain:
    def test_linear_sorted_order(self, tmp_path):
        """A -> B -> C should produce [C, B, A] order."""
        (tmp_path / "geno.toml").write_text('files = ["A", "B", "C"]\n')
        (tmp_path / "A.geno").write_text(
            "import B\nfunc a() -> Int\n  return 1\nend func\n"
        )
        (tmp_path / "B.geno").write_text(
            "import C\nfunc b() -> Int\n  return 2\nend func\n"
        )
        (tmp_path / "C.geno").write_text("func c() -> Int\n  return 3\nend func\n")

        pg = ProjectGraph.discover(tmp_path)
        dg = DependencyGraph.resolve(pg)

        assert dg.sorted_modules.index("C") < dg.sorted_modules.index("B")
        assert dg.sorted_modules.index("B") < dg.sorted_modules.index("A")

    def test_edges_captured(self, tmp_path):
        """Import edges are recorded correctly."""
        (tmp_path / "geno.toml").write_text('files = ["X", "Y"]\n')
        (tmp_path / "X.geno").write_text(
            "import Y\nfunc x() -> Int\n  return 1\nend func\n"
        )
        (tmp_path / "Y.geno").write_text("func y() -> Int\n  return 2\nend func\n")

        pg = ProjectGraph.discover(tmp_path)
        dg = DependencyGraph.resolve(pg)

        assert dg.edges["X"] == ["Y"]
        assert dg.edges["Y"] == []

    def test_all_files_parsed(self, tmp_path):
        """Every file has a parsed AST."""
        (tmp_path / "geno.toml").write_text('files = ["M", "N"]\n')
        (tmp_path / "M.geno").write_text("func m() -> Int\n  return 1\nend func\n")
        (tmp_path / "N.geno").write_text("func n() -> Int\n  return 2\nend func\n")

        pg = ProjectGraph.discover(tmp_path)
        dg = DependencyGraph.resolve(pg)

        assert "M" in dg.parsed
        assert "N" in dg.parsed


# =========================================================================
# Diamond dependency: A -> B, A -> C, B -> D, C -> D
# =========================================================================


class TestDiamondDependency:
    def test_diamond_sorted_order(self, tmp_path):
        """Diamond: D must come before B and C, which come before A."""
        (tmp_path / "geno.toml").write_text('files = ["A", "B", "C", "D"]\n')
        (tmp_path / "A.geno").write_text(
            "import B\nimport C\nfunc a() -> Int\n  return 1\nend func\n"
        )
        (tmp_path / "B.geno").write_text(
            "import D\nfunc b() -> Int\n  return 2\nend func\n"
        )
        (tmp_path / "C.geno").write_text(
            "import D\nfunc c() -> Int\n  return 3\nend func\n"
        )
        (tmp_path / "D.geno").write_text("func d() -> Int\n  return 4\nend func\n")

        pg = ProjectGraph.discover(tmp_path)
        dg = DependencyGraph.resolve(pg)

        d_idx = dg.sorted_modules.index("D")
        b_idx = dg.sorted_modules.index("B")
        c_idx = dg.sorted_modules.index("C")
        a_idx = dg.sorted_modules.index("A")

        assert d_idx < b_idx
        assert d_idx < c_idx
        assert b_idx < a_idx
        assert c_idx < a_idx

    def test_all_four_modules_present(self, tmp_path):
        """All four modules appear in sorted output."""
        (tmp_path / "geno.toml").write_text('files = ["A", "B", "C", "D"]\n')
        (tmp_path / "A.geno").write_text(
            "import B\nimport C\nfunc a() -> Int\n  return 1\nend func\n"
        )
        (tmp_path / "B.geno").write_text(
            "import D\nfunc b() -> Int\n  return 2\nend func\n"
        )
        (tmp_path / "C.geno").write_text(
            "import D\nfunc c() -> Int\n  return 3\nend func\n"
        )
        (tmp_path / "D.geno").write_text("func d() -> Int\n  return 4\nend func\n")

        pg = ProjectGraph.discover(tmp_path)
        dg = DependencyGraph.resolve(pg)

        assert sorted(dg.sorted_modules) == ["A", "B", "C", "D"]


# =========================================================================
# Circular import detection
# =========================================================================


class TestCircularImport:
    def test_direct_cycle(self, tmp_path):
        """A -> B -> A raises CircularDependencyError with clean cycle."""
        (tmp_path / "geno.toml").write_text('files = ["A", "B"]\n')
        (tmp_path / "A.geno").write_text(
            "import B\nfunc a() -> Int\n  return 1\nend func\n"
        )
        (tmp_path / "B.geno").write_text(
            "import A\nfunc b() -> Int\n  return 2\nend func\n"
        )

        pg = ProjectGraph.discover(tmp_path)
        with pytest.raises(CircularDependencyError) as exc_info:
            DependencyGraph.resolve(pg)

        cycle = exc_info.value.cycle
        assert "A" in cycle
        assert "B" in cycle
        # No duplicate node names in cycle
        assert cycle[0] == cycle[-1], "Cycle should start and end with same node"
        assert len(set(cycle[:-1])) == len(cycle[:-1]), (
            "No duplicates except closing node"
        )

    def test_indirect_cycle(self, tmp_path):
        """A -> B -> C -> A raises CircularDependencyError with clean cycle."""
        (tmp_path / "geno.toml").write_text('files = ["A", "B", "C"]\n')
        (tmp_path / "A.geno").write_text(
            "import B\nfunc a() -> Int\n  return 1\nend func\n"
        )
        (tmp_path / "B.geno").write_text(
            "import C\nfunc b() -> Int\n  return 2\nend func\n"
        )
        (tmp_path / "C.geno").write_text(
            "import A\nfunc c() -> Int\n  return 3\nend func\n"
        )

        pg = ProjectGraph.discover(tmp_path)
        with pytest.raises(CircularDependencyError) as exc_info:
            DependencyGraph.resolve(pg)

        cycle = exc_info.value.cycle
        assert len(cycle) == 4, "3-node cycle should have 4 entries (A->B->C->A)"
        assert cycle[0] == cycle[-1], "Cycle should start and end with same node"
        assert len(set(cycle[:-1])) == 3, "All 3 nodes should appear exactly once"
        assert "Circular import" in str(exc_info.value)

    def test_self_import_cycle(self, tmp_path):
        """A imports itself."""
        (tmp_path / "geno.toml").write_text('files = ["A"]\n')
        (tmp_path / "A.geno").write_text(
            "import A\nfunc a() -> Int\n  return 1\nend func\n"
        )

        pg = ProjectGraph.discover(tmp_path)
        with pytest.raises(CircularDependencyError) as exc_info:
            DependencyGraph.resolve(pg)
        cycle = exc_info.value.cycle
        assert cycle == ["A", "A"], "Self-import cycle should be [A, A]"


# =========================================================================
# Name collision detection
# =========================================================================


class TestNameCollision:
    def test_duplicate_module_name(self, tmp_path):
        """Two files with the same module name raises an error at discovery."""
        (tmp_path / "geno.toml").write_text(
            '[dependencies.utils]\ngit = "https://example.com/utils.git"\n'
        )
        (tmp_path / "Utils.geno").write_text("func u() -> Int\n  return 1\nend func\n")
        dep_dir = tmp_path / "geno_modules" / "utils"
        dep_dir.mkdir(parents=True)
        (dep_dir / "Utils.geno").write_text("func u2() -> Int\n  return 2\nend func\n")

        with pytest.raises(ProjectGraphError, match=r"Module name collision.*Utils"):
            ProjectGraph.discover(tmp_path)

    def test_dependency_private_sibling_modules_use_package_local_graph_keys(
        self, tmp_path
    ):
        _, alpha_utils_file, beta_utils_file = (
            write_dependency_private_collision_fixture(tmp_path)
        )

        pg = ProjectGraph.discover(tmp_path)
        dg = DependencyGraph.resolve(pg)

        alpha_utils_key = next(
            name
            for name, resolved in dg.file_map.items()
            if resolved.path == alpha_utils_file.resolve()
        )
        beta_utils_key = next(
            name
            for name, resolved in dg.file_map.items()
            if resolved.path == beta_utils_file.resolve()
        )

        assert alpha_utils_key != beta_utils_key
        assert dg.edges["Alpha"] == [alpha_utils_key]
        assert dg.edges["Beta"] == [beta_utils_key]

        TypeChecker().check_project_graph(dg)
        compiled = Compiler().compile_project(dg)
        namespace: dict[str, Any] = {}
        exec(compiled, namespace)
        assert namespace["main"]() == 12

    def test_original_sources_keep_pre_rewrite_text_per_graph_key(self, tmp_path):
        app_file, alpha_utils_file, _ = write_dependency_private_collision_fixture(
            tmp_path
        )

        pg = ProjectGraph.discover(tmp_path)
        dg = DependencyGraph.resolve(pg)

        alpha_source = (alpha_utils_file.parent / "Alpha.geno").read_text()
        assert dg.original_sources["Alpha"] == alpha_source
        assert "import Utils" in dg.original_sources["Alpha"]
        assert "import Utils" not in dg.normalized_sources["Alpha"]
        assert dg.original_sources["App"] == app_file.read_text()
        assert set(dg.original_sources) == set(dg.normalized_sources)

        override = "import Utils\nfunc alpha_value() -> Int\n  return 5\nend func\n"
        dg_overridden = DependencyGraph.resolve(
            pg,
            source_overrides={alpha_utils_file.parent / "Alpha.geno": override},
        )
        assert dg_overridden.original_sources["Alpha"] == override
        assert "import Utils" not in dg_overridden.normalized_sources["Alpha"]

    def test_collision_prone_dependency_package_names_still_resolve(self, tmp_path):
        _, hyphen_utils_file, underscore_utils_file = (
            write_dependency_private_package_name_collision_fixture(tmp_path)
        )

        pg = ProjectGraph.discover(tmp_path)
        dg = DependencyGraph.resolve(pg)

        hyphen_utils_key = next(
            name
            for name, resolved in dg.file_map.items()
            if resolved.path == hyphen_utils_file.resolve()
        )
        underscore_utils_key = next(
            name
            for name, resolved in dg.file_map.items()
            if resolved.path == underscore_utils_file.resolve()
        )

        assert hyphen_utils_key != underscore_utils_key
        assert dg.edges["FooBar"] == [hyphen_utils_key]
        assert dg.edges["PkgFooBar"] == [underscore_utils_key]

        TypeChecker().check_project_graph(dg)
        compiled = Compiler().compile_project(dg)
        namespace: dict[str, Any] = {}
        exec(compiled, namespace)
        assert namespace["main"]() == 12


class TestDependencyManifestResolution:
    def test_dependency_private_import_can_use_entrypoint_file_stem(self, tmp_path):
        """A dependency can import its manifest entry module by local file stem."""
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "App"\nfiles = ["App"]\n\n'
            '[dependencies.my-lib]\ngit = "https://example.com/my-lib.git"\n'
        )
        (tmp_path / "App.geno").write_text(
            "import MyLib\n"
            '@untested("entry point")\n'
            "func main() -> Int\n"
            "  return lib_value()\n"
            "end func\n"
        )
        dep_dir = tmp_path / "geno_modules" / "my-lib"
        dep_dir.mkdir(parents=True)
        (dep_dir / "geno.toml").write_text('entrypoint = "Lib"\n')
        (dep_dir / "Lib.geno").write_text(
            "func lib_value() -> Int\n  example () -> 1\n  return 1\nend func\n"
        )
        (dep_dir / "Feature.geno").write_text(
            "import Lib\n"
            "func feature_value() -> Int\n"
            "  example () -> 1\n"
            "  return lib_value()\n"
            "end func\n"
        )

        pg = ProjectGraph.discover(tmp_path)
        dg = DependencyGraph.resolve(pg)

        assert dg.edges["MyLib"] == []
        feature_key = next(
            name
            for name, resolved in dg.file_map.items()
            if resolved.path == (dep_dir / "Feature.geno").resolve()
        )
        assert dg.edges[feature_key] == ["MyLib"]
        TypeChecker().check_project_graph(dg)

    def test_dependency_manifest_files_include_nested_modules(self, tmp_path):
        """Dependency manifests honor files entries that point into subdirs."""
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "App"\nfiles = ["App"]\n\n'
            '[dependencies.lib]\ngit = "https://example.com/lib.git"\n'
        )
        (tmp_path / "App.geno").write_text(
            "import Lib\n"
            '@untested("entry point")\n'
            "func main() -> Int\n"
            "  return lib_value()\n"
            "end func\n"
        )
        dep_dir = tmp_path / "geno_modules" / "lib"
        helper_dir = dep_dir / "src"
        helper_dir.mkdir(parents=True)
        (dep_dir / "geno.toml").write_text(
            'entrypoint = "Lib"\nfiles = ["Lib", "src/Helper"]\n'
        )
        (dep_dir / "Lib.geno").write_text(
            "import Helper\n"
            "func lib_value() -> Int\n"
            "  example () -> 1\n"
            "  return helper_value()\n"
            "end func\n"
        )
        helper_path = helper_dir / "Helper.geno"
        helper_path.write_text(
            "func helper_value() -> Int\n  example () -> 1\n  return 1\nend func\n"
        )

        pg = ProjectGraph.discover(tmp_path)
        dg = DependencyGraph.resolve(pg)

        helper_key = next(
            name
            for name, resolved in dg.file_map.items()
            if resolved.path == helper_path.resolve()
        )
        assert dg.edges["Lib"] == [helper_key]
        TypeChecker().check_project_graph(dg)


# =========================================================================
# Edge cases
# =========================================================================


class TestEdgeCases:
    def test_no_imports(self, tmp_path):
        """Files with no imports produce flat sorted list."""
        (tmp_path / "geno.toml").write_text('files = ["A", "B"]\n')
        (tmp_path / "A.geno").write_text("func a() -> Int\n  return 1\nend func\n")
        (tmp_path / "B.geno").write_text("func b() -> Int\n  return 2\nend func\n")

        pg = ProjectGraph.discover(tmp_path)
        dg = DependencyGraph.resolve(pg)

        assert sorted(dg.sorted_modules) == ["A", "B"]

    def test_single_file(self, tmp_path):
        """Single file project produces a single-element sorted list."""
        solo = tmp_path / "Solo.geno"
        solo.write_text("func solo() -> Int\n  return 1\nend func\n")

        pg = ProjectGraph.discover(solo)
        dg = DependencyGraph.resolve(pg)

        assert dg.sorted_modules == ["Solo"]

    def test_import_of_external_module_ignored(self, tmp_path):
        """Import of a module not in the project graph is silently ignored."""
        (tmp_path / "geno.toml").write_text('files = ["A"]\n')
        (tmp_path / "A.geno").write_text(
            "import External\nfunc a() -> Int\n  return 1\nend func\n"
        )

        pg = ProjectGraph.discover(tmp_path)
        dg = DependencyGraph.resolve(pg)

        assert dg.edges["A"] == ["External"]
        assert dg.sorted_modules == ["A"]


class TestParseCache:
    def test_resolve_reuses_cached_parse_results(self, tmp_path, monkeypatch):
        """A second resolve should reuse the on-disk parse cache."""
        monkeypatch.setenv("GENO_CACHE_DIR", str(tmp_path / ".geno-cache"))
        dependency_graph._PARSED_MODULE_CACHES.clear()

        (tmp_path / "geno.toml").write_text('files = ["A", "B"]\n')
        (tmp_path / "A.geno").write_text(
            "import B\nfunc a() -> Int\n  return 1\nend func\n"
        )
        (tmp_path / "B.geno").write_text("func b() -> Int\n  return 2\nend func\n")

        pg = ProjectGraph.discover(tmp_path)
        first = DependencyGraph.resolve(pg)
        assert first.edges["A"] == ["B"]

        def fail_parse(_self):
            raise AssertionError("parse_program should not run on a cache hit")

        monkeypatch.setattr(dependency_graph.Parser, "parse_program", fail_parse)

        second = DependencyGraph.resolve(pg)
        assert second.edges == first.edges
        assert second.sorted_modules == first.sorted_modules

    def test_resolve_reuses_process_local_memory_cache(self, tmp_path, monkeypatch):
        """A warm resolve should hit in-memory cache before touching disk."""
        monkeypatch.setenv("GENO_CACHE_DIR", str(tmp_path / ".geno-cache"))
        dependency_graph._PARSED_MODULE_CACHES.clear()

        (tmp_path / "geno.toml").write_text('files = ["A"]\n')
        (tmp_path / "A.geno").write_text("func a() -> Int\n  return 1\nend func\n")

        pg = ProjectGraph.discover(tmp_path)
        first = DependencyGraph.resolve(pg)
        assert "A" in first.parsed

        def fail_load(_self, *_args, **_kwargs):
            raise AssertionError("_load_from_disk should not run after a warm resolve")

        monkeypatch.setattr(
            dependency_graph._ParsedModuleCache, "_load_from_disk", fail_load
        )

        second = DependencyGraph.resolve(pg)
        assert second.sorted_modules == ["A"]
        cache = next(iter(dependency_graph._PARSED_MODULE_CACHES.values()))
        assert len(cache._memory) == 1
        assert first.parsed["A"] is not second.parsed["A"]

    def test_disk_cache_hit_reuses_serialized_payload(self, tmp_path, monkeypatch):
        """A disk cache hit should reuse the stored AST payload without redumping."""
        monkeypatch.setenv("GENO_CACHE_DIR", str(tmp_path / ".geno-cache"))
        dependency_graph._PARSED_MODULE_CACHES.clear()

        (tmp_path / "geno.toml").write_text('files = ["A"]\n')
        (tmp_path / "A.geno").write_text("func a() -> Int\n  return 1\nend func\n")

        pg = ProjectGraph.discover(tmp_path)
        first = DependencyGraph.resolve(pg)
        assert first.sorted_modules == ["A"]

        dependency_graph._PARSED_MODULE_CACHES.clear()

        def fail_dumps(*_args, **_kwargs):
            raise AssertionError("pickle.dumps should not run on a disk cache hit")

        monkeypatch.setattr(dependency_graph.pickle, "dumps", fail_dumps)

        second = DependencyGraph.resolve(pg)
        assert second.sorted_modules == ["A"]

    def test_process_local_memory_cache_replaces_stale_versions(
        self, tmp_path, monkeypatch
    ):
        """In-memory cache should keep only the latest version per source path."""
        monkeypatch.setenv("GENO_CACHE_DIR", str(tmp_path / ".geno-cache"))
        dependency_graph._PARSED_MODULE_CACHES.clear()

        (tmp_path / "geno.toml").write_text('files = ["A"]\n')
        source_file = tmp_path / "A.geno"
        source_file.write_text("func a() -> Int\n  return 1\nend func\n")

        DependencyGraph.resolve(ProjectGraph.discover(tmp_path))
        cache = next(iter(dependency_graph._PARSED_MODULE_CACHES.values()))
        assert len(cache._memory) == 1

        source_file.write_text("func a() -> Int\n  return 2\nend func\n")
        DependencyGraph.resolve(ProjectGraph.discover(tmp_path))
        assert len(cache._memory) == 1

        source_file.write_text("func a() -> Int\n  return 3\nend func\n")
        DependencyGraph.resolve(ProjectGraph.discover(tmp_path))
        assert len(cache._memory) == 1

    def test_process_local_memory_cache_is_not_polluted_by_typechecker(
        self, tmp_path, monkeypatch
    ):
        """Warm resolves should return fresh ASTs even after prior typechecking."""
        monkeypatch.setenv("GENO_CACHE_DIR", str(tmp_path / ".geno-cache"))
        dependency_graph._PARSED_MODULE_CACHES.clear()

        (tmp_path / "geno.toml").write_text('files = ["A"]\n')
        (tmp_path / "A.geno").write_text(
            "func main() -> Int\n  return length([1, 2, 3])\nend func\n"
        )

        pg = ProjectGraph.discover(tmp_path)
        first = DependencyGraph.resolve(pg)
        cold_output = Compiler().compile_project(first)

        TypeChecker().check_project_graph(first)

        second = DependencyGraph.resolve(pg)
        warm_output = Compiler().compile_project(second)

        assert cold_output == warm_output
        assert first.parsed["A"] is not second.parsed["A"]

    def test_cache_invalidates_when_source_changes(self, tmp_path, monkeypatch):
        """Source edits must invalidate cached ASTs instead of serving stale data."""
        monkeypatch.setenv("GENO_CACHE_DIR", str(tmp_path / ".geno-cache"))
        dependency_graph._PARSED_MODULE_CACHES.clear()

        (tmp_path / "geno.toml").write_text('files = ["A"]\n')
        source_file = tmp_path / "A.geno"
        source_file.write_text("func a() -> Int\n  return 1\nend func\n")

        pg = ProjectGraph.discover(tmp_path)
        DependencyGraph.resolve(pg)

        source_file.write_text("func a( -> Int\n  return 1\nend func\n")

        with pytest.raises((ParseError, ParseErrors)):
            DependencyGraph.resolve(ProjectGraph.discover(tmp_path))

    def test_cache_invalidates_same_size_edit_with_preserved_mtime(
        self, tmp_path, monkeypatch
    ):
        """Content hash prevents stale hits when size and mtime are unchanged."""
        monkeypatch.setenv("GENO_CACHE_DIR", str(tmp_path / ".geno-cache"))
        dependency_graph._PARSED_MODULE_CACHES.clear()

        (tmp_path / "geno.toml").write_text('files = ["A"]\n')
        source_file = tmp_path / "A.geno"
        source_file.write_text("func a() -> Int\n  return 1\nend func\n")

        pg = ProjectGraph.discover(tmp_path)
        DependencyGraph.resolve(pg)
        original_stat = source_file.stat()

        source_file.write_text("func a() -> Int\n  return 2\nend func\n")
        os.utime(
            source_file,
            ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
        )

        parse_count = 0
        original_parse = dependency_graph._parse_module

        def counting_parse(path):
            nonlocal parse_count
            parse_count += 1
            return original_parse(path)

        monkeypatch.setattr(dependency_graph, "_parse_module", counting_parse)

        DependencyGraph.resolve(ProjectGraph.discover(tmp_path))
        assert parse_count > 0, "same-size same-mtime edit should reparse"

    def test_cache_invalidates_when_compiler_changes(self, tmp_path, monkeypatch):
        """Compiler/parser changes must invalidate the cache."""
        monkeypatch.setenv("GENO_CACHE_DIR", str(tmp_path / ".geno-cache"))
        dependency_graph._PARSED_MODULE_CACHES.clear()

        (tmp_path / "geno.toml").write_text('files = ["A"]\n')
        (tmp_path / "A.geno").write_text("func a() -> Int\n  return 1\nend func\n")

        pg = ProjectGraph.discover(tmp_path)
        DependencyGraph.resolve(pg)

        # Simulate a compiler change by altering the fingerprint
        monkeypatch.setattr(dependency_graph, "_COMPILER_FINGERPRINT", None)
        monkeypatch.setattr(
            dependency_graph, "_compiler_fingerprint", lambda: "changed"
        )

        parse_count = 0
        original_parse = dependency_graph._parse_module

        def counting_parse(path):
            nonlocal parse_count
            parse_count += 1
            return original_parse(path)

        monkeypatch.setattr(dependency_graph, "_parse_module", counting_parse)

        DependencyGraph.resolve(pg)
        assert parse_count > 0, "cache should miss after compiler fingerprint changes"

    def test_corrupt_cache_file_falls_back_to_reparse(self, tmp_path, monkeypatch):
        """Corrupt or incompatible cache files must degrade to a cache miss."""
        monkeypatch.setenv("GENO_CACHE_DIR", str(tmp_path / ".geno-cache"))
        dependency_graph._PARSED_MODULE_CACHES.clear()

        (tmp_path / "geno.toml").write_text('files = ["A"]\n')
        (tmp_path / "A.geno").write_text("func a() -> Int\n  return 1\nend func\n")

        pg = ProjectGraph.discover(tmp_path)
        DependencyGraph.resolve(pg)

        # Corrupt all cache files on disk
        cache_dir = dependency_graph._dependency_cache_dir()
        assert cache_dir is not None
        for pickle_file in cache_dir.glob("*.pickle"):
            pickle_file.write_bytes(b"corrupted data")

        dependency_graph._PARSED_MODULE_CACHES.clear()

        parse_count = 0
        original_parse = dependency_graph._parse_module

        def counting_parse(path):
            nonlocal parse_count
            parse_count += 1
            return original_parse(path)

        monkeypatch.setattr(dependency_graph, "_parse_module", counting_parse)

        # Should still work — falls back to reparsing
        result = DependencyGraph.resolve(pg)
        assert "A" in result.parsed
        assert parse_count > 0, "corrupt disk cache should fall back to reparsing"

    def test_deeply_nested_program_degrades_to_no_cache(self, tmp_path, monkeypatch):
        """H-08: a valid but very deeply-nested AST (long operator chain) must
        not crash resolution with a RecursionError from cache serialization —
        it degrades to no-cache and resolves normally."""
        monkeypatch.setenv("GENO_CACHE_DIR", str(tmp_path / ".geno-cache"))
        dependency_graph._PARSED_MODULE_CACHES.clear()

        terms = " + ".join(["1"] * 400)
        (tmp_path / "geno.toml").write_text('files = ["A"]\n')
        (tmp_path / "A.geno").write_text(
            f"func main() -> Int\n  return {terms}\nend func\n"
        )

        pg = ProjectGraph.discover(tmp_path)
        # Must not raise RecursionError.
        result = DependencyGraph.resolve(pg)
        assert "A" in result.parsed

    def test_store_failure_is_logged_and_does_not_crash(
        self, tmp_path, monkeypatch, caplog
    ):
        """L-13: a cache write failure must be surfaced (first at WARNING, then
        throttled to DEBUG), not swallowed silently, and must never raise."""
        cache = dependency_graph._ParsedModuleCache(tmp_path / ".geno-cache")
        monkeypatch.setattr(cache, "_cache_path_for", lambda p: tmp_path / "x.pickle")

        def failing_dump(*_a, **_k):
            raise OSError("disk full")

        monkeypatch.setattr(dependency_graph.pickle, "dump", failing_dump)

        with caplog.at_level("DEBUG", logger="geno.dependency_graph"):
            # Neither call may raise.
            cache._store_to_disk(
                dependency_graph.Path("A.geno"), 0, 0, "hash", b"payload", ["B"]
            )
            cache._store_to_disk(
                dependency_graph.Path("A.geno"), 0, 0, "hash", b"payload", ["B"]
            )

        warnings = [
            r
            for r in caplog.records
            if r.levelname == "WARNING" and "failed to write" in r.message
        ]
        debugs = [
            r
            for r in caplog.records
            if r.levelname == "DEBUG" and "failed to write" in r.message
        ]
        assert len(warnings) == 1, "first store failure should log exactly one WARNING"
        assert len(debugs) == 1, "subsequent failures should throttle to DEBUG"
        # The temp file is cleaned up on failure.
        assert not (tmp_path / "x.tmp").exists()

    def test_serialization_bug_is_logged_at_error(self, tmp_path, monkeypatch, caplog):
        """L-13: a pickling TypeError/AttributeError (a real serialization bug)
        is logged at ERROR with a traceback, not masked as a silent miss."""
        cache = dependency_graph._ParsedModuleCache(tmp_path / ".geno-cache")
        monkeypatch.setattr(cache, "_cache_path_for", lambda p: tmp_path / "y.pickle")

        def bad_dump(*_a, **_k):
            raise TypeError("cannot pickle this")

        monkeypatch.setattr(dependency_graph.pickle, "dump", bad_dump)

        with caplog.at_level("ERROR", logger="geno.dependency_graph"):
            cache._store_to_disk(
                dependency_graph.Path("A.geno"), 0, 0, "hash", b"payload", ["B"]
            )

        assert any(
            r.levelname == "ERROR"
            and "failed to serialize" in r.message
            and r.exc_info is not None
            for r in caplog.records
        )

    def test_corrupt_nested_program_payload_falls_back_to_reparse(
        self, tmp_path, monkeypatch
    ):
        """A valid cache entry with a bad nested AST payload should degrade to a miss."""
        monkeypatch.setenv("GENO_CACHE_DIR", str(tmp_path / ".geno-cache"))
        dependency_graph._PARSED_MODULE_CACHES.clear()

        (tmp_path / "geno.toml").write_text('files = ["A"]\n')
        source_path = tmp_path / "A.geno"
        source_path.write_text("func a() -> Int\n  return 1\nend func\n")

        pg = ProjectGraph.discover(tmp_path)
        DependencyGraph.resolve(pg)

        cache_dir = dependency_graph._dependency_cache_dir()
        assert cache_dir is not None
        cache_file = next(cache_dir.glob("*.pickle"))
        entry = dependency_graph._ParsedModuleCacheEntry(
            cache_version=dependency_graph._PARSE_CACHE_VERSION,
            compiler_fingerprint=dependency_graph._get_compiler_fingerprint(),
            source_path=str(source_path.resolve()),
            mtime_ns=source_path.stat().st_mtime_ns,
            size=source_path.stat().st_size,
            source_hash=dependency_graph._source_digest(source_path.read_bytes()),
            program_payload=b"not a pickle payload",
            imports=[],
        )
        with cache_file.open("wb") as handle:
            dependency_graph.pickle.dump(
                entry,
                handle,
                protocol=dependency_graph.pickle.HIGHEST_PROTOCOL,
            )

        dependency_graph._PARSED_MODULE_CACHES.clear()

        parse_count = 0
        original_parse = dependency_graph._parse_module

        def counting_parse(path):
            nonlocal parse_count
            parse_count += 1
            return original_parse(path)

        monkeypatch.setattr(dependency_graph, "_parse_module", counting_parse)

        result = DependencyGraph.resolve(pg)
        assert "A" in result.parsed
        assert parse_count > 0, "bad nested payload should fall back to reparsing"


class TestRestrictedUnpickler:
    """Cache deserialization must reject classes from untrusted modules."""

    def test_restricted_unpickler_blocks_os_system(self):
        import pickle

        from geno.dependency_graph import _safe_loads

        # Craft a pickle payload that would execute os.system
        payload = (
            b"\x80\x03cos\nsystem\nq\x00X\x04\x00\x00\x00echoq\x01\x85q\x02Rq\x03."
        )
        with pytest.raises(pickle.UnpicklingError, match="Blocked"):
            _safe_loads(payload)

    def test_restricted_unpickler_blocks_builtin_eval(self):
        import pickle

        from geno.dependency_graph import _safe_loads

        payload = b"cbuiltins\neval\n(V1+2\ntR."
        with pytest.raises(pickle.UnpicklingError, match="Blocked"):
            _safe_loads(payload)

    def test_restricted_unpickler_allows_parsed_program(self):
        import pickle

        from geno.ast_nodes import FunctionDef, Program, SimpleType
        from geno.dependency_graph import _safe_loads
        from geno.lexer import Lexer
        from geno.parser import Parser
        from geno.tokens import SourceLocation

        source = "func main() -> Int\n  return 1\nend func\n"
        prog = Parser(Lexer(source, "<test>").tokenize()).parse_program()
        payload = pickle.dumps(prog, protocol=3)
        result = _safe_loads(payload)

        assert isinstance(result, Program)
        assert len(result.definitions) == 1
        func = result.definitions[0]
        assert isinstance(func, FunctionDef)
        assert func.name == "main"
        assert isinstance(func.return_type, SimpleType)
        assert func.return_type.name == "Int"
        assert isinstance(func.location, SourceLocation)


class TestCompilerFingerprintCoversParserInputs:
    """_compiler_fingerprint must hash all files that affect parsing."""

    def test_fingerprint_includes_parser_mixins_and_tokens(self):
        from pathlib import Path

        from geno.dependency_graph import _compiler_fingerprint

        pkg_dir = Path(dependency_graph.__file__).resolve().parent
        expected_modules = [
            "parser_base.py",
            "parser_expressions.py",
            "parser_patterns.py",
            "parser_statements.py",
            "parser_types.py",
            "tokens.py",
        ]
        for mod in expected_modules:
            assert (pkg_dir / mod).exists(), f"{mod} should exist in the package"

        original = _compiler_fingerprint()

        import unittest.mock as mock

        real_read = Path.read_bytes

        def fingerprint_with_mocked_file(file_name: str, content: bytes) -> str:
            def patched_read(self):
                if self.name == file_name:
                    return content
                return real_read(self)

            with mock.patch.object(Path, "read_bytes", patched_read):
                return _compiler_fingerprint()

        parser_modified = fingerprint_with_mocked_file(
            "parser_expressions.py", b"modified parser content"
        )
        tokens_modified = fingerprint_with_mocked_file(
            "tokens.py", b"modified token definitions"
        )

        assert original != parser_modified, (
            "fingerprint should change when parser_expressions.py changes"
        )
        assert original != tokens_modified, (
            "fingerprint should change when tokens.py changes"
        )
