"""
全局定时任务调度器 (APScheduler)
"""
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.memory import MemoryJobStore
import logging

logger = logging.getLogger(__name__)

# 使用单例模式或全局变量存储调度器实例
scheduler = AsyncIOScheduler(
    jobstores={"default": MemoryJobStore()},
    timezone="Asia/Shanghai",  # 明确时区
)

def start_scheduler():
    """启动调度器"""
    try:
        if not scheduler.running:
            scheduler.start()
            logger.info("✅ 定时任务调度器已启动")
    except Exception as e:
        logger.error(f"❌ 调度器启动失败: {e}")

def shutdown_scheduler():
    """关闭调度器"""
    try:
        if scheduler.running:
            scheduler.shutdown()
            logger.info("👋 定时任务调度器已关闭")
    except Exception as e:
        logger.error(f"❌ 调度器关闭失败: {e}")
