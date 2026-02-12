"""
Market Service - 市场快照业务逻辑

职责:
1. 从 MySQL 获取历史价格数据
2. 数据不足时从 yfinance 补充并持久化
3. 计算快照指标（价格变化、波动率、期限结构）
4. 使用 Redis 缓存完整响应
"""
import json
import math
import logging
from datetime import datetime, date, timedelta
from typing import Optional, List, Tuple
from decimal import Decimal

import yfinance as yf
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.models.market import MarketDailyPrice, MarketType
from app.schemas.market import (
    MarketSnapshotResponse,
    TermStructure,
    TermStructureState,
    PricePoint,
)

logger = logging.getLogger(__name__)

# yfinance ticker 映射
MARKET_TICKERS = {
    "WTI": "CL=F",    # WTI Crude Oil Futures
    "Brent": "BZ=F",  # Brent Crude Oil Futures
}

# 数据需要的最少天数
MIN_DAYS_FOR_VOLATILITY = 20
HISTORY_FETCH_DAYS = 60
HISTORY_DISPLAY_DAYS = 30


async def get_market_snapshot(
    market: str,
    as_of: Optional[datetime],
    db: AsyncSession,
    redis_client=None,
) -> MarketSnapshotResponse:
    """
    获取市场快照数据

    优先级: Redis 缓存 -> MySQL -> yfinance（补数据并入库）
    """
    target_date = as_of.date() if as_of else date.today()
    cache_key = f"market:snapshot:{market}:{target_date.isoformat()}"

    # ── 1. 查询 Redis 缓存 ──
    if redis_client:
        try:
            cached = await redis_client.get(cache_key)
            if cached:
                logger.info(f"Redis 缓存命中: {cache_key}")
                data = json.loads(cached)
                return MarketSnapshotResponse(**data)
        except Exception as e:
            logger.warning(f"Redis 读取失败，降级跳过: {e}")

    # ── 2. 从 MySQL 获取历史数据 ──
    prices = await _get_prices_from_db(db, market, HISTORY_FETCH_DAYS, target_date)

    # ── 3. 数据不足时从 yfinance 补充 ──
    if len(prices) < MIN_DAYS_FOR_VOLATILITY:
        logger.info(f"MySQL 数据不足 ({len(prices)} 条)，从 yfinance 补充")
        await _sync_from_yfinance(db, market, HISTORY_FETCH_DAYS, target_date)
        prices = await _get_prices_from_db(db, market, HISTORY_FETCH_DAYS, target_date)

    if len(prices) < 2:
        raise ValueError(f"无法获取 {market} 的足够价格数据")

    # ── 4. 计算快照指标 ──
    # 按日期升序排列
    prices_asc = list(reversed(prices))

    latest = prices_asc[-1]
    previous = prices_asc[-2]

    last_price = float(latest.close_price)
    prev_close = float(previous.close_price)
    change_1d = round(last_price - prev_close, 2)
    pct_change_1d = round((change_1d / prev_close) * 100, 2) if prev_close != 0 else 0.0

    # 20 日波动率
    volatility_20d = _calculate_volatility(prices_asc)

    # 期限结构
    term_structure = _calculate_term_structure(latest)

    # 历史价格（最近 30 个交易日）
    history_prices = prices_asc[-HISTORY_DISPLAY_DAYS:]
    history = [
        PricePoint(
            ts=datetime.combine(p.trade_date, datetime.min.time()),
            value=float(p.close_price),
        )
        for p in history_prices
    ]

    snapshot = MarketSnapshotResponse(
        market=market,
        asOf=as_of or datetime.utcnow(),
        lastPrice=last_price,
        change1d=change_1d,
        pctChange1d=pct_change_1d,
        volatility20d=round(volatility_20d, 4),
        termStructure=term_structure,
        history=history,
    )

    # ── 5. 写入 Redis 缓存 ──
    if redis_client:
        try:
            ttl = 300 if target_date == date.today() else 86400
            await redis_client.setex(
                cache_key,
                ttl,
                snapshot.model_dump_json(),
            )
            logger.info(f"Redis 缓存写入: {cache_key} (TTL={ttl}s)")
        except Exception as e:
            logger.warning(f"Redis 写入失败: {e}")

    return snapshot


