"""Composition root: the one place that decides which concrete adapter is live.

Phase 0 wires settings only. Phase 2 adds providers for the LLM port and the
persistence repository; Phase 3 adds the tool registry; Phase 4 adds connectors.
Keeping this file as the single wiring point is what allows routes, services and
orchestration to depend on protocols instead of implementations.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from app.core.config import Settings, get_settings


def provide_settings(request: Request) -> Settings:
    """FastAPI dependency returning the settings for *this* application instance.

    The application's own settings win over the module-level cache: an app built
    with explicit settings (``create_app(settings)`` in tests or scripts) must
    behave the way it was told to, not the way the process environment happens to
    look. Falls back to the environment-derived settings when no instance value
    is attached.
    """
    instance_settings = getattr(request.app.state, "settings", None)
    if isinstance(instance_settings, Settings):
        return instance_settings
    return get_settings()


#: Annotated dependency type for handlers: ``settings: SettingsDep``.
SettingsDep = Annotated[Settings, Depends(provide_settings)]
