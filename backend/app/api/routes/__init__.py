"""API routes package for The Herald."""
from app.api.routes.admin import router as admin_router
from app.api.routes.events import router as events_router
from app.api.routes.notifications import router as notifications_router
from app.api.routes.subscribers import router as subscribers_router

__all__ = ["events_router", "subscribers_router", "notifications_router", "admin_router"]
