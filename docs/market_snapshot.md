# Market Snapshot 接口实现文档

## 1. 接口概览

| 项目 | 说明 |
|------|------|
| **路径** | `GET /v1/market/snapshot` |
| **功能** | 获取指定原油市场（WTI 或 Brent）的实时快照数据 |
| **前端场景** | Market & Factor Radar 页面展示，包含价格卡片和走势迷你图 |
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
| `asOf` | datetime | ✅ | 数据快照的时间点 | 请求参数传入或取当前 UTC 时间 |
| `lastPrice` | number | ✅ | 最新原油价格（USD/桶） | yfinance 获取的最新交易日收盘价 |
| `change1d` | number | ✅ | 相对前一交易日收盘价的绝对变化 | `lastPrice - previousClose` |
| `pctChange1d` | number | ✅ | 相对前一交易日收盘价的百分比变化 | `(change1d / previousClose) × 100` |
| `volatility20d` | number | ✅ | 20 交易日年化波动率 | 日收益率标准差 × √252 |
| `termStructure.state` | enum | ✅ | 期限结构状态 | 比较近月、远月合约价格 |
| `termStructure.spreadFrontSecond` | number | ✅ | 近远月合约价差 | `近月价格 - 远月价格` |
| `history` | PricePoint[] | ❌ | 历史价格数据（UI 走势图） | 最近 30 个交易日的 `{ts, value}` 数组 |

### 期限结构枚举值

| 值 | 含义 | 判定条件 |
|----|------|---------|
| `BACKWARDATION` | 现货升水（近月合约 > 远月合约） | `spreadFrontSecond > 0.05` |
| `CONTANGO` | 期货升水（近月合约 < 远月合约） | `spreadFrontSecond < -0.05` |
| `FLAT` | 持平 | `|spreadFrontSecond| ≤ 0.05` |

---

## 2. 架构设计

### 分层架构

```
请求 → 路由层 → Service 层 → 数据层 (Redis / MySQL / yfinance)
         │           │              │
    参数校验    业务计算逻辑    三级数据获取
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
│   └── market_service.py       # 核心业务逻辑
└── tasks/
    └── market_data_sync.py     # 定时数据同步任务
```

### 数据流程

```
用户请求 GET /v1/market/snapshot?market=WTI
│
▼
① Redis 缓存查询
│  Key: market:snapshot:WTI:2026-02-13
│  ├─ 命中 → 直接返回（<100ms）
│  └─ 未命中 ↓
│
▼
② MySQL 历史数据查询
│  SELECT * FROM market_daily_prices
│  WHERE market='WTI' AND trade_date <= ?
│  ORDER BY trade_date DESC LIMIT 60
│  ├─ 数据 ≥ 20 条 → 跳到 ④
│  └─ 数据不足 ↓
│
▼
③ yfinance 数据补充
│  Ticker: CL=F (WTI) / BZ=F (Brent)
│  获取最近 70 天历史数据
│  ├─ 去重后写入 MySQL（持久化）
│  └─ 重新从 MySQL 查询 ↓
│
▼
④ 计算快照指标
│  ├─ lastPrice / change1d / pctChange1d
│  ├─ volatility20d（年化波动率）
│  ├─ termStructure（期限结构）
│  └─ history（最近 30 天走势）
│
▼
⑤ 写入 Redis 缓存
│  当天数据: TTL = 300s（5 分钟）
│  历史数据: TTL = 86400s（24 小时）
│
▼
⑥ 返回 MarketSnapshotResponse
```

---

## 3. 核心算法说明

### 3.1 波动率计算（volatility20d）

波动率是衡量价格波动剧烈程度的指标，采用**历史波动率**方法：

**Step 1.** 取最近 21 个交易日的收盘价 `P₁, P₂, ..., P₂₁`

**Step 2.** 计算 20 个日收益率：

$$r_i = \frac{P_{i+1} - P_i}{P_i}$$

**Step 3.** 计算日收益率的标准差：

$$\sigma_{daily} = \sqrt{\frac{1}{n} \sum_{i=1}^{n} (r_i - \bar{r})^2}$$

**Step 4.** 年化（一年约 252 个交易日）：

$$\sigma_{annual} = \sigma_{daily} \times \sqrt{252}$$

**典型值范围**：原油波动率通常在 **0.15 ~ 0.50** 之间（即 15% ~ 50%）。

### 3.2 期限结构判定（termStructure）

期限结构反映市场对未来供需的预期：

