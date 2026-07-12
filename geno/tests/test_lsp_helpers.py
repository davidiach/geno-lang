from types import SimpleNamespace

from geno.lsp_server import GenoLanguageServer, _extract_user_func_sig


def test_full_document_change_detection_without_pygls_dependency():
    full_change = SimpleNamespace(text="whole document")
    incremental_change = SimpleNamespace(range=object(), text="fragment")

    assert GenoLanguageServer._is_full_document_change(full_change)
    assert not GenoLanguageServer._is_full_document_change(incremental_change)


def test_extract_user_func_sig_async_with_effects_without_pygls_dependency():
    source = (
        "async func fetch(url: String) -> String with http\n  return url\nend func\n"
    )

    result = _extract_user_func_sig("fetch", source)

    assert result is not None
    label, params = result
    assert label == "fetch(url: String) -> String with http"
    assert [param.label for param in params] == ["url: String"]


def test_extract_user_func_sig_exported_untested_async_with_effects():
    source = (
        'export @untested("network") async func fetch(url: String) -> String with http\n'
        "  return url\n"
        "end func\n"
    )

    result = _extract_user_func_sig("fetch", source)

    assert result is not None
    label, params = result
    assert label == "fetch(url: String) -> String with http"
    assert [param.label for param in params] == ["url: String"]
