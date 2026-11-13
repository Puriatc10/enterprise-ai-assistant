"""Test package.

Present so ``tests.conftest`` is importable as a module by test files; without it
pytest inserts ``tests/`` itself on ``sys.path`` and the package-style import
fails.
"""
