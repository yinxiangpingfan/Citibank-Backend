"""
市场事件分析定时任务
每天凌晨 01:20 执行，生成并保存当天的市场事件分析。
"""
import logging
from datetime import date
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal
from app.core.redis import RedisClient
from app.services.market.event_service import generate_and_save_events

logger = logging.getLogger(__name__)

MARKETS = ["WTI", "Brent"]


async def sync_market_events_task():
    """
    定时任务：生成所有市场的每日事件分析并入库
    """
    logger.info("⏰ 开始执行每日市场事件分析任务...")
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
                logger.info(f"🔄 正在分析 {market} 市场事件 - {target_date} ...")
                await generate_and_save_events(
                    market=market,
                    target_date=target_date,
                    window_days=7,
                    db=db,
                    redis_client=redis_client
                )
                logger.info(f"✅ {market} 市场事件分析完成")
            except Exception as e:
                logger.error(f"❌ {market} 市场事件分析失败: {e}")

    logger.info("🏁 每日市场事件分析任务结束")
