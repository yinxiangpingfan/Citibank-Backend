"""
健康检查端点
提供 ping 和 health 检查功能
"""
from fastapi import APIRouter
from datetime import datetime
from typing import Dict, Any

router = APIRouter()


@router.get("/ping", summary="Ping 检查", description="简单的 ping 检查,返回 pong")
async def ping() -> Dict[str, str]:
    """
    Ping 端点
    
    Returns:
        Dict[str, str]: 包含 message 字段的响应
    """
    return {"message": "pong"}


@router.get("/health", summary="健康检查", description="详细的健康检查,返回服务状态和时间戳")
async def health_check() -> Dict[str, Any]:
    """
    健康检查端点
    
    Returns:
        Dict[str, Any]: 包含状态、时间戳和服务信息的响应
    """
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "service": "Citibank Backend API",
        "version": "0.1.0"
    }
