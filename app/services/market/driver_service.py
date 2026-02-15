"""
Driver Service - 市场驱动因素分析

流程: Redis 缓存(30min) → 必应 MCP 搜索最新新闻 → 收集市场 context
     → OpenRouter 调用 Qwen3 分析 → 解析 JSON → 写缓存 → 返回
"""
import json
import os
import re
import logging
from datetime import datetime, date, timedelta
from typing import Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.market import MarketDriverAnalysis, MarketType
from app.schemas.market import (
    DriverAttributionResponse,
    FactorContribution,
    FactorCategory,
    FactorDirection,
)
from app.services.market.market_service import _get_prices_from_db, _sync_from_yfinance

logger = logging.getLogger(__name__)

# ── 配置 ──
MODEL_NAME = "qwen/qwen3-vl-235b-a22b-thinking"  # 不带 :online，联网由必应 MCP 提供
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
BING_MCP_URL = "https://mcp.api-inference.modelscope.net/925ce3579b2944/mcp"
CACHE_TTL = 1800  # 30 分钟


def _get_api_key() -> str:
    """从环境变量获取 EASYIMPR_API_KEY"""
    key = os.getenv("EASYIMPR_API_KEY", "")
    if not key:
        raise ValueError("EASYIMPR_API_KEY 未配置")
    return key


def _get_bing_token() -> str:
    """从环境变量获取 BING_YING_SEARCH Token"""
    token = os.getenv("BING_YING_SEARCH", "")
    if not token:
        raise ValueError("BING_YING_SEARCH 未配置")
    return token

async def get_market_drivers(
    market: str,
    as_of: Optional[datetime],
    db: AsyncSession,
    redis_client=None,
) -> DriverAttributionResponse:
    """
    获取市场驱动因素分析
    
    逻辑：
    1. 确定目标日期：每日 01:00 AM 更新。01:00 之前取前一天，01:00 之后取当天。
    2. 查 Redis：有则返回。
    3. 查 数据库：有则返回，并回写 Redis。
    4. 均无：实时生成 -> 存数据库 -> 存 Redis -> 返回。
    """
    if as_of:
        target_date = as_of.date()
    else:
        # 01:00 AM 分界线逻辑
        now = datetime.now()
        if now.hour < 1:
            target_date = now.date() - timedelta(days=1)
        else:
            target_date = now.date()

    cache_key = f"market:drivers:{market}:{target_date.isoformat()}"
    
    # ── 1. 优先查询 Redis 缓存 ──
    if redis_client:
        try:
            cached = await redis_client.get(cache_key)
            if cached:
                logger.info(f"Redis 缓存命中: {cache_key}")
                data = json.loads(cached)
                return DriverAttributionResponse(**data)
        except Exception as e:
            logger.warning(f"Redis 读取失败: {e}")

    # ── 2. 其次查询数据库 ──
    try:
        stmt = select(MarketDriverAnalysis).where(
            MarketDriverAnalysis.market == MarketType(market),
            MarketDriverAnalysis.analysis_date == target_date
        )
        result = await db.execute(stmt)
        record = result.scalar_one_or_none()
        
        if record:
            data = record.content
            # 数据库命中 -> 回填 Redis
            if redis_client:
                 try:
                     await redis_client.setex(cache_key, CACHE_TTL, json.dumps(data))
                     logger.info(f"数据库命中，回填 Redis: {cache_key}")
                 except Exception as re:
                     logger.warning(f"Redis 回填失败: {re}")
            
            return DriverAttributionResponse(**data)
            
    except Exception as e:
        logger.error(f"查询数据库驱动分析失败: {e}")

    # ── 3. 最后实时生成（并入库 + 入缓存） ──
    return await generate_and_save_drivers(market, target_date, db, redis_client)


