"""
Event Service - 市场事件分析 (Event Lens)

流程: Redis 缓存(30min) → MySQL → 必应 MCP 搜索最新新闻
     → OpenRouter 调用 Qwen3 识别事件 → 解析 JSON → 写缓存 → 返回
"""
import json
import os
import re
import logging
import hashlib
from datetime import datetime, date, timedelta
from typing import Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.market import MarketEventAnalysis, MarketType
from app.schemas.market import (
    EventsResponse,
    EventType,
    EventImpact,
    EventCard,
)
from app.services.market.market_service import _get_prices_from_db, _sync_from_yfinance

logger = logging.getLogger(__name__)

# ── 配置 ──
MODEL_NAME = "qwen/qwen3-vl-235b-a22b-thinking"
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


async def get_market_events(
    market: str,
    as_of: Optional[datetime],
    window_days: int,
    db: AsyncSession,
    redis_client=None,
) -> EventsResponse:
    """
    获取市场事件分析

    逻辑：
    1. 确定目标日期：每日 01:20 AM 更新。01:20 之前取前一天，01:20 之后取当天。
    2. 查 Redis：有则返回（key 包含 windowDays）。
    3. 查 数据库：有则返回，并回写 Redis。
    4. 均无：实时生成 -> 存数据库 -> 存 Redis -> 返回。
    """
    if as_of:
        target_date = as_of.date()
    else:
        # 01:20 AM 分界线逻辑
        now = datetime.now()
        if now.hour < 1 or (now.hour == 1 and now.minute < 20):
            target_date = now.date() - timedelta(days=1)
        else:
            target_date = now.date()

    # Redis key 包含 windowDays
    cache_key = f"market:events:{market}:{target_date.isoformat()}:{window_days}"

    # ── 1. 优先查询 Redis 缓存 ──
    if redis_client:
        try:
            cached = await redis_client.get(cache_key)
            if cached:
                logger.info(f"Redis 缓存命中: {cache_key}")
                data = json.loads(cached)
                return EventsResponse(**data)
        except Exception as e:
            logger.warning(f"Redis 读取失败: {e}")

    # ── 2. 其次查询数据库 ──
    try:
        stmt = select(MarketEventAnalysis).where(
            MarketEventAnalysis.market == MarketType(market),
            MarketEventAnalysis.analysis_date == target_date,
            MarketEventAnalysis.window_days == window_days
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

            return EventsResponse(**data)

    except Exception as e:
        logger.error(f"查询数据库事件分析失败: {e}")

    # ── 3. 最后实时生成（并入库 + 入缓存） ──
    return await generate_and_save_events(market, target_date, window_days, db, redis_client)


async def generate_and_save_events(
    market: str,
    target_date: date,
    window_days: int = 7,
    db: AsyncSession = None,
    redis_client=None,
) -> EventsResponse:
    """
    生成市场事件分析，并保存到数据库和 Redis
    供 API 降级调用或定时任务调用
    """
    logger.info(f"开始生成市场事件分析: {market} {target_date} window={window_days}天")

    # ── 1. 必应 MCP 搜索事件相关新闻 ──
    market_full = "WTI 西德克萨斯中质原油" if market == "WTI" else "Brent 布伦特原油"
    search_query = f"{market_full} crude oil geopolitics OPEC policy supply demand events {target_date.year}"
    news_context = await _search_bing_mcp(search_query)

    # ── 2. 收集市场行情 context ──
    price_context = await _build_market_context(db, market, target_date)

    # ── 3. 调用 Qwen3 分析事件 ──
    llm_result = await _call_qwen(market, price_context, news_context, window_days)

    # ── 4. 解析 JSON 响应 ──
    response = _parse_llm_response(llm_result, market, datetime.now(), window_days)

    # 如果是降级响应（events 为空且无正常分析），只返回不入库
    if not response.events:
        return response

    # ── 5. 存入数据库 ──
    if db:
        try:
            content_json = response.model_dump(mode='json')

            # 检查是否已存在（并发情况）- 包含 window_days
            stmt = select(MarketEventAnalysis).where(
                MarketEventAnalysis.market == MarketType(market),
                MarketEventAnalysis.analysis_date == target_date,
                MarketEventAnalysis.window_days == window_days
            )
            existing = (await db.execute(stmt)).scalar_one_or_none()

            if existing:
                existing.content = content_json
                existing.created_at = datetime.now()
                logger.info(f"更新数据库事件分析: {market} {target_date} window={window_days}")
            else:
                new_record = MarketEventAnalysis(
                    market=MarketType(market),
                    analysis_date=target_date,
                    window_days=window_days,
                    content=content_json
                )
                db.add(new_record)
                logger.info(f"插入数据库事件分析: {market} {target_date} window={window_days}")

            await db.commit()
        except Exception as e:
            logger.error(f"保存事件分析到数据库失败: {e}")
            await db.rollback()

    # ── 6. 写入 Redis 缓存 ──
    if redis_client:
        try:
            cache_key = f"market:events:{market}:{target_date.isoformat()}:{window_days}"
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

        timeout = httpx.Timeout(60.0, connect=10.0, read=60.0, write=60.0)

        async with httpx.AsyncClient(headers=headers, timeout=timeout) as client:
            async with streamable_http_client(
                BING_MCP_URL,
                http_client=client,
            ) as (read_stream, write_stream, _):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()

                    tools = await session.list_tools()
                    tool_names = [t.name for t in tools.tools]
                    print(f"INFO:     必应 MCP 可用工具: {tool_names}", flush=True)

                    search_tool = None
                    if not search_tool and tool_names:
                        search_tool = tool_names[0]

                    if not search_tool:
                        return "（必应 MCP 无可用搜索工具）"

                    try:
                        print(f"INFO:     正在调用必应搜索工具: {search_tool} query='{query}'", flush=True)
                        result = await session.call_tool(search_tool, {"query": query})
                    except Exception as call_error:
                        logger.error(f"首次调用搜索失败: {call_error}，尝试重试...")
                        raise call_error

                    if result.isError:
                        logger.error(f"必应搜索错误响应: {result.content}")
                        return "（搜索出错）"

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
    if not db:
        return f"市场: {market}\n当前无可用价格数据。"

    prices = await _get_prices_from_db(db, market, 60, target_date)

    if len(prices) < 2:
        await _sync_from_yfinance(db, market, 90, target_date)
        prices = await _get_prices_from_db(db, market, 60, target_date)

    if not prices:
        return f"市场: {market}\n当前无可用价格数据。"

    prices_asc = list(reversed(prices))
    latest = prices_asc[-1]
    previous = prices_asc[-2] if len(prices_asc) >= 2 else None

    last_price = float(latest.close_price)
    change_1d = round(last_price - float(previous.close_price), 2) if previous else 0
    pct_change = round((change_1d / float(previous.close_price)) * 100, 2) if previous and float(previous.close_price) != 0 else 0

    # 近 10 个交易日走势
    recent_prices = "\n".join([
        f"  {p.trade_date}: 开盘 {p.open_price}, 最高 {p.high_price}, 最低 {p.low_price}, 收盘 {p.close_price}"
        for p in prices_asc[-10:]
    ])

    return f"""市场: {market} ({"西德克萨斯中质原油" if market == "WTI" else "布伦特原油"})
数据日期: {target_date.isoformat()}
最新收盘价: ${last_price:.2f}/桶
日变化: {change_1d:+.2f} ({pct_change:+.2f}%)

近10个交易日走势:
{recent_prices}"""


# ────────────────── LLM 调用 ──────────────────


async def _call_qwen(market: str, price_context: str, news_context: str, window_days: int) -> str:
    """通过 OpenRouter 调用 Qwen3，从新闻中识别并分类近期市场事件"""
    api_key = _get_api_key()

    system_prompt = f"""你是一名专业的原油市场高级分析师。你需要基于提供的市场数据和最新新闻搜索结果，识别并分类过去 {window_days} 天内影响原油市场的重要事件。

事件类型说明：
- GEOPOLITICS: 地缘政治事件（如中东冲突、制裁、外交事件等）
- POLICY: 政策变化（如OPEC决议、各国能源政策、环保法规等）
- SUPPLY: 供应事件（如产量变化、管道故障、库存数据等）
- DEMAND: 需求事件（如经济数据发布、消费旺季/淡季、航运需求等）
- MACRO: 宏观经济事件（如利率决议、GDP数据、通胀数据、美元走势等）
- OTHER: 其他事件

影响方向说明：
- UP: 利多（推升油价）
- DOWN: 利空（压低油价）
- UNCERTAIN: 影响不确定

请严格按照以下 JSON 格式返回结果，不要包含任何额外文字、解释或 markdown 标记：

{{
  "events": [
    {{
      "eventId": "evt_简短英文标识_日期YYYYMMDD",
      "ts": "事件发生时间（ISO-8601格式）",
      "title": "事件标题（中文，简洁明了）",
      "type": "GEOPOLITICS|POLICY|SUPPLY|DEMAND|MACRO|OTHER",
      "impact": "UP|DOWN|UNCERTAIN",
      "linkedFactors": ["关联的驱动因素ID，如opec_production_cut"],
      "evidence": ["新闻来源或数据支撑"]
    }}
  ]
}}

【重要要求】：
1. 从新闻中识别过去 {window_days} 天内的具体市场事件
2. 每个事件必须是具体的、有明确时间的事件，不要泛泛而谈
3. 事件标题用中文，简洁概括事件内容
4. eventId 格式为 evt_简短英文描述_日期（如 evt_opec_meeting_20260210）
5. linkedFactors 填写与该事件关联的驱动因素ID（如 opec_production_cut, us_dollar_strength 等）
6. evidence 填写事件的信息来源（如"路透社报道"、"EIA周报数据"等）
7. 按事件发生时间倒序排列（最新的在前）
8. 返回 3-10 个最重要的事件

【强制要求 - 禁止返回空数组】：
- 你必须返回至少 3 个事件，events 数组不能为空！
- 即使新闻信息有限，也要根据价格走势、市场常识推断可能的驱动事件
- 如果新闻中没有明确事件，请基于以下常见原油市场事件类型进行合理推断：
  * OPEC+ 产量政策或会议
  * 美国原油库存变化 (EIA/API数据)
  * 地缘政治紧张局势
  * 美元指数波动
  * 全球经济数据发布
  * 主要产油国供应中断
- 推断的事件需在 evidence 中标注"基于市场数据推断"
- 绝对不允许返回 "events": [] 空数组！"""

    user_prompt = f"""请分析以下原油市场数据和新闻，识别过去 {window_days} 天内的重要市场事件：

=== 市场行情数据 ===
{price_context}

=== 最新新闻搜索结果 ===
{news_context}"""

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/citibank-backend",
        "X-Title": "Citibank Market Events Analysis",
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
            logger.info(f"Qwen3 事件分析响应长度: {len(content)} 字符")
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
    window_days: int,
) -> EventsResponse:
    """从 LLM 响应中解析 JSON 并构建响应对象"""
    json_str = _extract_json(llm_text)

    logger.info(f"LLM 原始返回: {json_str[:1000]}")

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        logger.error(f"LLM 返回的 JSON 解析失败: {e}\n原始内容: {llm_text[:500]}")
        return _build_fallback_response(market, as_of, window_days)

    try:
        events_raw = data.get("events", [])
        events = []

        for evt_data in events_raw:
            try:
                # 解析 eventId
                event_id = evt_data.get("eventId", "")
                if not event_id:
                    # 用标题生成一个 ID
                    title_hash = hashlib.md5(evt_data.get("title", "").encode()).hexdigest()[:8]
                    event_id = f"evt_{title_hash}"

                # 解析时间
                ts = evt_data.get("ts")
                if isinstance(ts, str):
                    try:
                        ts_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    except Exception:
                        ts_dt = as_of
                else:
                    ts_dt = as_of

                # 解析事件类型
                type_str = evt_data.get("type", "OTHER")
                try:
                    event_type = EventType(type_str)
                except ValueError:
                    event_type = EventType.OTHER

                # 解析影响方向
                impact_str = evt_data.get("impact", "UNCERTAIN")
                try:
                    event_impact = EventImpact(impact_str)
                except ValueError:
                    event_impact = EventImpact.UNCERTAIN

                events.append(EventCard(
                    eventId=event_id,
                    ts=ts_dt,
                    title=evt_data.get("title", "未知事件"),
                    type=event_type,
                    impact=event_impact,
                    linkedFactors=evt_data.get("linkedFactors"),
                    evidence=evt_data.get("evidence"),
                ))
            except Exception as e:
                logger.warning(f"解析 EventCard 失败: {e}")
                continue

        return EventsResponse(
            market=market,
            asOf=as_of,
            windowDays=window_days,
            events=events,
        )
    except Exception as e:
        logger.error(f"构建 EventsResponse 失败: {e}")
        return _build_fallback_response(market, as_of, window_days)


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
    window_days: int,
) -> EventsResponse:
    """LLM 解析失败时的降级响应"""
    return EventsResponse(
        market=market,
        asOf=as_of,
        windowDays=window_days,
        events=[],
    )
