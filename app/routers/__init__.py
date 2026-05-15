from app.routers.activity import router as activity_router
from app.routers.config import router as config_router
from app.routers.destinations import router as destinations_router
from app.routers.rules import router as rules_router
from app.routers.transmission import router as transmission_router

__all__ = [
    "activity_router",
    "config_router",
    "destinations_router",
    "rules_router",
    "transmission_router",
]
