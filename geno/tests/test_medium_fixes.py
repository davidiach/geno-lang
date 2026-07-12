"""Regression tests for miscellaneous Medium production-readiness fixes.

Covers the smaller, cross-cutting fixes that don't have a natural home in a
per-feature test module: output-write error boundary (M-08), LSP logging
enablement (M-14), and corrupt-manifest observability (M-15).
"""

from __future__ import annotations

import logging

import pytest


class TestWriteTextOutput:
    """M-08: output-write failures must be reported cleanly, never as a raw
    traceback or mislabeled as an input 'File not found'."""

    def test_write_failure_exits_with_clean_message(self, tmp_path, capsys):
        from geno.cli._util import write_text_output

        # A path whose parent directory does not exist -> OSError on open.
        target = tmp_path / "does_not_exist" / "out.js"
        with pytest.raises(SystemExit) as exc_info:
            write_text_output(str(target), "content")
        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert "cannot write output file" in err
        assert "Traceback" not in err

    def test_write_success_writes_content(self, tmp_path):
        from geno.cli._util import write_text_output

        target = tmp_path / "out.js"
        write_text_output(str(target), "hello")
        assert target.read_text(encoding="utf-8") == "hello"


class TestLspLoggingEnablement:
    """M-14: `geno lsp` must configure logging so internal-failure records are
    reachable (they were logged only at DEBUG with no handler)."""

    def test_configure_adds_stderr_handler_at_env_level(self, monkeypatch):
        import geno.lsp_server as lsp_server

        geno_logger = logging.getLogger("geno")
        # Remove any handler our helper added in a prior test.
        for h in list(geno_logger.handlers):
            if h.get_name() == lsp_server._LSP_LOG_HANDLER_NAME:
                geno_logger.removeHandler(h)

        monkeypatch.setenv("GENO_LSP_LOG_LEVEL", "DEBUG")
        lsp_server._configure_lsp_logging()

        handlers = [
            h
            for h in geno_logger.handlers
            if h.get_name() == lsp_server._LSP_LOG_HANDLER_NAME
        ]
        assert len(handlers) == 1
        assert geno_logger.level == logging.DEBUG

    def test_configure_is_idempotent(self, monkeypatch):
        import geno.lsp_server as lsp_server

        geno_logger = logging.getLogger("geno")
        for h in list(geno_logger.handlers):
            if h.get_name() == lsp_server._LSP_LOG_HANDLER_NAME:
                geno_logger.removeHandler(h)

        monkeypatch.setenv("GENO_LSP_LOG_LEVEL", "WARNING")
        lsp_server._configure_lsp_logging()
        lsp_server._configure_lsp_logging()
        handlers = [
            h
            for h in geno_logger.handlers
            if h.get_name() == lsp_server._LSP_LOG_HANDLER_NAME
        ]
        assert len(handlers) == 1


class TestCorruptManifestObservability:
    """M-15: a corrupt dependency manifest must surface at WARNING (visible via
    Python's default handler) rather than being swallowed at DEBUG."""

    def test_project_graph_warns_on_corrupt_manifest(self, tmp_path, caplog):
        import geno.project_graph as project_graph

        dep_dir = tmp_path / "dep"
        dep_dir.mkdir()
        (dep_dir / "geno.toml").write_text("this is = not valid toml [[[\n")

        with caplog.at_level("WARNING", logger=project_graph._logger.name):
            result = project_graph._parse_dependency_manifest(dep_dir)
        assert result is None
        assert any(
            "Failed to parse dependency manifest" in rec.message
            for rec in caplog.records
        )
