"""
Market 路由端点
"""
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.core.redis import get_redis
from app.core.deps import get_current_user
from app.schemas.market import MarketSnapshotResponse, DriverAttributionResponse, RegimeStateResponse, EventsResponse
from app.services.market import get_market_snapshot, get_market_drivers, get_market_regime, get_market_events

router = APIRouter()


@router.get("/snapshot", response_model=MarketSnapshotResponse)
async def market_snapshot(
    market: str = Query(
        ...,
        description="参考原油基准 / Reference crude benchmark",
        enum=["WTI", "Brent"],
    ),
    asOf: Optional[datetime] = Query(
        None,
        description="ISO-8601 时间戳，用于查询历史某一时刻的数据。省略则返回最新数据。",
    ),
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
    current_user: str = Depends(get_current_user),
):
    """
    获取市场快照数据 / Get market snapshot

    返回指定原油市场（WTI 或 Brent）的实时快照数据，
    包括最新价格、日变化、波动率和期限结构等信息。
    """
    try:
        return await get_market_snapshot(
            market=market,
            as_of=asOf,
            db=db,
            redis_client=redis,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail=f"获取市场数据失败: {str(e)}"
        )


@router.get("/drivers", response_model=DriverAttributionResponse)
async def market_drivers(
    market: str = Query(
        ...,
        description="参考原油基准 / Reference crude benchmark",
        enum=["WTI", "Brent"],
    ),
    asOf: Optional[datetime] = Query(
        None,
        description="ISO-8601 时间戳，用于查询历史某一时刻的数据。省略则返回最新数据。",
    ),
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
    current_user: str = Depends(get_current_user),
):
    """
    获取市场驱动因素分析 / Get factor attribution (drivers)

    返回影响原油价格的各类驱动因素及其贡献度分析，
    包括供应、需求、宏观金融、外汇和事件等因素。
    通过联网搜索获取最新市场信息进行分析。
    """
    try:
        return await get_market_drivers(
            market=market,
            as_of=asOf,
            db=db,
            redis_client=redis,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail=f"获取驱动因素分析失败: {str(e)}"
        )


@router.get("/regime", response_model=RegimeStateResponse)
async def market_regime(
    market: str = Query(
        ...,
        description="参考原油基准 / Reference crude benchmark",
        enum=["WTI", "Brent"],
    ),
    asOf: Optional[datetime] = Query(
        None,
        description="ISO-8601 时间戳，用于查询历史某一时刻的数据。省略则返回最新数据。",
    ),
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
    current_user: str = Depends(get_current_user),
):
    """
    获取市场状态机制 / Get market regime state

    返回当前原油市场的状态机制分析，包括驱动类型（需求驱动/供应驱动/事件驱动等）
    及其稳定性评估，帮助理解当前市场的主导力量。
    通过联网搜索获取最新市场信息进行分析。
    """
    try:
        return await get_market_regime(
            market=market,
            as_of=asOf,
            db=db,
            redis_client=redis,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail=f"获取状态机制分析失败: {str(e)}"
        )


@router.get("/events", response_model=EventsResponse)
async def market_events(
    market: str = Query(
        ...,
        description="参考原油基准 / Reference crude benchmark",
        enum=["WTI", "Brent"],
    ),
    asOf: Optional[datetime] = Query(
        None,
        description="ISO-8601 时间戳，用于查询历史某一时刻的数据。省略则返回最新数据。",
    ),
    windowDays: Optional[int] = Query(
        7,
        description="回溯时间窗口（天），默认 7 天 / Lookback window in days",
    ),
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
    current_user: str = Depends(get_current_user),
):
    """
    获取近期市场事件 / Get recent market events (Event Lens)

    返回指定时间窗口内影响原油市场的重要事件列表，
    包括地缘政治、政策变化、供需事件等，帮助理解市场波动的具体触发因素。
    通过联网搜索获取最新市场信息进行事件识别。
    """
    try:
        return await get_market_events(
            market=market,
            as_of=asOf,
            window_days=windowDays,
            db=db,
            redis_client=redis,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail=f"获取市场事件分析失败: {str(e)}"
        )
