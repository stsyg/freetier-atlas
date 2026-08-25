"""Scratch control for the CI masking-edge demonstration. Delete with the branch.

Fails `ruff check` with F401 (unused import) and NOTHING else. Deliberately
formatted correctly so `ruff format --check` PASSES on it: this control has to
isolate the `Ruff lint` edge, so it must not also trip the formatter.
"""

import os
