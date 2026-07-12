"""Load the JS runtime prelude, same pattern as runtime_prelude.py."""

import pathlib

_SUPPORT_FILE = pathlib.Path(__file__).parent / "_js_runtime_support.js"
JS_RUNTIME_PRELUDE = _SUPPORT_FILE.read_text(encoding="utf-8")
