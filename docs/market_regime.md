# Market Regime 接口实现文档

## 1. 接口概览

| 项目 | 说明 |
|------|------|
| **路径** | `GET /v1/market/regime` |
| **功能** | 获取当前原油市场（WTI 或 Brent）所处的状态机制（Regime）及其稳定性评估 |
| **前端场景** | Market & Factor Radar 页面展示市场驱动类型卡片 |
| **认证** | Bearer Token（当前未强制） |

### 请求参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `market` | string (枚举) | ✅ | `WTI`（西德克萨斯中质原油）或 `Brent`（布伦特原油） |
| `asOf` | string (ISO-8601) | ❌ | 指定查询历史某一时刻的数据，省略则返回最新数据 |

### 响应字段

| 字段 | 类型 | 必填 | 含义 | 实现方式 |
|------|------|------|------|---------|
| `market` | string | ✅ | 市场类型 | 直接从请求参数传入 |
| `asOf` | datetime | ✅ | 数据时间点 | 请求参数传入或取当前时间 |
| `regime` | enum | ✅ | 当前市场状态机制类型 | LLM 分析判断 |
| `stability` | enum | ✅ | 稳定性等级 | LLM 分析判断 |
| `confidence` | number | ✅ | 置信度 (0-1) | LLM 分析判断 |
| `recentSwitches` | RegimeSwitch[] | ❌ | 近期状态转换记录 | LLM 分析推断 |
| `summary` | string | ❌ | 状态机制分析摘要 | LLM 生成 |

### 状态机制类型枚举值

| 值 | 含义 | 说明 |
|----|------|------|
| `DEMAND_DRIVEN` | 需求驱动 | 市场价格主要由需求端因素主导（如经济复苏、工业需求增长） |
| `SUPPLY_DRIVEN` | 供应驱动 | 市场价格主要由供应端因素主导（如 OPEC 减产、地缘政治） |
| `EVENT_DRIVEN` | 事件驱动 | 市场价格主要由突发事件主导（如战争、自然灾害、突发政策） |
| `FINANCIAL_DRIVEN` | 金融驱动 | 市场价格主要由金融市场因素主导（如美元走势、投机资金流向） |
| `MIXED` | 混合状态 | 多种因素共同作用，没有明显的主导因素 |

### 稳定性等级枚举值

| 值 | 含义 | 说明 |
|----|------|------|
| `HIGH` | 高稳定 | 状态稳定，预计将持续较长时间 |
| `MEDIUM` | 中稳定 | 状态相对稳定，但可能因某些因素变化 |
| `LOW` | 低稳定 | 状态不稳定，可能很快发生转换 |

### RegimeSwitch 结构

| 字段 | 类型 | 含义 |
|------|------|------|
| `from` | string | 原状态机制类型 |
| `to` | string | 新状态机制类型 |
| `ts` | datetime | 状态转换时间 |
| `reason` | string | 转换原因 |

---

## 2. 架构设计

### 分层架构

```
请求 → 路由层 → Service 层 → 数据层 (Redis / MySQL / 必应搜索 / LLM)
         │           │              │
    参数校验    业务计算逻辑    五级数据获取
```

### 文件结构

```
app/
├── api/v1/endpoints/
│   └── market.py               # 路由端点定义
├── schemas/
│   └── market.py               # Pydantic 数据模型
├── models/
│   └── market.py               # SQLAlchemy ORM 模型
├── services/market/
│   ├── __init__.py              # 模块导出
│   ├── market_service.py       # 市场快照服务
│   ├── driver_service.py       # 驱动因素服务
│   └── regime_service.py       # 状态机制服务（本文档）
└── tasks/
    ├── driver_sync.py          # 驱动因素定时任务
    └── regime_sync.py          # 状态机制定时任务
```

### 数据流程

```
用户请求 GET /v1/market/regime?market=WTI
│
▼
① 确定目标日期
│  01:10 之前 → 取前一天
│  01:10 之后 → 取当天
│
▼
② Redis 缓存查询
│  Key: market:regime:WTI:2026-02-15
│  ├─ 命中 → 直接返回（<50ms）
│  └─ 未命中 ↓
│
▼
③ MySQL 历史分析查询
│  SELECT * FROM market_regime_analysis
│  WHERE market='WTI' AND analysis_date='2026-02-15'
│  ├─ 命中 → 回填 Redis → 返回
│  └─ 未命中 ↓
│
▼
④ 实时生成分析
│  ├─ 必应 MCP 搜索最新新闻
│  │  Query: "WTI crude oil price OPEC supply demand trend 2026"
│  │
│  ├─ 收集市场行情 context
│  │  获取最近 60 天价格数据，计算波动率、趋势等
│  │
│  ├─ 调用 LLM (Qwen3) 分析
│  │  输入: 市场数据 + 新闻 + prompt
│  │  输出: JSON 格式的 regime 分析
│  │
│  └─ 解析 JSON 响应 ↓
│
▼
⑤ 写入 MySQL 持久化
│  Table: market_regime_analysis
│  TTL: 长期存储
│
▼
⑥ 写入 Redis 缓存
│  Key: market:regime:WTI:2026-02-15
│  TTL: 1800s（30 分钟）
│
▼
⑦ 返回 RegimeStateResponse
```