| 状态 | 条件 | 市场含义 |
|------|------|---------|
| **BACKWARDATION** | 近月 > 远月（价差 > $0.05） | 供应紧张，现货需求强劲 |
| **CONTANGO** | 近月 < 远月（价差 < -$0.05） | 供应充足，存储成本推高远期价格 |
| **FLAT** | 价差绝对值 ≤ $0.05 | 市场相对平衡 |

> **当前限制**：yfinance 的 `CL=F` / `BZ=F` ticker 仅返回近月合约数据，缺少次近月合约。当前实现中 `second_month_price` 暂以 `close_price × 0.99` 估算，未来需接入 CME Group 数据获取真实次近月价格。

### 3.3 历史走势数据（history）

- 用途：前端 UI **sparkline 走势迷你图**
- 数据量：最近 **30 个交易日**的收盘价
- 格式：`[{ts: "2026-01-15T00:00:00Z", value: 72.30}, ...]`
- 按日期升序排列，便于前端直接绑定到图表组件

---

## 4. 存储设计

### MySQL 表结构

表名：`market_daily_prices`

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | BIGINT AUTO_INCREMENT | 主键 |
| `market` | ENUM('WTI', 'Brent') | 市场类型 |
| `trade_date` | DATE | 交易日期 |
| `open_price` | DECIMAL(10,2) | 开盘价 |
| `high_price` | DECIMAL(10,2) | 最高价 |
| `low_price` | DECIMAL(10,2) | 最低价 |
| `close_price` | DECIMAL(10,2) | 收盘价 |
| `volume` | BIGINT | 成交量 |
| `front_month_price` | DECIMAL(10,2) | 近月合约价格 |
| `second_month_price` | DECIMAL(10,2) | 次近月合约价格 |
| `created_at` | TIMESTAMP | 数据入库时间 |

**索引**：`UNIQUE(market, trade_date)` — 防止同一市场同一日期重复数据

### Redis 缓存

| Key 格式 | 示例 | TTL |
|---------|------|-----|
| `market:snapshot:{market}:{date}` | `market:snapshot:WTI:2026-02-13` | 当天 300s / 历史 86400s |

---

## 5. 外部数据源

### yfinance Ticker 映射

| 市场 | Ticker | 说明 |
|------|--------|------|
| WTI | `CL=F` | NYMEX WTI Crude Oil Futures |
| Brent | `BZ=F` | ICE Brent Crude Oil Futures |

### 数据获取方式

```python
import yfinance as yf

ticker = yf.Ticker("CL=F")
df = ticker.history(start="2026-01-01", end="2026-02-14")
# 返回 DataFrame: Open, High, Low, Close, Volume
```

### 触发时机

| 场景 | 描述 |
|------|------|
| **被动触发** | 用户请求时 MySQL 数据不足 20 天 → 自动调用 yfinance 补数据 |
| **定时任务** | 每天北京时间 5:30 主动同步前一交易日数据（`tasks/market_data_sync.py`） |

---

## 6. 错误处理

| HTTP 状态码 | 触发条件 | 响应 |
|------------|---------|------|
| `200` | 正常返回 | `MarketSnapshot` 对象 |
| `400` | market 参数无效（非 WTI/Brent） | `{"detail": "不支持的市场类型: XXX"}` |
| `422` | 参数格式错误或缺少必填参数 | FastAPI 自动校验错误 |
| `503` | yfinance 调用失败或数据不可获取 | `{"detail": "获取市场数据失败: ..."}` |

### 降级策略

| 组件故障 | 降级行为 |
|---------|---------|
| Redis 不可用 | 跳过缓存读写，直接查 MySQL → yfinance |
| MySQL 数据不足 | 自动调用 yfinance 补充数据并入库 |
| yfinance 超时/错误 | 返回 503 错误 |

---

## 7. 性能指标

| 场景 | 预期响应时间 |
|------|------------|
| Redis 缓存命中 | < 50ms |
| MySQL 查询（有数据） | 100 ~ 300ms |
| yfinance 首次拉取 | 1 ~ 3s |

---

## 8. 后续优化方向

| 优化点 | 说明 | 优先级 |
|-------|------|--------|
| 次近月合约价格 | 接入 CME Group API 获取真实的 `second_month_price` | 高 |
| 盘中实时价格 | 当前只支持收盘价，可接入实时行情 API | 中 |
| 数据回填命令 | 提供 CLI 命令批量回填历史数据 | 中 |
| 监控告警 | yfinance 同步失败时发送告警通知 | 低 |