async def generate_and_save_drivers(
    market: str,
    target_date: date,
    db: AsyncSession,
    redis_client=None,
) -> DriverAttributionResponse:
    """
    生成市场驱动因素分析，并保存到数据库和 Redis
    供 API 降级调用或定时任务调用
    """
    logger.info(f"开始生成市场驱动分析: {market} {target_date}")
    
    # ── 1. 必应 MCP 搜索最新新闻 ──
    market_full = "WTI 西德克萨斯中质原油" if market == "WTI" else "Brent 布伦特原油"
    search_query = f"{market_full} crude oil price news today {target_date.isoformat()}"
    news_context = await _search_bing_mcp(search_query)

    # ── 2. 收集市场行情 context ──
    price_context = await _build_market_context(db, market, target_date)

    # ── 3. 调用 Qwen3 分析（不带联网） ──
    llm_result = await _call_qwen(market, price_context, news_context)

    # ── 4. 解析 JSON 响应 ──
    response = _parse_llm_response(llm_result, market, datetime.now()) # as_of 使用当前时间

    # 如果是降级响应（分析不可用），则不入库，只返回
    if response.topDrivers and response.topDrivers[0].factorId == "analysis_unavailable":
         return response

    # ── 5. 存入数据库 ──
    try:
        # 转换为字典用于存储
        content_json = response.model_dump(mode='json')
        
        # 检查是否已存在（并发情况）
        stmt = select(MarketDriverAnalysis).where(
            MarketDriverAnalysis.market == MarketType(market),
            MarketDriverAnalysis.analysis_date == target_date
        )
        existing = (await db.execute(stmt)).scalar_one_or_none()
        
        if existing:
            existing.content = content_json
            existing.created_at = datetime.now()
            logger.info(f"更新数据库驱动分析: {market} {target_date}")
        else:
            new_record = MarketDriverAnalysis(
                market=MarketType(market),
                analysis_date=target_date,
                content=content_json
            )
            db.add(new_record)
            logger.info(f"插入数据库驱动分析: {market} {target_date}")
            
        await db.commit()
    except Exception as e:
        logger.error(f"保存驱动分析到数据库失败: {e}")
        await db.rollback()

    # ── 6. 写入 Redis 缓存 ──
    if redis_client:
        try:
            cache_key = f"market:drivers:{market}:{target_date.isoformat()}"
            await redis_client.setex(
                cache_key,
                CACHE_TTL,
                response.model_dump_json(),
            )
            logger.info(f"Redis 缓存写入: {cache_key}")
        except Exception as e:
            logger.warning(f"Redis 写入失败: {e}")

    return response


# ────────────────── 必应 MCP 搜索 ──────────────────


async def _search_bing_mcp(query: str) -> str:
    """通过必应 MCP 搜索最新市场新闻"""
    try:
        token = _get_bing_token()
    except ValueError as e:
        logger.warning(f"必应 MCP Token 未配置，跳过搜索: {e}")
        return "（未获取到最新新闻，请基于已有数据分析）"

    try:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        headers = {"Authorization": f"Bearer {token}"}
        
        # 增加超时时间并详细配置
        timeout = httpx.Timeout(60.0, connect=10.0, read=60.0, write=60.0)

        async with httpx.AsyncClient(headers=headers, timeout=timeout) as client:
            async with streamable_http_client(
                BING_MCP_URL,
                http_client=client,
            ) as (read_stream, write_stream, _):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()

                    # 列出可用工具
                    tools = await session.list_tools()
                    tool_names = [t.name for t in tools.tools]
                    print(f"INFO:     必应 MCP 可用工具: {tool_names}", flush=True)

                    search_tool = None
                    # ... (unchanged lines)

                    if not search_tool and tool_names:
                        search_tool = tool_names[0]

                    if not search_tool:
                        return "（必应 MCP 无可用搜索工具）"

                    # 调用搜索工具
                    try:
                        print(f"INFO:     正在调用必应搜索工具: {search_tool} query='{query}'", flush=True)
                        result = await session.call_tool(search_tool, {"query": query})
                    except Exception as call_error:
                        logger.error(f"首次调用搜索失败: {call_error}，尝试重试...")
                        raise call_error

                    if result.isError:
                        logger.error(f"必应搜索错误响应: {result.content}")
                        return "（搜索出错）"

                    # 提取结果
                    search_text = ""
                    for content_block in result.content:
                        if hasattr(content_block, "text"):
                            search_text += content_block.text + "\n"

                    if len(search_text) > 3000:
                        search_text = search_text[:3000] + "\n...(已截断)"
                    
                    print(f"INFO:     必应搜索成功，获取到 {len(search_text)} 字符的新闻数据", flush=True)
                    return search_text if search_text.strip() else "（搜索无结果）"

    except Exception as e:
        logger.error(f"必应 MCP 搜索失败: {e}")
        return f"（搜索服务异常: {str(e)[:100]}）"