---

## 3. LLM 分析设计

### Prompt 结构

#### System Prompt

```
你是一名专业的原油市场高级分析师。你需要基于提供的市场数据和最新新闻搜索结果，
判断当前原油市场处于哪种状态机制（Regime）。

市场状态机制类型说明：
- DEMAND_DRIVEN: 需求驱动 - 市场价格主要由需求端因素主导
- SUPPLY_DRIVEN: 供应驱动 - 市场价格主要由供应端因素主导
- EVENT_DRIVEN: 事件驱动 - 市场价格主要由突发事件主导
- FINANCIAL_DRIVEN: 金融驱动 - 市场价格主要由金融市场因素主导
- MIXED: 混合状态 - 多种因素共同作用

输出 JSON 格式：
{
  "regime": "状态机制类型",
  "stability": "HIGH|MEDIUM|LOW",
  "confidence": 0.0-1.0,
  "recentSwitches": [
    {"from": "原状态", "to": "新状态", "ts": "ISO日期", "reason": "原因"}
  ],
  "summary": "中文摘要"
}

【重要】recentSwitches 必须返回数据：
1. 分析近30日价格走势，找出明显上涨或下跌阶段
2. 结合新闻中的供应、需求、事件、金融因素
3. 推断每个价格阶段的驱动因素类型
4. 至少返回最近1次状态转换（即使是推断的）
```

#### User Prompt

```
请分析以下原油市场数据，判断当前市场处于哪种状态机制：

=== 市场行情数据 ===
市场: WTI (西德克萨斯中质原油)
数据日期: 2026-02-15
最新收盘价: $73.50/桶
日变化: +1.20 (+1.66%)
20日年化波动率: 34.02%
近期趋势: 震荡 (2.15%)

近30个交易日走势：
...

=== 最新新闻搜索结果 ===
[必应搜索获取的新闻内容]
```

### LLM 配置

| 参数 | 值 |
|------|-----|
| 模型 | `qwen/qwen3-vl-235b-a22b-thinking` |
| API | OpenRouter |
| 超时 | 120s |
| 温度 | 默认（ factual） |

---

## 4. 存储设计

### MySQL 表结构

表名：`market_regime_analysis`

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | BIGINT AUTO_INCREMENT | 主键 |
| `market` | ENUM('WTI', 'Brent') | 市场类型 |
| `analysis_date` | DATE | 分析归属日期 |
| `content` | JSON | 完整分析结果 JSON |
| `created_at` | TIMESTAMP | 生成时间 |

**索引**：`UNIQUE(market, analysis_date)` — 防止同一市场同一日期重复分析

### Redis 缓存

| Key 格式 | 示例 | TTL |
|---------|------|-----|
| `market:regime:{market}:{date}` | `market:regime:WTI:2026-02-15` | 1800s (30分钟) |

---

## 5. 外部数据源

### 必应 MCP 搜索

| 参数 | 值 |
|------|-----|
| MCP URL | `https://mcp.api-inference.modelscope.net/925ce3579b2944/mcp` |
| 工具 | `bing_search` |
| 超时 | 60s |
| 结果截断 | 3000 字符 |

### 搜索查询策略

| 市场 | 查询关键词 |
|------|-----------|
| WTI | `WTI crude oil price OPEC supply demand trend 2026` |
| Brent | `Brent crude oil price OPEC supply demand trend 2026` |

### 触发时机

| 场景 | 描述 |
|------|------|
| **被动触发** | 用户请求时 Redis 和 MySQL 均无数据 → 实时调用 LLM 生成 |
| **定时任务** | 每天北京时间 01:10 自动生成当天分析（`tasks/regime_sync.py`） |

---

## 6. 定时任务

### 任务配置

| 项目 | 值 |
|------|-----|
| 文件 | `app/tasks/regime_sync.py` |
| 执行时间 | 每天 01:10 (北京时间) |
| 调度器 | APScheduler (AsyncIOScheduler) |
| 目标市场 | WTI, Brent |
| 注册位置 | `app/main.py` 的 `startup_event` |

