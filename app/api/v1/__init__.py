"""
API v1 版本路由
"""
from fastapi import APIRouter
from app.api.v1.endpoints import health

api_router = APIRouter()

# 注册健康检查路由
api_router.include_router(health.router, tags=["健康检查"])
