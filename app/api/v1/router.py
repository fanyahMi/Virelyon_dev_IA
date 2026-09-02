from fastapi import APIRouter

from app.api.v1.endpoints import apex, ares, builder, costs, sourcing

api_router = APIRouter()
api_router.include_router(ares.router)
api_router.include_router(apex.router)
api_router.include_router(builder.router)
api_router.include_router(sourcing.router)
api_router.include_router(costs.router)
