"""
FastAPI 应用主入口
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.api.v1 import api_router

# 创建 FastAPI 应用实例
app = FastAPI(
    title=settings.APP_NAME,
    description=settings.APP_DESCRIPTION,
    version=settings.APP_VERSION,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json"
)

# 配置 CORS 中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=settings.CORS_ALLOW_CREDENTIALS,
    allow_methods=settings.CORS_ALLOW_METHODS,
    allow_headers=settings.CORS_ALLOW_HEADERS,
)

# 注册 API 路由
app.include_router(api_router, prefix=settings.API_V1_PREFIX)


@app.get("/", tags=["根路径"])
async def root():
    """
    根路径端点
    
    Returns:
        dict: 欢迎信息和 API 文档链接
    """
    return {
        "message": "欢迎使用 Citibank Backend API",
        "version": settings.APP_VERSION,
        "docs": "/docs",
        "health": f"{settings.API_V1_PREFIX}/health",
        "ping": f"{settings.API_V1_PREFIX}/ping"
    }


@app.on_event("startup")
async def startup_event():
    """应用启动事件"""
    import asyncio
    from app.db.session import engine
    from app.db.base import Base
    from app.models import user
    from app.models import market  # noqa: F401 确保市场数据表被创建
    
    # 数据库连接重试（Docker 启动时 MySQL 可能还未就绪）
    max_retries = 5
    for attempt in range(1, max_retries + 1):
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            print(f"✅ 数据库连接成功")
            break
        except Exception as e:
            if attempt == max_retries:
                print(f"❌ 数据库连接失败，已重试 {max_retries} 次: {e}")
                raise
            print(f"⏳ 数据库连接失败 (第 {attempt}/{max_retries} 次)，{3}秒后重试...")
            await asyncio.sleep(3)
        
    print(f"🔍 ReDoc 文档: http://{settings.HOST}:{settings.PORT}/redoc")

    # 启动定时任务调度器
    from app.core.scheduler import start_scheduler, scheduler, shutdown_scheduler
    from app.tasks.driver_sync import sync_market_drivers_task
    from app.tasks.regime_sync import sync_market_regime_task
    from app.tasks.event_sync import sync_market_events_task

    # 注册每日 01:00 执行驱动因素分析任务
    scheduler.add_job(
        sync_market_drivers_task,
        "cron",
        hour=1,
        minute=0,
        id="sync_market_drivers",
        replace_existing=True,
    )

    # 注册每日 01:10 执行状态机制分析任务
    scheduler.add_job(
        sync_market_regime_task,
        "cron",
        hour=1,
        minute=10,
        id="sync_market_regime",
        replace_existing=True,
    )

    # 注册每日 01:20 执行市场事件分析任务
    scheduler.add_job(
        sync_market_events_task,
        "cron",
        hour=1,
        minute=20,
        id="sync_market_events",
        replace_existing=True,
    )
    start_scheduler()


@app.on_event("shutdown")
async def shutdown_event():
    """应用关闭事件"""
    from app.core.scheduler import shutdown_scheduler
    shutdown_scheduler()
    print(f"👋 {settings.APP_NAME} 已关闭")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG
    )
