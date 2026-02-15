"""
市场驱动因素分析定时任务
每天凌晨 01:00 执行，生成并保存当天的驱动因素分析。
"""
import logging
from datetime import date
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal
from app.core.redis import RedisClient
from app.services.market.driver_service import generate_and_save_drivers

logger = logging.getLogger(__name__)

MARKETS = ["WTI", "Brent"]

async def sync_market_drivers_task():
    """
    定时任务：生成所有市场的每日驱动因素分析并入库
    """
    logger.info("⏰ 开始执行每日市场驱动因素分析任务...")
    target_date = date.today()

    # 获取 Redis 客户端实例
    try:
        redis_client = RedisClient.get_instance()
    except Exception as e:
        logger.error(f"❌ 获取 Redis 客户端失败: {e}")
        redis_client = None

    async with AsyncSessionLocal() as db:
        for market in MARKETS:
            try:
                logger.info(f"🔄正在分析 {market} - {target_date} ...")
                await generate_and_save_drivers(
                    market=market,
                    target_date=target_date,
                    db=db,
                    redis_client=redis_client
                )
                logger.info(f"✅ {market} 分析完成")
            except Exception as e:
                logger.error(f"❌ {market} 分析失败: {e}")

    logger.info("🏁 每日市场驱动因素分析任务结束")
