"""Allow ``python3 -m geno.tests.fuzzing`` invocation."""

import sys

from .cli import main

sys.exit(main())