### 任务逻辑 (app/tasks/regime_sync.py)

```python
async def sync_market_regime_task():
    """定时任务：生成所有市场的每日状态机制分析并入库"""
    logger.info("⏰ 开始执行每日市场状态机制分析任务...")
    target_date = date.today()

    # 获取 Redis 客户端实例
    try:
        redis_client = RedisClient.get_instance()
    except Exception as e:
        logger.error(f"❌ 获取 Redis 客户端失败: {e}")
        redis_client = None

    async with AsyncSessionLocal() as db:
        for market in ["WTI", "Brent"]:
            try:
                logger.info(f"🔄 正在分析 {market} 状态机制 - {target_date} ...")
                await generate_and_save_regime(
                    market=market,
                    target_date=target_date,
                    db=db,
                    redis_client=redis_client
                )
                logger.info(f"✅ {market} 状态机制分析完成")
            except Exception as e:
                logger.error(f"❌ {market} 状态机制分析失败: {e}")

    logger.info("🏁 每日市场状态机制分析任务结束")
```

### 调度器注册 (app/main.py)

```python
from app.core.scheduler import start_scheduler, scheduler
from app.tasks.driver_sync import sync_market_drivers_task
from app.tasks.regime_sync import sync_market_regime_task

# 注册每日 01:00 执行驱动因素分析任务
scheduler.add_job(
    sync_market_drivers_task,
    "cron",
    hour=1,
    minute=0,
    id="sync_market_drivers",
    replace_existing=True,
)

# 注册每日 01:10 执行状态机制分析任务
scheduler.add_job(
    sync_market_regime_task,
    "cron",
    hour=1,
    minute=10,
    id="sync_market_regime",
    replace_existing=True,
)
start_scheduler()
```

### 定时任务 vs 被动触发

| 触发方式 | 时间 | 行为 |
|---------|------|------|
| **定时任务** | 每天 01:10 | 自动生成当天分析，存入 MySQL + Redis |
| **用户请求** | 随时 | 查缓存 → 查数据库 → 实时生成（降级） |

---

## 7. 错误处理

| HTTP 状态码 | 触发条件 | 响应 |
|------------|---------|------|
| `200` | 正常返回 | `RegimeStateResponse` 对象 |
| `400` | market 参数无效 | `{"detail": "不支持的市场类型: XXX"}` |
| `422` | 参数格式错误 | FastAPI 自动校验错误 |
| `503` | LLM 调用失败或超时 | `{"detail": "获取状态机制分析失败: ..."}` |

### 降级策略

| 组件故障 | 降级行为 |
|---------|---------|
| Redis 不可用 | 跳过缓存，直接查 MySQL → LLM |
| MySQL 无数据 | 实时调用 LLM 生成并入库 |
| 必应搜索失败 | 使用默认提示语调用 LLM |
| LLM 调用失败 | 返回降级响应（regime: MIXED, confidence: 0） |

### 降级响应示例

```json
{
  "market": "WTI",
  "asOf": "2026-02-15T10:00:00Z",
  "regime": "MIXED",
  "stability": "MEDIUM",
  "confidence": 0,
  "recentSwitches": [],
  "summary": "状态机制分析暂时不可用，请稍后重试。"
}
```

---

## 8. 性能指标

| 场景 | 预期响应时间 |
|------|------------|
| Redis 缓存命中 | < 50ms |
| MySQL 查询命中（回填缓存） | 100 ~ 300ms |
| LLM 实时生成 | 3 ~ 10s |

---

## 9. 与其他接口的关系

| 接口 | 数据来源 | 用途 |
|------|---------|------|
| `/v1/market/snapshot` | yfinance | 价格、波动率、期限结构 |
| `/v1/market/drivers` | 必应 MCP + LLM | 驱动因素归因 |
| `/v1/market/regime` | 必应 MCP + LLM | 市场状态机制判断 |

### 数据共享

- **价格数据**：snapshot 和 regime 都使用 `market_daily_prices` 表
- **分析缓存**：drivers 和 regime 各自独立缓存（不同 key 前缀）
- **定时任务**：drivers (01:00) 和 regime (01:10) 分开执行，避免并发压力

---

## 10. 后续优化方向

| 优化点 | 说明 | 优先级 |
|-------|------|--------|
| 多模型对比 | 同时调用多个 LLM 进行分析，对比结果一致性 | 中 |
| 状态转换历史 | 持久化每次生成的分析，构建完整的 regime 演变历史 | 中 |
| 置信度校准 | 根据历史准确率调整 confidence 计算方式 | 低 |
| 前端展示优化 | 在 UI 上展示 recentSwitches 的时间线 | 低 |
