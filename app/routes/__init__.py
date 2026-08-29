from .applications import router as applications_router
from .dashboard import router as dashboard_router
from .guide import router as guide_router
from .profile import router as profile_router

__all__ = ["applications_router", "dashboard_router", "guide_router", "profile_router"]
