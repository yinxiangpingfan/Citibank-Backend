"""
Market 路由端点
"""
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.core.redis import get_redis
from app.schemas.market import MarketSnapshotResponse
from app.services.market import get_market_snapshot

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
