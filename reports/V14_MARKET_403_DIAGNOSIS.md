# V1.4 · T02 — Binance HTTP 403 根因诊断

- 执行时间（UTC）：2026-11-08 15:10 – 15:17
- 部署 SHA：`c340c120`（诊断端点首次上线）、复核于 `ee237c2d` / `4b4dec81`
- 测试数量：本地 A + 执行环境 B 共 8 次；Worker 边缘 C 共 24 次真实请求（2 host × 4 endpoint × 3 UA）；另有 20 目标广域探测（Stage 2）
- 结论：**根因确定（VERIFIED）— 上游按出口 IP 地域/ASN 拒绝，与 UA、路径、DNS/TLS、Workers 代码、网关路由均无关**

## 1. 三环境对照

### A/B · 本地 Windows 与 WorkBuddy 执行环境（`data-api.binance.vision`）

| 端点 | HTTP | 延迟(ms) | 字节 | Content-Type | SHA256(前16) |
|---|---|---|---|---|---|
| `/api/v3/time` | 200 | 1111 | 28 | application/json;charset | `3913d4f3a113748e` |
| `/api/v3/ticker/price?symbol=BTCUSDT` | 200 | 408 | 45 | application/json;charset | `69c1310c4ff1c105` |
| `/api/v3/depth?symbol=BTCUSDT&limit=5` | 200 | 1117 | 367 | application/json;charset | `4a5cbbfc02e097a5` |
| `/api/v3/klines?...interval=1m&limit=10` | 200 | 1045 | 1663 | application/json;charset | `2e1db47bd386c505` |

真实响应体样本：`{"serverTime":1794150160642}`、`{"symbol":"BTCUSDT","price":"76068.61000000"}`。

→ **源站在线、四个路径均有效、DNS/TLS 正常、无需 API key**。本地环境 B 与 A 走同一出口，结果一致。

### C · Cloudflare Worker 实际运行环境（决定性）

端点 `/api/jev/market/diagnose`，返回 200，`15,132` 字节，耗时 2.91s，`24` 次真实 Worker 出口请求。

| host | 请求数 | 成功 | HTTP 状态集合 | custom UA | empty UA | browser UA |
|---|---|---|---|---|---|---|
| `data-api.binance.vision` | 12 | 0 | `[403]` | `[403]` | `[403]` | `[403]` |
| `api.binance.us` | 12 | 12 | `[200]` | `[200]` | `[200]` | `[200]` |

四个端点逐项（engine host，三种 UA 均相同）：

```
time          [403, 403, 403]
ticker_price  [403, 403, 403]
depth         [403, 403, 403]
klines        [403, 403, 403]
```

派生结论（由矩阵实算，非硬编码）：`ua_changes_http_outcome = false`。

403 响应头与响应体（判定拒绝方）：

```
server : cloudflare
cf-ray : a57583d5bfb5f8d1-SEA
content-type : text/html
body   : <html><head><title>403 Forbidden</title></head>
         <body><center><h1>403 Forbidden</h1></center></body>
```

→ 403 是**上游 Binance 自己的 Cloudflare WAF** 返回的 HTML 页，不是 JEV 网关生成、不是 Workers 抛错、不是源站 JSON 错误码。

## 2. 逐项排查结论

| 假设 | 证据 | 结论 |
|---|---|---|
| 源站宕机 | 本地四端点 200 + `serverTime` 真实 | 排除 |
| 接口路径错误 | 本地四路径全 200；Worker 四路径一致 403 | 排除 |
| DNS / TLS | 本地解析与握手成功；Worker 拿到上游 `cf-ray` | 排除 |
| 请求头 / User-Agent | 3 种 UA × 4 端点 × 2 host，同 host 结果完全一致 | **排除** |
| 代理链 | 本地经沙箱代理 200；Worker 为独立出口 403 | 差异在出口本身 |
| Workers 网络配置 / 网关路由 | `api.binance.us` 同一 Worker 同一代码 12/12 200 | 排除 |
| **出口 IP 地域 / ASN 策略** | 本地出口 200 vs Worker SEA colo 出口 403；上游 WAF 返回 403 HTML | **根因** |

补充证据（Stage 2 广域探测，20 个硬编码目标，Worker 边缘）：

| 目标 | HTTP |
|---|---|
| `api.binance.com` / `api1–4` | **451** `Service unavailable from a restricted location` |
| `data-api.binance.vision` | **403** |
| `api.binance.vision` | 530 (CF 1016) |
| `api.binance.us` | **200** |
| Coinbase / Kraken | 200（格式不兼容，未采用） |
| CoinGecko | 429（限流，未采用） |

451 的文案直接指向「restricted location」，与地域/ASN 判断一致。

