"""
Market 数据同步定时任务

每天自动从 yfinance 拉取前一交易日的 WTI 和 Brent 价格数据并写入 MySQL。
"""
import logging
from datetime import date, timedelta
from decimal import Decimal

import yfinance as yf
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal
from app.models.market import MarketDailyPrice, MarketType

logger = logging.getLogger(__name__)

MARKET_TICKERS = {
    "WTI": "CL=F",
    "Brent": "BZ=F",
}


async def sync_daily_prices():
    """
    同步所有市场的每日价格数据。
    通常在美股收盘后执行（北京时间早上 5:30）。
    """
    logger.info("🔄 开始同步市场数据...")

    async with AsyncSessionLocal() as session:
        for market_name, ticker_symbol in MARKET_TICKERS.items():
            try:
                await _sync_single_market(session, market_name, ticker_symbol)
            except Exception as e:
                logger.error(f"❌ {market_name} 同步失败: {e}")

    logger.info("✅ 市场数据同步完成")


async def sync_historical_data(days: int = 365):
    """
    同步历史数据（首次初始化或补数据时使用）
    """
    logger.info(f"🔄 开始同步 {days} 天历史数据...")

    async with AsyncSessionLocal() as session:
        for market_name, ticker_symbol in MARKET_TICKERS.items():
            try:
                end_date = date.today()
                start_date = end_date - timedelta(days=days + 10)

                ticker = yf.Ticker(ticker_symbol)
                df = ticker.history(
                    start=start_date.isoformat(),
                    end=(end_date + timedelta(days=1)).isoformat(),
                )

                if df.empty:
                    logger.warning(f"{market_name}: yfinance 返回空数据")
                    continue

                market_enum = MarketType(market_name)

                # 获取已存在的日期
                existing_query = (
                    select(MarketDailyPrice.trade_date)
                    .where(MarketDailyPrice.market == market_enum)
                )
                result = await session.execute(existing_query)
                existing_dates = {row[0] for row in result.fetchall()}

                new_records = []
                for idx, row in df.iterrows():
                    trade_date = idx.date() if hasattr(idx, "date") else idx
                    if trade_date in existing_dates:
                        continue

                    record = MarketDailyPrice(
                        market=market_enum,
                        trade_date=trade_date,
                        open_price=Decimal(str(round(row["Open"], 2))),
                        high_price=Decimal(str(round(row["High"], 2))),
                        low_price=Decimal(str(round(row["Low"], 2))),
                        close_price=Decimal(str(round(row["Close"], 2))),
                        volume=int(row["Volume"]) if row["Volume"] > 0 else None,
                        front_month_price=Decimal(str(round(row["Close"], 2))),
                        second_month_price=None,
                    )
                    new_records.append(record)

                if new_records:
                    session.add_all(new_records)
                    await session.commit()
                    logger.info(f"✅ {market_name}: 写入 {len(new_records)} 条历史数据")
                else:
                    logger.info(f"ℹ️ {market_name}: 无新数据")

            except Exception as e:
                await session.rollback()
                logger.error(f"❌ {market_name} 历史数据同步失败: {e}")


async def _sync_single_market(
    session: AsyncSession,
    market_name: str,
    ticker_symbol: str,
):
    """同步单个市场的前一交易日数据"""
    yesterday = date.today() - timedelta(days=1)
    start = yesterday - timedelta(days=5)  # 多取几天兜底

    ticker = yf.Ticker(ticker_symbol)
    df = ticker.history(
        start=start.isoformat(),
        end=(date.today() + timedelta(days=1)).isoformat(),
    )

    if df.empty:
        logger.warning(f"{market_name}: yfinance 返回空数据")
        return

    market_enum = MarketType(market_name)

    # 获取已存在的日期
    existing_query = (
        select(MarketDailyPrice.trade_date)
        .where(MarketDailyPrice.market == market_enum)
        .where(MarketDailyPrice.trade_date >= start)
    )
    result = await session.execute(existing_query)
    existing_dates = {row[0] for row in result.fetchall()}

    new_records = []
    for idx, row in df.iterrows():
        trade_date = idx.date() if hasattr(idx, "date") else idx
        if trade_date in existing_dates:
            continue

        record = MarketDailyPrice(
            market=market_enum,
            trade_date=trade_date,
            open_price=Decimal(str(round(row["Open"], 2))),
            high_price=Decimal(str(round(row["High"], 2))),
            low_price=Decimal(str(round(row["Low"], 2))),
            close_price=Decimal(str(round(row["Close"], 2))),
            volume=int(row["Volume"]) if row["Volume"] > 0 else None,
            front_month_price=Decimal(str(round(row["Close"], 2))),
            second_month_price=None,
        )
        new_records.append(record)

    if new_records:
        session.add_all(new_records)
        await session.commit()
        logger.info(f"✅ {market_name}: 写入 {len(new_records)} 条数据")
    else:
        logger.info(f"ℹ️ {market_name}: 数据已是最新")
