"""
Regime Service - 市场状态机制分析

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

from app.models.market import MarketRegimeAnalysis, MarketType
from app.schemas.market import (
    RegimeStateResponse,
    RegimeType,
    StabilityLevel,
    RegimeSwitch,
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


async def get_market_regime(
    market: str,
    as_of: Optional[datetime],
    db: AsyncSession,
    redis_client=None,
) -> RegimeStateResponse:
    """
    获取市场状态机制分析

    逻辑：
    1. 确定目标日期：每日 01:10 AM 更新。01:10 之前取前一天，01:10 之后取当天。
    2. 查 Redis：有则返回。
    3. 查 数据库：有则返回，并回写 Redis。
    4. 均无：实时生成 -> 存数据库 -> 存 Redis -> 返回。
    """
    if as_of:
        target_date = as_of.date()
    else:
        # 01:10 AM 分界线逻辑
        now = datetime.now()
        if now.hour < 1 or (now.hour == 1 and now.minute < 10):
            target_date = now.date() - timedelta(days=1)
        else:
            target_date = now.date()

    cache_key = f"market:regime:{market}:{target_date.isoformat()}"

    # ── 1. 优先查询 Redis 缓存 ──
    if redis_client:
        try:
            cached = await redis_client.get(cache_key)
            if cached:
                logger.info(f"Redis 缓存命中: {cache_key}")
                data = json.loads(cached)
                return RegimeStateResponse(**data)
        except Exception as e:
            logger.warning(f"Redis 读取失败: {e}")

    # ── 2. 其次查询数据库 ──
    try:
        stmt = select(MarketRegimeAnalysis).where(
            MarketRegimeAnalysis.market == MarketType(market),
            MarketRegimeAnalysis.analysis_date == target_date
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

            return RegimeStateResponse(**data)

    except Exception as e:
        logger.error(f"查询数据库状态机制分析失败: {e}")

    # ── 3. 最后实时生成（并入库 + 入缓存） ──
    return await generate_and_save_regime(market, target_date, db, redis_client)


async def generate_and_save_regime(
    market: str,
    target_date: date,
    db: AsyncSession,
    redis_client=None,
) -> RegimeStateResponse:
    """
    生成市场状态机制分析，并保存到数据库和 Redis
    供 API 降级调用或定时任务调用
    """
    logger.info(f"开始生成市场状态机制分析: {market} {target_date}")

    # ── 1. 必应 MCP 搜索最新新闻 ──
    market_full = "WTI 西德克萨斯中质原油" if market == "WTI" else "Brent 布伦特原油"
    search_query = f"{market_full} crude oil price OPEC supply demand trend 2026"
    news_context = await _search_bing_mcp(search_query)

    # ── 2. 收集市场行情 context ──
    price_context = await _build_market_context(db, market, target_date)

    # ── 3. 调用 Qwen3 分析（不带联网） ──
    llm_result = await _call_qwen(market, price_context, news_context)

    # ── 4. 解析 JSON 响应 ──
    response = _parse_llm_response(llm_result, market, datetime.now())

    # 如果是降级响应（分析不可用），则不入库，只返回
    if response.regime == RegimeType.MIXED and response.confidence == 0:
         return response

    # ── 5. 存入数据库 ──
    try:
        # 转换为字典用于存储
        content_json = response.model_dump(mode='json')

        # 检查是否已存在（并发情况）
        stmt = select(MarketRegimeAnalysis).where(
            MarketRegimeAnalysis.market == MarketType(market),
            MarketRegimeAnalysis.analysis_date == target_date
        )
        existing = (await db.execute(stmt)).scalar_one_or_none()

        if existing:
            existing.content = content_json
            existing.created_at = datetime.now()
            logger.info(f"更新数据库状态机制分析: {market} {target_date}")
        else:
            new_record = MarketRegimeAnalysis(
                market=MarketType(market),
                analysis_date=target_date,
                content=content_json
            )
            db.add(new_record)
            logger.info(f"插入数据库状态机制分析: {market} {target_date}")

        await db.commit()
    except Exception as e:
        logger.error(f"保存状态机制分析到数据库失败: {e}")
        await db.rollback()

    # ── 6. 写入 Redis 缓存 ──
    if redis_client:
        try:
            cache_key = f"market:regime:{market}:{target_date.isoformat()}"
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

    # 计算 20 日波动率
    if len(prices_asc) >= 20:
        closes = [float(p.close_price) for p in prices_asc[-20:]]
        returns = [(closes[i] - closes[i-1]) / closes[i-1] for i in range(1, len(closes))]
        volatility = (sum(r**2 for r in returns) / len(returns)) ** 0.5 * (252 ** 0.5)
    else:
        volatility = 0

    # 计算趋势（近 5 日 vs 前 5 日均价）
    if len(prices_asc) >= 10:
        recent_avg = sum(float(p.close_price) for p in prices_asc[-5:]) / 5
        earlier_avg = sum(float(p.close_price) for p in prices_asc[-10:-5]) / 5
        trend = "上涨" if recent_avg > earlier_avg else "下跌" if recent_avg < earlier_avg else "震荡"
        trend_pct = abs(recent_avg - earlier_avg) / earlier_avg * 100
    else:
        trend = "未知"
        trend_pct = 0

    # 获取更多历史数据用于状态转换分析（30天）
    recent_prices = "\n".join([
        f"  {p.trade_date}: 开盘 {p.open_price}, 最高 {p.high_price}, 最低 {p.low_price}, 收盘 {p.close_price}"
        for p in prices_asc[-30:]
    ])

    # 计算月度和季度变化
    if len(prices_asc) >= 20:
        monthly_change = last_price - float(prices_asc[-20].close_price) if len(prices_asc) >= 20 else 0
        monthly_pct = (monthly_change / float(prices_asc[-20].close_price)) * 100 if float(prices_asc[-20].close_price) != 0 else 0
    else:
        monthly_change = 0
        monthly_pct = 0

    return f"""市场: {market} ({"西德克萨斯中质原油" if market == "WTI" else "布伦特原油"})
