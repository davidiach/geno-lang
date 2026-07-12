"""Tests for the package index (search and resolve)."""

import pytest

import geno.package_index as package_index
from geno.package_index import resolve, search
from geno.target_profile import VALID_TARGETS


@pytest.fixture(autouse=True)
def clear_index_cache():
    package_index._INDEX_CACHE = None
    yield
    package_index._INDEX_CACHE = None


SAMPLE_PACKAGES = [
    {
        "name": "geno-http",
        "description": "HTTP client and server utilities for Geno",
        "git": "https://example.com/geno-http.git",
        "latest_version": "0.1.0",
        "tag": "v0.1.0",
        "targets": ["python-cli", "node-cli"],
    },
    {
        "name": "geno-json",
        "description": "JSON parsing and serialization",
        "git": "https://example.com/geno-json.git",
        "latest_version": "0.1.0",
        "tag": "v0.1.0",
        "targets": ["python-cli", "node-cli", "browser"],
    },
]


def test_shipped_index_lists_only_published_packages():
    assert package_index._load_index() == []


class UsesSampleIndex:
    @pytest.fixture(autouse=True)
    def use_sample_index(self):
        package_index._INDEX_CACHE = [dict(package) for package in SAMPLE_PACKAGES]


class TestSearch(UsesSampleIndex):
    def test_search_by_name(self):
        results = search("http")
        assert len(results) >= 1
        names = [r["name"] for r in results]
        assert "geno-http" in names

    def test_search_by_description(self):
        results = search("parsing")
        assert len(results) >= 1

    def test_search_case_insensitive(self):
        results = search("JSON")
        assert len(results) >= 1
        assert any(r["name"] == "geno-json" for r in results)

    def test_search_no_results(self):
        results = search("nonexistent-package-xyz-12345")
        assert results == []

    def test_search_returns_latest_version(self):
        results = search("geno-http")
        pkg = results[0]
        assert "latest_version" in pkg
        assert pkg["latest_version"] == "0.1.0"
        assert pkg["tag"] == "v0.1.0"

    def test_search_returns_targets(self):
        results = search("geno-json")
        pkg = next(r for r in results if r["name"] == "geno-json")
        assert "targets" in pkg
        assert "browser" in pkg["targets"]


class TestResolve(UsesSampleIndex):
    def test_resolve_existing_package(self):
        pkg = resolve("geno-http")
        assert pkg is not None
        assert pkg["name"] == "geno-http"
        assert "git" in pkg

    def test_resolve_nonexistent_package(self):
        pkg = resolve("nonexistent-package-xyz")
        assert pkg is None

    def test_resolve_returns_git_url(self):
        pkg = resolve("geno-json")
        assert pkg is not None
        assert pkg["git"].endswith(".git")
        assert pkg["tag"] == "v0.1.0"

    def test_all_packages_have_required_fields(self):
        """Every package in the index has name, description, git, latest_version, tag, targets."""
        from geno.package_index import _load_index

        packages = _load_index()
        for pkg in packages:
            assert "name" in pkg, f"Missing 'name' in {pkg}"
            assert "description" in pkg, f"Missing 'description' in {pkg}"
            assert "git" in pkg, f"Missing 'git' in {pkg}"
            assert "latest_version" in pkg, f"Missing 'latest_version' in {pkg}"
            assert "tag" in pkg, f"Missing 'tag' in {pkg}"
            assert "targets" in pkg, f"Missing 'targets' in {pkg}"
            assert isinstance(pkg["tag"], str)
            assert isinstance(pkg["targets"], list)
            assert set(pkg["targets"]) <= VALID_TARGETS


class TestLoadIndexResilience:
    def _patch_index_contents(self, monkeypatch, contents: str):
        package_path = (
            package_index.Path(package_index.__file__).parent / "packages.json"
        )
        original_read_text = package_index.Path.read_text

        def fake_read_text(self, *args, **kwargs):
            if self == package_path:
                return contents
            return original_read_text(self, *args, **kwargs)

        monkeypatch.setattr(package_index.Path, "read_text", fake_read_text)

    def test_load_index_is_cached_after_first_read(self, monkeypatch):
        package_path = (
            package_index.Path(package_index.__file__).parent / "packages.json"
        )
        original_read_text = package_index.Path.read_text
        read_count = 0

        def fake_read_text(self, *args, **kwargs):
            nonlocal read_count
            if self == package_path:
                read_count += 1
            return original_read_text(self, *args, **kwargs)

        monkeypatch.setattr(package_index.Path, "read_text", fake_read_text)

        assert search("http") == []
        assert resolve("geno-json") is None
        assert read_count == 1

    def test_non_list_packages_field_returns_empty_index(self, monkeypatch):
        self._patch_index_contents(monkeypatch, '{"packages": {"broken": true}}')

        assert search("geno") == []
        assert resolve("geno-http") is None

    def test_invalid_entries_are_ignored(self, monkeypatch):
        self._patch_index_contents(
            monkeypatch,
            """
            {
              "packages": [
                "broken",
                {"name": "missing-git", "description": "No git", "latest_version": "0.1.0", "targets": []},
                {"name": "../bad", "description": "Bad name", "git": "https://example.com/bad.git", "latest_version": "0.1.0", "targets": []},
                {"name": "bad-url", "description": "Bad URL", "git": "not a url", "latest_version": "0.1.0", "targets": []},
                {"name": "bad-targets", "description": "Bad targets", "git": "https://example.com/bad-targets.git", "latest_version": "0.1.0", "targets": "python-cli"},
                {"name": "unknown-target", "description": "Bad target", "git": "https://example.com/unknown-target.git", "latest_version": "0.1.0", "tag": "v0.1.0", "targets": ["browzer"]},
                {"name": "missing-tag", "description": "No tag", "git": "https://example.com/missing-tag.git", "latest_version": "0.1.0", "targets": []},
                {"name": "bad-tag", "description": "Bad tag", "git": "https://example.com/bad-tag.git", "latest_version": "0.1.0", "tag": "../bad", "targets": []},
                {"name": "non-string-tag", "description": "Bad tag", "git": "https://example.com/non-string-tag.git", "latest_version": "0.1.0", "tag": 123, "targets": []},
                {
                  "name": "geno-good",
                  "description": "A valid package entry",
                  "git": "https://example.com/geno-good.git",
                  "latest_version": "0.1.0",
                  "tag": "v0.1.0",
                  "targets": ["python-cli"]
                }
              ]
            }
            """,
        )

        results = search("geno-good")
        assert len(results) == 1
        assert results[0]["name"] == "geno-good"
        assert results[0]["tag"] == "v0.1.0"
        assert resolve("geno-good") is not None
