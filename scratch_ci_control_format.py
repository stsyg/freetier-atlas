"""Scratch control for the CI masking-edge demonstration. Delete with the branch.

Fails `ruff format --check` and NOTHING else: the repository sets
quote-style = "double", and no lint rule in the selected set (E, F, I, UP, B, W)
flags quote style. This control has to isolate the `Ruff format check` edge, so
`ruff check` must stay clean on it.
"""

VALUE = 'single-quoted on purpose so the formatter rewrites it'