数据日期: {target_date.isoformat()}
最新收盘价: ${last_price:.2f}/桶
日变化: {change_1d:+.2f} ({pct_change:+.2f}%)
20日变化: {monthly_change:+.2f} ({monthly_pct:+.2f}%)
20日年化波动率: {volatility:.2%}
近期趋势: {trend} ({trend_pct:.2f}%)

近30个交易日走势（用于分析状态转换）:
{recent_prices}"""


# ────────────────── LLM 调用 ──────────────────


async def _call_qwen(market: str, price_context: str, news_context: str) -> str:
    """通过 OpenRouter 调用 Qwen3（不带联网），将必应搜索结果作为 context"""
    api_key = _get_api_key()

    system_prompt = """你是一名专业的原油市场高级分析师。你需要基于提供的市场数据和最新新闻搜索结果，判断当前原油市场处于哪种状态机制（Regime）。

市场状态机制类型说明：
- DEMAND_DRIVEN: 需求驱动 - 市场价格主要由需求端因素主导（如经济复苏、工业需求增长等）
- SUPPLY_DRIVEN: 供应驱动 - 市场价格主要由供应端因素主导（如OPEC减产、地缘政治影响供应等）
- EVENT_DRIVEN: 事件驱动 - 市场价格主要由突发事件主导（如战争、自然灾害、突发政策等）
- FINANCIAL_DRIVEN: 金融驱动 - 市场价格主要由金融市场因素主导（如美元走势、投机资金流向、利率变化等）
- MIXED: 混合状态 - 多种因素共同作用，没有明显的主导因素

请严格按照以下 JSON 格式返回结果，不要包含任何额外文字、解释或 markdown 标记：

{
  "regime": "DEMAND_DRIVEN|SUPPLY_DRIVEN|EVENT_DRIVEN|FINANCIAL_DRIVEN|MIXED",
  "stability": "HIGH|MEDIUM|LOW",
  "confidence": 0.0到1.0之间的数字,
  "recentSwitches": [
    {
      "from": "原状态类型",
      "to": "新状态类型",
      "ts": "状态转换的大致时间（ISO格式日期）",
      "reason": "转换原因简述"
    }
  ],
  "summary": "一段话总结当前市场状态机制及其原因（中文）"
}

