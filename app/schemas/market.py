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


# ────────────────── Drivers 接口模型 ──────────────────


class FactorCategory(str, Enum):
    """驱动因素类别"""
    SUPPLY = "SUPPLY"
    DEMAND = "DEMAND"
    MACRO_FINANCIAL = "MACRO_FINANCIAL"
    FX = "FX"
    EVENTS = "EVENTS"
    OTHER = "OTHER"


class FactorDirection(str, Enum):
    """价格影响方向"""
    UP = "UP"
    DOWN = "DOWN"
    NEUTRAL = "NEUTRAL"


class FactorContribution(BaseModel):
    """驱动因素贡献"""
    factorId: str = Field(..., description="因素唯一标识符")
    factorName: str = Field(..., description="因素名称")
    category: FactorCategory = Field(..., description="因素类别")
    direction: FactorDirection = Field(..., description="对价格的影响方向")
    strength: float = Field(..., description="标准化贡献强度 (1-10)")
    evidence: Optional[List[str]] = Field(default=None, description="证据要点列表")


class DriverAttributionResponse(BaseModel):
    """市场驱动因素分析响应"""
    market: str = Field(..., description="市场类型 (WTI/Brent)")
    asOf: datetime = Field(..., description="数据时间点")
    topDrivers: List[FactorContribution] = Field(..., description="最重要的驱动因素")
    allDrivers: List[FactorContribution] = Field(..., description="全部驱动因素")
    summary: str = Field(..., description="一段话自然语言解释")


# ────────────────── Regime 接口模型 ──────────────────


class RegimeType(str, Enum):
    """市场状态机制类型"""
    DEMAND_DRIVEN = "DEMAND_DRIVEN"      # 需求驱动
    SUPPLY_DRIVEN = "SUPPLY_DRIVEN"      # 供应驱动
    EVENT_DRIVEN = "EVENT_DRIVEN"        # 事件驱动
    FINANCIAL_DRIVEN = "FINANCIAL_DRIVEN" # 金融驱动
    MIXED = "MIXED"                      # 混合


class StabilityLevel(str, Enum):
    """稳定性等级"""
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class RegimeSwitch(BaseModel):
    """状态转换记录"""
    from_regime: str = Field(..., alias="from", description="原状态")
    to_regime: str = Field(..., alias="to", description="新状态")
    ts: datetime = Field(..., description="转换时间")
    reason: Optional[str] = Field(None, description="转换原因")

    class Config:
        populate_by_name = True


class RegimeStateResponse(BaseModel):
    """市场状态机制响应"""
    market: str = Field(..., description="市场类型 (WTI/Brent)")
    asOf: datetime = Field(..., description="数据时间点")
    regime: RegimeType = Field(..., description="当前市场状态机制类型")
    stability: StabilityLevel = Field(..., description="稳定性等级")
    confidence: float = Field(..., ge=0, le=1, description="置信度 (0-1)")
    recentSwitches: List[RegimeSwitch] = Field(default_factory=list, description="近期状态转换记录")
    summary: Optional[str] = Field(None, description="状态机制分析摘要")

    class Config:
        json_schema_extra = {
            "example": {
                "market": "WTI",
                "asOf": "2026-02-15T10:00:00Z",
                "regime": "SUPPLY_DRIVEN",
                "stability": "MEDIUM",
                "confidence": 0.75,
                "recentSwitches": [
                    {
                        "from": "DEMAND_DRIVEN",
                        "to": "SUPPLY_DRIVEN",
                        "ts": "2026-02-10T00:00:00Z",
                        "reason": "OPEC+ 减产决议导致供应端影响力上升"
                    }
                ],
                "summary": "当前市场主要由供应端因素驱动，OPEC+ 减产政策对价格形成支撑。"
            }
        }


# ────────────────── Events 接口模型 ──────────────────


class EventType(str, Enum):
    """事件类型"""
    GEOPOLITICS = "GEOPOLITICS"    # 地缘政治
    POLICY = "POLICY"              # 政策变化
    SUPPLY = "SUPPLY"              # 供应事件
    DEMAND = "DEMAND"              # 需求事件
    MACRO = "MACRO"                # 宏观经济
    OTHER = "OTHER"                # 其他


class EventImpact(str, Enum):
    """事件影响方向"""
    UP = "UP"                      # 利多
    DOWN = "DOWN"                  # 利空
    UNCERTAIN = "UNCERTAIN"        # 不确定


class EventCard(BaseModel):
    """事件卡片"""
    eventId: str = Field(..., description="事件唯一标识符")
    ts: datetime = Field(..., description="事件发生时间")
    title: str = Field(..., description="事件标题")
    type: EventType = Field(..., description="事件类型")
    impact: EventImpact = Field(..., description="对油价的影响方向")
    linkedFactors: Optional[List[str]] = Field(default=None, description="关联的驱动因素ID列表")
    evidence: Optional[List[str]] = Field(default=None, description="证据来源列表")


class EventsResponse(BaseModel):
    """市场事件响应"""
    market: str = Field(..., description="市场类型 (WTI/Brent)")
    asOf: datetime = Field(..., description="数据时间点")
    windowDays: int = Field(..., description="回溯时间窗口（天）")
    events: List[EventCard] = Field(default_factory=list, description="事件卡片列表")

    class Config:
        json_schema_extra = {
            "example": {
                "market": "WTI",
                "asOf": "2026-02-15T10:00:00Z",
                "windowDays": 7,
                "events": [
                    {
                        "eventId": "evt_opec_meeting_20260210",
                        "ts": "2026-02-10T00:00:00Z",
                        "title": "OPEC+ 部长级会议决定延长减产至Q2",
                        "type": "POLICY",
                        "impact": "UP",
                        "linkedFactors": ["opec_production_cut"],
                        "evidence": ["路透社报道", "OPEC官方公告"]
                    }
                ]
            }
        }
