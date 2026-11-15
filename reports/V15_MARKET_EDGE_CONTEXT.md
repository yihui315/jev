# JEV V1.5 · P3 补充 — 行情源的「网络上下文」必须作为证据字段

**结论：`network_context` 是第一等证据字段。三个上下文（本地 52498 / 本地 10808 /
Cloudflare 边缘运行时）连通性结论不同，但交易所业务结论一致；任一上下文的连通性
不得泛化为「交易所不可用 / 未上市」，也不得据本地结果推翻 V1.4。**

完整逐 symbol 证据见 `reports/V15_MARKET_COVERAGE.md` 与 `reports/evidence/market/`
（56 份脱敏证据 JSON）。

## 1. 三上下文对照

| 上下文 | 采集窗口 (UTC) | `api.binance.us` | `data-api.binance.vision` | 标注 |
|---|---|---|---|---|
| **A1** 本地沙箱 proxy `127.0.0.1:52498` | 04:24:32–04:24:52 | 502 CONNECT tunnel failed（17/17 TRANSPORT_FAILURE） | http 200 / nginx / json（17/17） | `LOCAL_SANDBOX_TRANSPORT_FAILURE / UNVERIFIED`（**仅该代理路径**） |
| **A2** 本地沙箱 proxy `127.0.0.1:10808` | 04:36:20–04:36:27 | **http 200 / nginx / json**（BTC/ETH 五端点 200；ORDI/1000SATS 4/4 端点 `400 -1121 Invalid symbol`） | http 200（time 对照） | **VERIFIED**（本轮实测） |
| **B** Cloudflare Pages 边缘运行时（JEV Staging 部署目标） | 04:24:20 | **http 200 / VERIFIED / FRESH / skew 38ms** | http 403 / text-html / UNAVAILABLE | **VERIFIED**（主理人实测，非沿用） |

A1 与 A2 的差异证明「502」是**本地代理路径属性**，不是交易所属性；A2 与 B 相互印证
`api.binance.us` 在可达路径与真实部署运行时都可用。V1.4 结论**保留，不推翻**。

## 2. 边缘运行时实测明细（主理人复测，非沿用 V1.4）

端点：`https://jev-cloud-staging.pages.dev/api/jev/market/status`（预览 Basic 认证，http 200），
由网关 `marketStatusEvidence()` 在**请求当时**做真实 Worker-edge HTTP 探测（无缓存、无常量 LIVE 声明）。

| 字段 | live（主源） | original（对照） |
|---|---|---|
| `source_host` | `api.binance.us` | `data-api.binance.vision` |
| `endpoint` | `/api/v3/time` | `/api/v3/ticker/price?symbol=BTCUSDT` |
| `ok` | true | false |
| `http_status` | 200 | 403 |
| `content_type` | `application/json;charset=UTF-8` | `text/html` |
| `fetched_at_utc` | 2026-11-09T04:24:20.589Z | 同上 |
| `latency_ms` | 384 | 385 |
| `bytes` | 28 | 118 |
| `sha256`(body) | `63ea381f9f35483cc9ea449c0e382130c1abb06d5868084cf6cadf676518b9ec` | `58bf2215b395dcac74c009aa98701854e43cbe54a1cd3a95fee6a647ca9910d4` |
| `server_time_ms` | `1794198260935` | null |
| `clock_skew_ms` | 38 | null |
| `freshness` / `verdict` | `FRESH` / **`VERIFIED`** | `UNAVAILABLE` / **`UNAVAILABLE`** |

判定策略（网关 `classifyFreshness()`）：clock skew ≥ 60000ms → `STALE`；非 2xx / 超时 → `UNAVAILABLE`；否则 `FRESH`。

## 3. ORDIUSDT / 1000SATSUSDT 的 BLOCKED 现已有可复现证据

A2（10808）对二者 `exchangeInfo` / `ticker/price` / `bookTicker` / `trades` **4/4 端点**均返回
`400 {"code":-1121,"msg":"Invalid symbol."}`（响应体 sha16 `882fbec4a8b18dbb`）。
这是**交易所业务判定（未上市）**，不是网络传输失败，因此 **BLOCKED 可复现**，
V1.4 的 BLOCKED 不再是「继承结论」。B 上下文边缘探测亦印证。

P3 另在 `data-api.binance.vision`（**Binance Global spot**）实测二者 `status=TRADING` 并有真实价格。
但这是**另一个交易所主体/成交簿/法域**，只能作为「候选来源」记录，**不自动解除** Binance.US 维度的 BLOCKED，
且 1000SATS 在该来源的行情实测为 `STALE`（滞后约 96.5s > 60s 阈值）。

## 4. 对 JEV 的落地约束

1. **部署目标运行时**：JEV Staging 撮合/行情走 `env.__fetch → api.binance.us`，以 B 上下文实测为当前有效证据。
2. **本地开发/CI Runner**：若走 52498，`api.binance.us` 传输失败，本地测试必须使用注入的 fetcher/fixture，
   并把 `network_context=local_sandbox_proxy_52498`、`data_quality=UNKNOWN` 明确标注；不得伪装成行情成功。
   走 10808 时可取得真实行情，但必须记录该上下文。
3. **新增来源**：若接入 `data-api.binance.vision`，每条价格必须携带
   `network_context` / `exchange` / `market` / `symbol` / `base_asset` / `quote_asset` / `price` /
   `price_unit` / `price_ts` / `price_ts_source` / `book_ts` / `book_ts_source` / `trade_ts` /
   `source_endpoint` / `received_ts` / `http_status` / `body_sha256` / `probe_ts_utc`；
   **禁止**与 Binance.US 价格拼接成同一条权益曲线，**禁止**互为兜底或平替。
4. 1000SATS 的 `baseAsset=1000SATS` 单位倍数必须显式化，禁止与按 1 SATS 计价的 `SATSUSDT` 混用（相差 1000 倍）。
5. `TRANSPORT_FAILURE` / 非 2xx / `-1121` 是三类不同失败，不得互相改写。

## 5. 判定

- 目标运行时主源 `api.binance.us` = **VERIFIED**（A2 本地实测 + B 边缘实测双重印证）。
- BTCUSDT / ETHUSDT 在 Binance.US = **VERIFIED**（A2 五端点 200，含真实成交与盘口时间戳）。
- ORDIUSDT / 1000SATSUSDT 在 Binance.US = **BLOCKED**（`-1121` 可复现）。
- Binance Global spot 作为新增来源 = **CONDITIONAL / NO-GO**，接入决策归主理人；未满足 §4 字段约束前不接入。