# ────────────────── 市场 Context ──────────────────


async def _build_market_context(
    db: AsyncSession,
    market: str,
    target_date: date,
) -> str:
    """收集市场行情数据作为 LLM 的分析 context"""
    prices = await _get_prices_from_db(db, market, 10, target_date)

    if len(prices) < 2:
        await _sync_from_yfinance(db, market, 30, target_date)
        prices = await _get_prices_from_db(db, market, 10, target_date)

    if not prices:
        return f"市场: {market}\n当前无可用价格数据。"

    prices_asc = list(reversed(prices))
    latest = prices_asc[-1]
    previous = prices_asc[-2] if len(prices_asc) >= 2 else None

    last_price = float(latest.close_price)
    change_1d = round(last_price - float(previous.close_price), 2) if previous else 0
    pct_change = round((change_1d / float(previous.close_price)) * 100, 2) if previous and float(previous.close_price) != 0 else 0

    recent_prices = "\n".join([
        f"  {p.trade_date}: 开盘 {p.open_price}, 最高 {p.high_price}, 最低 {p.low_price}, 收盘 {p.close_price}"
        for p in prices_asc[-5:]
    ])

    return f"""市场: {market} ({"西德克萨斯中质原油" if market == "WTI" else "布伦特原油"})
数据日期: {target_date.isoformat()}
最新收盘价: ${last_price:.2f}/桶
日变化: {change_1d:+.2f} ({pct_change:+.2f}%)

近5个交易日走势:
{recent_prices}"""


# ────────────────── LLM 调用 ──────────────────


