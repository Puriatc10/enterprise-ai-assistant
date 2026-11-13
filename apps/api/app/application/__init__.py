"""Application layer: use cases that coordinate domain ports.

Depends on ``app.domain`` only. Adapters arrive by constructor injection from the
composition root (``app.core.dependencies``); this layer never imports
``app.infrastructure``.
"""
