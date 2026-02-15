# Market Events 接口实现文档

## 1. 接口概览

| 项目 | 说明 |
|------|------|
| **路径** | `GET /v1/market/events` |
| **功能** | 获取指定原油市场（WTI 或 Brent）近期影响市场的重要事件列表 |
| **前端场景** | Event Lens 页面展示，包含事件卡片时间线 |
| **认证** | Bearer Token（当前未强制） |

### 请求参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `market` | string (枚举) | ✅ | `WTI`（西德克萨斯中质原油）或 `Brent`（布伦特原油） |
| `asOf` | string (ISO-8601) | ❌ | 指定分析日期，省略则返回最新分析（每日 1:20 AM 更新） |
| `windowDays` | integer | ❌ | 回溯时间窗口（天），默认 7 |

### 响应字段

| 字段 | 类型 | 必填 | 含义 |
|------|------|------|------|
| `market` | string | ✅ | 市场类型 |
| `asOf` | datetime | ✅ | 分析生成时间 |
| `windowDays` | integer | ✅ | 回溯时间窗口（天） |
| `events` | EventCard[] | ✅ | 事件卡片列表 |

### EventCard 对象结构

| 字段 | 类型 | 说明 |
|------|------|------|
| `eventId` | string | 事件唯一标识（如 `evt_opec_meeting_20260210`） |
| `ts` | datetime | 事件发生时间 |
| `title` | string | 事件标题（中文） |
| `type` | enum | 事件类型：`GEOPOLITICS`, `POLICY`, `SUPPLY`, `DEMAND`, `MACRO`, `OTHER` |
| `impact` | enum | 影响方向：`UP`（利多）, `DOWN`（利空）, `UNCERTAIN`（不确定） |
| `linkedFactors` | string[] | 关联的驱动因素 ID 列表 |
| `evidence` | string[] | 证据来源列表 |

---

## 2. 架构设计

### 分层架构

```
请求 → 路由层 → Service 层 → 数据层 (Redis / MySQL / MCP / LLM)
         │           │              │
    参数校验    缓存优先策略    多源数据聚合与分析
```

### 文件结构

```
app/
├── api/v1/endpoints/
│   └── market.py               # 路由端点定义
├── schemas/
│   └── market.py               # Pydantic 数据模型 (EventsResponse)
├── models/
│   └── market.py               # SQLAlchemy ORM 模型 (MarketEventAnalysis)
├── services/market/
│   └── event_service.py        # 核心业务逻辑 (MCP 搜索 + LLM 事件识别)
└── tasks/
    └── event_sync.py           # 定时分析任务 (每日 1:20 AM)
```

### 数据流程

```
用户请求 GET /v1/market/events?market=WTI&windowDays=7
│
▼
① Redis 缓存查询 (优先)
│  Key: market:events:WTI:2026-02-15
│  ├─ 命中 → 直接返回（<50ms）
│  └─ 未命中 ↓
│
▼
② MySQL 数据库查询
│  SELECT * FROM market_event_analysis
│  WHERE market='WTI' AND analysis_date = '2026-02-15'
│  ├─ 命中 → 回填 Redis → 返回
│  └─ 未命中 ↓
│
▼
③ 实时生成 (Fallback / 首次生成)
│  A. Bing MCP 搜索: "WTI crude oil geopolitics OPEC policy events 2026"
│  B. 收集 Context: 从数据库获取最近 10 天价格走势
│  C. LLM 分析: 调用 Qwen3-VL (via OpenRouter) 识别并分类事件
│  D. 入库: 存入 MySQL (持久化)
│  E. 缓存: 写入 Redis (TTL=30min)
│
▼
④ 返回 EventsResponse
```

---

## 3. 核心逻辑说明

### 3.1 日期判定逻辑

每日 **01:20 AM (Asia/Shanghai)** 设为新一天的分界线（在 drivers 01:00 和 regime 01:10 之后）：

- **请求时间 < 01:20**: 视为查询 **前一天** 的数据。
- **请求时间 ≥ 01:20**: 视为查询 **当天** 的数据。

### 3.2 事件识别 (LLM)

使用 **Qwen3-VL-235B** 模型进行事件识别，Prompt 包含：
- **Role**: 原油市场高级分析师
- **Context**: 必应搜索到的最新新闻 + 最近 10 天价格走势
- **Output**: 强制 JSON 格式，包含 `events` 事件卡片数组
- **要求**: 识别 3-10 个重要事件，按时间倒序排列

### 3.3 定时任务

- **频率**: 每日 01:20 AM
- **任务**: 遍历所有市场 (WTI, Brent)，主动触发 `generate_and_save_events`
- **默认窗口**: 7 天
- **目的**: 确保用户查看时已有现成数据

---

## 4. 存储设计

### MySQL 表结构

表名：`market_event_analysis`

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | BIGINT AUTO_INCREMENT | 主键 |
| `market` | ENUM('WTI', 'Brent') | 市场类型 |
| `analysis_date` | DATE | 分析归属日期 |
| `content` | JSON | 完整事件分析结果（EventsResponse JSON） |
| `created_at` | TIMESTAMP | 生成时间 |

**索引**: `UNIQUE(market, analysis_date)` — 保证每日一份分析报告。

### Redis 缓存

| Key 格式 | TTL | 说明 |
|---------|-----|------|
| `market:events:{market}:{date}` | 1800s (30分钟) | 缓存热数据，减轻数据库压力 |

---

## 5. 外部服务依赖

| 服务 | 用途 | 关键配置 |
|------|------|----------|
| **Bing MCP** | 实时新闻搜索 | `BING_YING_SEARCH` Token |
| **OpenRouter** | LLM 事件识别 (Qwen3) | `EASYIMPR_API_KEY` |

---

## 6. 错误处理与降级

| 场景 | 行为 | 响应示例 |
|------|------|----------|
| **Redis 故障** | 降级查 MySQL | 正常返回 |
| **MySQL 无数据** | 触发实时生成 | 正常返回（延迟较高） |
| **Bing/LLM 失败** | 返回空事件列表 | `{"events": []}` |

---

## 7. 性能指标

| 场景 | 预期响应时间 |
|------|------------|
| Redis 命中 | < 50ms |
| MySQL 命中 | < 100ms |
| 实时生成 (LLM) | 20s ~ 60s |