async def _call_qwen(market: str, price_context: str, news_context: str) -> str:
    """通过 OpenRouter 调用 Qwen3（不带联网），将必应搜索结果作为 context"""
    api_key = _get_api_key()

    system_prompt = """你是一名专业的原油市场高级分析师。你需要基于提供的市场数据和最新新闻搜索结果，分析当前影响原油价格的关键驱动因素。

请严格按照以下 JSON 格式返回结果，不要包含任何额外文字、解释或 markdown 标记：

{
  "topDrivers": [
    {
      "factorId": "因素ID（英文下划线命名，如 opec_production）",
      "factorName": "因素名称（中文）",
      "category": "SUPPLY|DEMAND|MACRO_FINANCIAL|FX|EVENTS|OTHER",
      "direction": "UP|DOWN|NEUTRAL",
      "strength": 1到10的数字,
      "evidence": ["证据1", "证据2"]
    }
  ],
  "allDrivers": [同上格式，包含全部因素],
  "summary": "一段话总结当前市场驱动逻辑（中文）"
}

要求：
1. topDrivers 是 allDrivers 中 strength 最大的前 3 个因素
2. allDrivers 应包含 5-8 个因素，覆盖 SUPPLY/DEMAND/MACRO_FINANCIAL/FX/EVENTS 各类别
3. strength 1-10，10 表示影响最大
4. direction 表示该因素对油价的影响方向：UP=推高油价，DOWN=压低油价，NEUTRAL=中性
5. evidence 每个因素提供 1-3 条简短证据
6. summary 用一段自然语言概述当前市场整体驱动逻辑
7. 只返回 JSON，不要返回任何其他内容"""

    user_prompt = f"""请分析以下原油市场数据，结合最新新闻给出驱动因素分析：

=== 市场行情数据 ===
{price_context}

=== 最新新闻搜索结果 ===
{news_context}"""

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/citibank-backend",
        "X-Title": "Citibank Market Drivers Analysis",
    }

    payload = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                OPENROUTER_API_URL,
                headers=headers,
                json=payload,
            )
            response.raise_for_status()

            data = response.json()

            if "error" in data:
                logger.error(f"OpenRouter API 错误: {data['error']}")
                raise RuntimeError(f"LLM API 错误: {data['error']}")

            if not data.get("choices"):
                raise RuntimeError("LLM 未返回任何内容")

            content = data["choices"][0]["message"].get("content", "")
            logger.info(f"Qwen3 响应长度: {len(content)} 字符")
            return content

    except httpx.HTTPStatusError as e:
        logger.error(f"OpenRouter HTTP 错误: {e.response.status_code} - {e.response.text}")
        raise RuntimeError(f"LLM 调用失败 (HTTP {e.response.status_code})")
    except httpx.TimeoutException:
        logger.error("OpenRouter 请求超时")
        raise RuntimeError("LLM 调用超时（120s）")


# ────────────────── JSON 解析 ──────────────────


def _parse_llm_response(
    llm_text: str,
    market: str,
    as_of: datetime,
) -> DriverAttributionResponse:
    """从 LLM 响应中解析 JSON 并构建响应对象"""
    json_str = _extract_json(llm_text)

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        logger.error(f"LLM 返回的 JSON 解析失败: {e}\n原始内容: {llm_text[:500]}")
        return _build_fallback_response(market, as_of)

    try:
        top_drivers = [FactorContribution(**d) for d in data.get("topDrivers", [])]
        all_drivers = [FactorContribution(**d) for d in data.get("allDrivers", [])]
        summary = data.get("summary", "暂无分析摘要")

        if not top_drivers and all_drivers:
            sorted_drivers = sorted(all_drivers, key=lambda x: x.strength, reverse=True)
            top_drivers = sorted_drivers[:3]

        return DriverAttributionResponse(
            market=market,
            asOf=as_of,
            topDrivers=top_drivers,
            allDrivers=all_drivers,
            summary=summary,
        )
    except Exception as e:
        logger.error(f"构建 DriverAttributionResponse 失败: {e}")
        return _build_fallback_response(market, as_of)


def _extract_json(text: str) -> str:
    """从文本中提取 JSON，处理 markdown code block 等情况"""
    pattern = r"```(?:json)?\s*\n?(.*?)\n?\s*```"
    match = re.search(pattern, text, re.DOTALL)
    if match:
        return match.group(1).strip()

    brace_start = text.find("{")
    brace_end = text.rfind("}")
    if brace_start != -1 and brace_end != -1 and brace_end > brace_start:
        return text[brace_start:brace_end + 1]

    return text.strip()


def _build_fallback_response(
    market: str,
    as_of: datetime,
) -> DriverAttributionResponse:
    """LLM 解析失败时的降级响应"""
    fallback_drivers = [
        FactorContribution(
            factorId="analysis_unavailable",
            factorName="分析暂不可用",
            category=FactorCategory.OTHER,
            direction=FactorDirection.NEUTRAL,
            strength=0,
            evidence=["LLM 分析服务暂时不可用，请稍后重试"],
        )
    ]
    return DriverAttributionResponse(
        market=market,
        asOf=as_of,
        topDrivers=fallback_drivers,
        allDrivers=fallback_drivers,
        summary="驱动因素分析暂时不可用，请稍后重试。",
    )
