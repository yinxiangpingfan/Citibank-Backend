"""
Market 模块的 Pydantic Schema 定义
"""
from enum import Enum
from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel, Field


class TermStructureState(str, Enum):
    """期限结构状态"""
    BACKWARDATION = "BACKWARDATION"  # 现货升水（近月贵）
    CONTANGO = "CONTANGO"            # 期货升水（远月贵）
    FLAT = "FLAT"                    # 持平


class TermStructure(BaseModel):
    """期限结构"""
    state: TermStructureState = Field(..., description="期限结构状态")
    spreadFrontSecond: float = Field(..., description="近月合约与次近月合约的价差")


class PricePoint(BaseModel):
    """历史价格点"""
    ts: datetime = Field(..., description="时间戳")
    value: float = Field(..., description="价格值")


class MarketSnapshotResponse(BaseModel):
    """市场快照响应"""
    market: str = Field(..., description="市场类型 (WTI/Brent)")
    asOf: datetime = Field(..., description="数据时间点")
    lastPrice: float = Field(..., description="最新价格 (USD/桶)")
    change1d: float = Field(..., description="日绝对变化")
    pctChange1d: float = Field(..., description="日百分比变化 (%)")
    volatility20d: float = Field(..., description="20日年化波动率")
    termStructure: TermStructure = Field(..., description="期限结构")
    history: List[PricePoint] = Field(..., description="历史价格数据（用于走势图）")

    class Config:
        json_schema_extra = {
            "example": {
                "market": "WTI",
                "asOf": "2026-02-12T14:30:00Z",
                "lastPrice": 75.50,
                "change1d": 2.30,
                "pctChange1d": 3.15,
                "volatility20d": 0.25,
                "termStructure": {
                    "state": "BACKWARDATION",
                    "spreadFrontSecond": 1.50
                },
                "history": [
                    {"ts": "2026-01-15T00:00:00Z", "value": 72.30},
                    {"ts": "2026-01-16T00:00:00Z", "value": 73.10}
                ]
            }
        }
