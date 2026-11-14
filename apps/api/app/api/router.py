"""Route modules. Routers validate input, delegate, and shape the response."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.routes import chat, health

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(chat.router)
