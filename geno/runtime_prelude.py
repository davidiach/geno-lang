"""
Geno Runtime Prelude
=======================

Python runtime support code injected into compiled Geno output.
Loads the prelude from _runtime_support.py so the code is lintable.
"""

import pathlib

_SUPPORT_FILE = pathlib.Path(__file__).parent / "_runtime_support.py"
RUNTIME_PRELUDE = _SUPPORT_FILE.read_text(encoding="utf-8")
