"""
API v1 版本路由
"""
from fastapi import APIRouter
from app.api.v1.endpoints import health, auth, translator, market

api_router = APIRouter()

# 注册健康检查路由
api_router.include_router(health.router, tags=["健康检查"])
api_router.include_router(auth.router, prefix="/auth", tags=["认证"])
api_router.include_router(translator.router, prefix="/translator", tags=["Translator"])
api_router.include_router(market.router, prefix="/market", tags=["Market"])