**明确否定的方案**：用户已指出网关 `fetchVerified()` 早已发送自定义 User-Agent（源码 L84 `user-agent: JEV-Trading-Desk/1.0 (public REST)`）。本轮 24 次对照实验实证 **加/改 UA 对 403 无任何影响**，故"添加 UA"不作为修复方案，也不再重复提出。

## 3. 已落地的合规替代路径（引擎零改动）

`api.binance.us` 是 Binance 官方实体、公开 REST、Binance API v3 格式兼容。冻结引擎在 `paper-server.mjs:88` 原生预留 `dbFetch(env) => env.__fetch || fetch`。网关仅注入 `env.__fetch`，把 host 重指向 `api.binance.us`，**响应体原样透传**（无缓存、无价格合成、无重算）。

冻结引擎与 D1 账本零改动证明：`git diff --stat -- apps/command_center_3d/server/engine/` 为空；`health.engine_modified=false`。

诚实边界：

- `api.binance.us` 是 **Binance.US 现货**，不是 Binance.com 全球盘；报告与 UI 均标注 venue，不混称。
- `ORDIUSDT` / `1000SATSUSDT` 在 Binance.US 未上市 → 多标的 ticker 过滤为 BTC/ETH；这两个标的的订单由上游 400 → 网关 `MARKET_UNAVAILABLE` → 引擎 `REJECTED`，`fills=0`，**绝不伪造成交**。
- 原 `403` 记录保留在 `datasource/audit` 的 `market_rest_original_host`，且现在由实时探测生成而非硬编码。

## 4. 你点名的「行情状态硬编码」问题 — 已修复

修复前：`systemStatus()` 与 `dataSourceAudit()` 直接写死 `403` / `UNAVAILABLE`（Stage 2 又翻成 `VERIFIED` 常量）—— 无论哪种都是常量宣称，源站恢复或宕机时状态都不会变。

修复后（本轮）：

- 新增 `marketStatusEvidence()`：**每次请求时**从 Worker 边缘真实探测 `api.binance.us/api/v3/time` 与 `data-api.binance.vision/api/v3/ticker/price`，返回 `http_status`、`sha256`、`fetched_at_utc`、`latency_ms`、`clock_skew_ms`、`server_header`。
- 新增纯函数 `classifyFreshness()`：`非 2xx → UNAVAILABLE`、`时钟偏差 > 60000ms → STALE`、否则 `VERIFIED`。
- `systemStatus()` / `dataSourceAudit()` 改为接收该证据，**不再包含任何硬编码 LIVE/403 声明**。
- 新增 `/api/jev/market/status`（原始证据）与 `/api/jev/market/diagnose`（对照矩阵）。

实测（部署 `c340c120`）：

```
market/status live     : VERIFIED http=200 skew_ms=42 sha256=3797859db6aaf863 server_time=1794150965970
market/status original : UNAVAILABLE http=403 server=cloudflare
system/status          : "VERIFIED (probe-derived) — source=api.binance.us http=200 ..."
datasource/audit       : market_rest VERIFIED / market_rest_original_host BLOCKED http=403
```

连续两次探测 `sha256` 与 `server_time` 均不同（`efe5f398…` → `e4983a1a…`），证明**无缓存、不以历史数据冒充实时**。

## 5. PASS / FAIL / BLOCKED

| # | 项 | 状态 |
|---|---|---|
| 1 | 三环境四端点逐项记录状态/类型/延迟/哈希/来源/异常类别 | PASS |
| 2 | 排除 UA / 路径 / DNS-TLS / 源站 / 网关 / Worker 配置 | PASS |
| 3 | 定位拒绝方为上游 Cloudflare WAF（server/cf-ray/HTML body） | PASS |
| 4 | 确认替代源为官方 v3 兼容且真实可用（12/12 200） | PASS |
| 5 | 状态硬编码改为探测驱动（含 STALE/UNAVAILABLE 自动标记） | PASS |
| 6 | 冻结撮合引擎未修改、未伪造 200 | PASS |
| — | 让 `data-api.binance.vision` 在 Worker 出口可用 | **BLOCKED**（需上游放行 Cloudflare 出口 IP，非本方可控） |

## 6. 证据路径

- `reports/evidence/T02_worker_diagnose.json` — 24 行完整矩阵（含每行 sha256、延迟、响应头、解析摘要），脱敏
- 源码：`apps/command_center_3d/functions/api/jev/[[path]].js`（`marketStatusEvidence` / `runMarketDiagnosis` / `classifyFreshness`）
- 单测：`apps/command_center_3d/cloud/tests/market-freshness.check.mjs`

> 全部记录不含 Basic Auth 密码、Cookie、Session ID 或密钥。