【重要】recentSwitches 必须返回数据！要求如下：
1. 请仔细分析你掌握的所有信息（新闻、价格走势、波动率变化等）
2. 基于近30日的的价格走势，找出价格明显上涨或下跌的阶段
3. 结合新闻中提到的供应、需求、事件、金融等影响因素
4. 推断每个价格阶段的驱动因素属于哪种状态机制
5. 如果近期价格从低位反弹，很可能经历了 DEMAND_DRIVEN 或 EVENT_DRIVEN → 其他 的转换
6. 如果近期价格持续下跌，可能经历了 SUPPLY_DRIVEN 或 EVENT_DRIVEN → DEMAND_DRIVEN 的转换
7. 至少返回最近1次状态转换（即使是推断的），格式如下：
   {"from": "SUPPLY_DRIVEN", "to": "DEMAND_DRIVEN", "ts": "2026-01-15", "reason": "价格持续下跌，需求疲软成为主要驱动因素"}
8. 最多返回最近3次状态转换
9. 如果你确定近期没有任何状态转换，才返回空数组 []"""

    user_prompt = f"""请分析以下原油市场数据，判断当前市场处于哪种状态机制：

=== 市场行情数据 ===
{price_context}

=== 最新新闻搜索结果 ===
{news_context}"""

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/citibank-backend",
        "X-Title": "Citibank Market Regime Analysis",
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
) -> RegimeStateResponse:
    """从 LLM 响应中解析 JSON 并构建响应对象"""
    json_str = _extract_json(llm_text)

    # 打印 LLM 返回的原始 JSON（用于调试）
    logger.info(f"LLM 原始返回: {json_str[:1000]}")

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        logger.error(f"LLM 返回的 JSON 解析失败: {e}\n原始内容: {llm_text[:500]}")
        return _build_fallback_response(market, as_of)

    try:
        # 解析 regime
        regime_str = data.get("regime", "MIXED")
        try:
            regime = RegimeType(regime_str)
        except ValueError:
            regime = RegimeType.MIXED

        # 解析 stability
        stability_str = data.get("stability", "MEDIUM")
        try:
            stability = StabilityLevel(stability_str)
        except ValueError:
            stability = StabilityLevel.MEDIUM

        # 解析 confidence
        confidence = float(data.get("confidence", 0.5))
        confidence = max(0, min(1, confidence))

        # 解析 recentSwitches
        recent_switches = []
        switches_raw = data.get("recentSwitches", [])
        logger.info(f"LLM 返回的 recentSwitches: {switches_raw}")

        for switch_data in switches_raw:
            try:
                ts = switch_data.get("ts")
                if isinstance(ts, str):
                    # 尝试解析日期字符串
                    try:
                        ts_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    except:
                        ts_dt = as_of
                else:
                    ts_dt = as_of

                recent_switches.append(RegimeSwitch(
                    from_regime=switch_data.get("from", "UNKNOWN"),
                    to_regime=switch_data.get("to", regime.value),
                    ts=ts_dt,
                    reason=switch_data.get("reason"),
                ))
            except Exception as e:
                logger.warning(f"解析 RegimeSwitch 失败: {e}")
                continue

        summary = data.get("summary", "")

        return RegimeStateResponse(
            market=market,
            asOf=as_of,
            regime=regime,
            stability=stability,
            confidence=confidence,
            recentSwitches=recent_switches,
            summary=summary,
        )
    except Exception as e:
        logger.error(f"构建 RegimeStateResponse 失败: {e}")
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
) -> RegimeStateResponse:
    """LLM 解析失败时的降级响应"""
    return RegimeStateResponse(
        market=market,
        asOf=as_of,
        regime=RegimeType.MIXED,
        stability=StabilityLevel.MEDIUM,
        confidence=0,
        recentSwitches=[],
        summary="状态机制分析暂时不可用，请稍后重试。",
    )
