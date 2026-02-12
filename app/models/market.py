"""
Market 数据库模型
"""
from sqlalchemy import Column, BigInteger, String, Date, DECIMAL, TIMESTAMP, Enum as SQLEnum, Index
from sqlalchemy.sql import func
from datetime import date
from typing import List, Optional
from app.db.base import Base
import enum


class MarketType(str, enum.Enum):
    """市场类型枚举"""
    WTI = "WTI"
    Brent = "Brent"


class MarketDailyPrice(Base):
    """市场每日价格数据表"""
    __tablename__ = "market_daily_prices"

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment="主键ID")
    market = Column(SQLEnum(MarketType), nullable=False, comment="市场类型")
    trade_date = Column(Date, nullable=False, comment="交易日期")
    open_price = Column(DECIMAL(10, 2), nullable=False, comment="开盘价")
    high_price = Column(DECIMAL(10, 2), nullable=False, comment="最高价")
    low_price = Column(DECIMAL(10, 2), nullable=False, comment="最低价")
    close_price = Column(DECIMAL(10, 2), nullable=False, comment="收盘价")
    volume = Column(BigInteger, nullable=True, comment="成交量")
    front_month_price = Column(DECIMAL(10, 2), nullable=True, comment="近月合约价格")
    second_month_price = Column(DECIMAL(10, 2), nullable=True, comment="次近月合约价格")
    created_at = Column(TIMESTAMP, server_default=func.now(), comment="数据入库时间")

    # 复合唯一索引：防止同一市场同一日期重复数据
    __table_args__ = (
        Index('idx_market_date', 'market', 'trade_date', unique=True),
    )

    @classmethod
    async def get_recent_prices(
        cls,
        session,
        market: MarketType,
        days: int = 60,
        end_date: Optional[date] = None
    ) -> List['MarketDailyPrice']:
        """
        获取指定市场最近 N 天的价格数据
        
        Args:
            session: 数据库会话
            market: 市场类型
            days: 天数
            end_date: 结束日期（不传则为当前日期）
            
        Returns:
            价格数据列表，按日期降序
        """
        from sqlalchemy import select
        
        query = select(cls).where(cls.market == market)
        
        if end_date:
            query = query.where(cls.trade_date <= end_date)
        
        query = query.order_by(cls.trade_date.desc()).limit(days)
        
        result = await session.execute(query)
        return result.scalars().all()

    def __repr__(self):
        return f"<MarketDailyPrice(market={self.market}, date={self.trade_date}, close={self.close_price})>"