# ────────────────── 私有辅助函数 ──────────────────


async def _get_prices_from_db(
    db: AsyncSession,
    market: str,
    days: int,
    end_date: date,
) -> List[MarketDailyPrice]:
    """从 MySQL 获取历史价格，按日期降序"""
    market_enum = MarketType(market)
    query = (
        select(MarketDailyPrice)
        .where(MarketDailyPrice.market == market_enum)
        .where(MarketDailyPrice.trade_date <= end_date)
        .order_by(MarketDailyPrice.trade_date.desc())
        .limit(days)
    )
    result = await db.execute(query)
    return result.scalars().all()


async def _sync_from_yfinance(
    db: AsyncSession,
    market: str,
    days: int,
    end_date: date,
) -> None:
    """从 yfinance 拉取数据并写入 MySQL"""
    ticker_symbol = MARKET_TICKERS.get(market)
    if not ticker_symbol:
        raise ValueError(f"不支持的市场类型: {market}")

    start_date = end_date - timedelta(days=days + 10)  # 多取一些以覆盖非交易日

    logger.info(f"从 yfinance 获取 {market} ({ticker_symbol}) 数据: {start_date} ~ {end_date}")

    try:
        ticker = yf.Ticker(ticker_symbol)
        df = ticker.history(start=start_date.isoformat(), end=(end_date + timedelta(days=1)).isoformat())

        if df.empty:
            logger.warning(f"yfinance 返回空数据: {market}")
            return

        market_enum = MarketType(market)

        # 获取已存在的日期，避免重复插入
        existing_query = (
            select(MarketDailyPrice.trade_date)
            .where(MarketDailyPrice.market == market_enum)
            .where(MarketDailyPrice.trade_date >= start_date)
            .where(MarketDailyPrice.trade_date <= end_date)
        )
        result = await db.execute(existing_query)
        existing_dates = {row[0] for row in result.fetchall()}

        new_records = []
        for idx, row in df.iterrows():
            trade_date = idx.date() if hasattr(idx, 'date') else idx
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
            db.add_all(new_records)
            await db.commit()
            logger.info(f"写入 {len(new_records)} 条 {market} 数据到 MySQL")
        else:
            logger.info(f"无新数据需要写入 {market}")

    except Exception as e:
        await db.rollback()
        logger.error(f"yfinance 数据同步失败: {e}")
        raise


def _calculate_volatility(prices_asc: List[MarketDailyPrice]) -> float:
    """
    计算 20 日年化波动率

    公式: 日收益率标准差 × √252
    """
    close_prices = [float(p.close_price) for p in prices_asc]

    if len(close_prices) < MIN_DAYS_FOR_VOLATILITY + 1:
        # 数据不足 20 天，用现有数据计算
        n = len(close_prices)
        if n < 2:
            return 0.0
    else:
        # 取最近 21 天数据（20 个收益率）
        close_prices = close_prices[-(MIN_DAYS_FOR_VOLATILITY + 1):]

    # 计算日收益率
    returns = []
    for i in range(1, len(close_prices)):
        if close_prices[i - 1] != 0:
            daily_return = (close_prices[i] - close_prices[i - 1]) / close_prices[i - 1]
            returns.append(daily_return)

    if not returns:
        return 0.0

    # 标准差
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / len(returns)
    std_dev = math.sqrt(variance)

    # 年化: × √252
    annualized = std_dev * math.sqrt(252)
    return annualized


def _calculate_term_structure(latest: MarketDailyPrice) -> TermStructure:
    """计算期限结构"""
    front = float(latest.front_month_price) if latest.front_month_price else float(latest.close_price)
    second = float(latest.second_month_price) if latest.second_month_price else front * 0.99

    spread = round(front - second, 2)

    if abs(spread) < 0.05:
        state = TermStructureState.FLAT
    elif spread > 0:
        state = TermStructureState.BACKWARDATION
    else:
        state = TermStructureState.CONTANGO

    return TermStructure(state=state, spreadFrontSecond=spread)
