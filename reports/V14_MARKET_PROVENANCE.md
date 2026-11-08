# V1.4 · T03 — 可信实时行情验收

- 执行时间（UTC）：2026-11-08 15:14 – 15:17
- 部署 SHA：`c340c120`
- 前置条件：T02 已证明获得真实行情（✅ 满足，才执行 T03）
- 测试数量：16 项 → **PASS 15 / FAIL 0 / BLOCKED 1**
- 结论：**PASS（带 1 项明确边界）**：来源可信、响应真实、未过期；不使用缓存冒充实时

## 1. 最新价与可追溯性

`GET /api/jev/market/ticker`（真实 Binance.US 24hr）：

| 标的 | lastPrice | bidPrice | askPrice |
|---|---|---|---|
| BTCUSDT | 76028.01000000 | 76029.23000000 | 76029.24000000 |
| ETHUSDT | 2239.70000000 | 2239.78000000 | 2239.83000000 |

响应 `meta` 溯源字段：

```
source_host      = api.binance.us
fetched_at_utc   = 2026-11-08T15:15:01.970Z
sha256           = c411d1f7c2dfef88ca71…
http_status      = 200
no_synthetic_data= true
data_class       = REAL_MARKET_DATA
no_api_key_required = true
channel          = REST_POLLING (never WS LIVE)
```

价格单位：报价币 USDT，标的 `BTCUSDT`/`ETHUSDT`，与冻结引擎 `ALLOWED_SYMBOLS` 命名一致；无跨交易所/跨市场无标识混价。

## 2. 盘口结构正确性

`GET /api/jev/market/depth?symbol=BTCUSDT&limit=10`：

| 检查 | 结果 |
|---|---|
| `lastUpdateId` | 3408954078（真实递增） |
| bids / asks | 10 / 10 |
| bids 价格严格递减 | PASS |
| asks 价格严格递增 | PASS |
| best_bid < best_ask | PASS，spread = 0.01 |
| 所有档位数量 > 0 | PASS |
| sha256 / fetched_at | `bdcd581c0ea940e763d1…` / 15:15:04.150Z |

## 3. K 线时间连续性与单位

`GET /api/jev/market/klines?symbol=BTCUSDT&interval=1m&limit=10`：

| 检查 | 结果 |
|---|---|
| 返回 bar 数 | 10 |
| openTime 相邻差值集合 | `{60000}`（1m 无缺口） |
| 严格递增 | PASS |
| 每 bar 字段数 | 12（Binance v3 kline 标准结构） |
| 首 bar OHLC | 76050.23 / 76050.23 / 76050.23 / 76050.23 |
| 末 bar close | 76028.01000000（与 ticker lastPrice 一致） |
| sha256 / fetched_at | `6ed5fefc61077422df07…` / 15:15:06.269Z |

末 bar close 与独立 ticker 请求价格一致，构成跨端点一致性佐证（两条独立 HTTP 请求，各自有 sha256）。

## 4. 时间戳与响应哈希可复核

所有 market 端点统一返回 `fetched_at_utc`、`latency_ms`、`bytes`、`sha256`、`source`、`endpoint`、`http_status`。状态端点 `/api/jev/market/status` 额外返回 `server_time_ms` 与 `clock_skew_ms`：

```
generated_utc  = 2026-11-08T15:13:03.671Z
live           = VERIFIED http=200 clock_skew_ms=42 sha256=3797859db6aaf863…
original       = UNAVAILABLE http=403 server=cloudflare
```

时钟偏差 42ms 远低于 60,000ms 阈值 → `VERIFIED / FRESH`。

## 5. 过期自动 STALE / 源站失败自动 UNAVAILABLE

**UNAVAILABLE（真实失败路径，非模拟）**：对未上市标的请求上游返回 400，网关不伪装 200：

```
GET /api/jev/market/ticker?symbols=["ORDIUSDT"] -> HTTP 502
  {"ok":false,"error":"MARKET_HTTP_400","code":"MARKET_UNAVAILABLE",
   "meta":{"label":"UNAVAILABLE"}}
GET /api/jev/market/depth?symbol=ORDIUSDT&limit=5 -> HTTP 502 (同上)
```

原 host 探测亦实测 `http=403 → verdict=UNAVAILABLE`，`datasource/audit` 自动把 `market_rest_original_host` 标为 `BLOCKED`。

**STALE（合成输入单元测试，明确标注非真实行情）**：真实源站时钟偏差 41–42ms，不触发 60s 阈值，因此不伪造一次"过期行情"来凑测试。改为把判定抽成纯函数 `classifyFreshness()` 并用合成输入验证全部分支：

`cloud/tests/market-freshness.check.mjs` → **10/10 PASS**

| 合成输入 | 期望 | 结果 |
|---|---|---|
| ok=200, skew=41 | VERIFIED | PASS |
| ok=200, skew=60000（边界） | VERIFIED | PASS |
| ok=200, skew=60001 | STALE | PASS |
| ok=200, skew=900000 | STALE | PASS |
| http=403/451/530 | UNAVAILABLE | PASS（3 例） |
| ok=false 但 skew 很小 | UNAVAILABLE | PASS |
| skew=null | VERIFIED | PASS |
| 自定义阈值 100 / skew 200 | STALE | PASS |

该测试文件头部明确声明「inputs are SYNTHETIC … does NOT generate, cache or claim any real market data」。

## 6. 不以历史缓存冒充实时行情

连续两次 `/api/jev/market/status`（间隔约 1.5s）：

```
probe1: fetched=15:16:05.687Z sha256=efe5f3988278cdbe server_time=1794150965970
probe2: fetched=15:16:07.149Z sha256=e4983a1adabc5e36 server_time=1794150967375
```

时间戳、响应哈希、上游 `serverTime` 均不同 → 每次都是真实新请求，无缓存层。`systemStatus()` 已删除原先"4 real server fills recorded …"这类静态宣称，改为从探测证据派生。

## 7. 诚实边界（BLOCKED 1 项）

| 项 | 说明 |
|---|---|
| 全球盘行情 | **BLOCKED**：本部署使用 **Binance.US 现货**，与 Binance.com 全球盘存在价差。`ORDIUSDT` / `1000SATSUSDT` 未上市，订单诚实拒绝（`MARKET_HTTP_400`, `fills=0`）。要覆盖这两个标的，需上游放行 Worker 出口或经官方许可接入另一真实源并完成标的价格单位/时间戳/来源验证，且不得无标识混价。 |

其余未通过项不在 T03 范围：`providers=BLOCKED`、`strategies=NO_DATA`、`thirty_day=UNVERIFIED`、`paper_equity_snapshots` 0 行（见 `V14_D1_SECURITY.md` / `V14_FINAL_ACCEPTANCE.md`）。

## 8. 证据路径

- `reports/evidence/T03_ticker.json`、`T03_depth.json`、`T03_klines.json` — 原始响应体 + 溯源 meta，脱敏
- `reports/evidence/T02_worker_diagnose.json` — 24 行真实出口矩阵（含四端点 sha256 与 `serverTime`）
- 单测：`apps/command_center_3d/cloud/tests/market-freshness.check.mjs`

> 报告中的哈希为响应体 SHA-256 前 16 位；完整 64 位保存在原始响应 `meta.sha256` 与证据 JSON 中，可复核。
