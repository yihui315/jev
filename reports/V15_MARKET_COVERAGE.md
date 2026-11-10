# JEV V1.5 — P3 市场覆盖核查报告（MARKET_COVERAGE）

| 项 | 值 |
| --- | --- |
| 角色 | market-auditor（P3 市场覆盖核查） |
| 范围 | BTCUSDT / ETHUSDT / ORDIUSDT / 1000SATSUSDT 四交易对在可用行情源上的真实可用市场 |
| 探测窗口（UTC） | A1 2026-11-09 04:24:32~04:24:52；A2 2026-11-09 04:36:20~04:36:27；B 2026-11-09 04:24:18~04:24:20（PROVIDED） |
| 环境 | Windows 11 / Git Bash / Python 3.13.14 / curl 8.4.0 |
| 出站代理（双代理） | `52498`（默认 `HTTP_PROXY/HTTPS_PROXY`）、`10808`（team-lead 指定第二代理，GitHub API 走它） |
| 凭据 | 未读取、未发送、未创建、未撤销任何凭据/Token；边缘证据的 Basic 预览认证未由本角色访问 |
| 写操作 | 未对任何远程系统写入；未执行任何 GitHub / Cloudflare 写操作；未执行 git commit/push；未修改 V1.4 报告 |
| 本地产物 | `D:/jev-v15-wt/v15-market/` 下 `probe_market.py`、`probe_binance_us_10808.py`、`MARKET_COVERAGE.md`、`evidence/*.json` |

> **醒目声明（硬规则）**：
> 1. **`api.binance.us`（Binance.US）与 `data-api.binance.vision`（Binance Global spot）是不同交易市场**，不同成交簿、不同法域、不同价格序列。
> 2. 每一条价格必须携带 `exchange` / `market` / `symbol` / `base_asset` / `quote_asset` / `price_unit` / `price_ts` / `book_ts` / `source_endpoint` / `network_context` 及价格单位与真实成交、盘口时间戳。
> 3. **禁止与 `Binance.US` 价格混用或拼接成同一条权益曲线**；禁止跨市场填充 / 平替 / 兜底。
> 4. **网络上下文必须作为证据字段**：本地沙箱（52498 / 10808）与 Cloudflare 边缘运行时是三个独立上下文，任一上下文的连通性结论不得泛化到交易所本身或其它上下文。

---

## 0. 结论摘要（收敛版）

1. **连通性按网络上下文分裂，不是交易所属性**。同一对来源在「本地沙箱」与「Cloudflare 边缘运行时」的连通性正好相反；因此**保留 V1.4 结论，不予推翻**。
2. **上下文 A1（本地沙箱 proxy 52498，本会话实测）**：`api.binance.us` 17/17 请求 `TRANSPORT_FAILURE`（502 CONNECT / SSL EOF）→ **`LOCAL_SANDBOX_TRANSPORT_FAILURE / UNVERIFIED`**，**仅代表该代理路径**；`data-api.binance.vision` 17/17 `HTTP 200` → `VERIFIED`。
3. **上下文 A2（本地沙箱 proxy 10808，本会话实测）**：`api.binance.us` **HTTP 200 / nginx / application/json 完整可达**（17 请求：9 `OK` + 8 `API_ERROR`）。**故 team-lead 预设的「两条代理都 502」前提不成立**，不写 `LOCAL_SANDBOX_TRANSPORT_FAILURE` 作为整体结论。
4. **上下文 B（Cloudflare Pages 边缘运行时 / Staging，team-lead 提供，本会话未独立复现）**：`api.binance.us` `http 200 / VERIFIED / FRESH`（clock skew 38ms）；`data-api.binance.vision` `http 403 / text/html / UNAVAILABLE`。**V1.4 主源结论保留**。
5. **交易所业务结论跨上下文一致**：`api.binance.us` 上 BTC/ETH `TRADING`，ORDI/1000SATS 返回 **`-1121 Invalid symbol`** —— 本地 10808 实测（各 4/4 端点）与边缘 `/api/jev/market/probe` 引擎精确探测（`us_ticker_ordi/sats`）**相互印证**。ORDI/1000SATS 在 Binance.US 的 `BLOCKED` 现为**本会话可复现**，不再是「继承、不可复现」。
6. **候选新增来源**：`data-api.binance.vision`（Binance Global spot）四 symbol 全部 `TRADING`，但**仅作候选来源记录**；ORDI/1000SATS 在 Global 可用**不自动改判 `BLOCKED`**，接入决策由主理人做。
7. **判定规则**：行情新鲜度阈值 `60000ms`；`clock skew >= 60s` 判 `STALE`；非 2xx / 超时判 `UNAVAILABLE`（交易所业务错误码如 `-1121` 单独归类为上市判定，不混为 UNAVAILABLE）。

---

## 1. 环境与复现

### 1.1 环境
```
HTTP_PROXY  = HTTPS_PROXY  = http://127.0.0.1:52498   # 默认代理
第二代理                        = http://127.0.0.1:10808   # team-lead 指定，GitHub API 实测 200
NO_PROXY    = (空)
python      = 3.13.14   # 仅标准库 urllib，未使用 requests
curl        = 8.4.0
```

### 1.2 复现命令
```bash
# 上下文 A1：默认代理 52498，两源 x 四 symbol x 五端点（34 请求）
python D:/jev-v15-wt/v15-market/probe_market.py

# 上下文 A2：第二代理 10808，api.binance.us 完整四 symbol x 五端点（17 请求）
python D:/jev-v15-wt/v15-market/probe_binance_us_10808.py

# 单请求复现（10808 下 api.binance.us）
curl -sS --max-time 20 -x http://127.0.0.1:10808 -D /tmp/h.txt -o /tmp/b.txt \
  "https://api.binance.us/api/v3/ticker/price?symbol=BTCUSDT"
tr -d '\r' < /tmp/h.txt | grep -iE '^(HTTP|server|content-type|date)'
cat /tmp/b.txt; echo
python -c "import hashlib;print(hashlib.sha256(open('/tmp/b.txt','rb').read()).hexdigest())"

# 单请求复现（10808 下 ORDIUSDT 的 -1121）
curl -sS --max-time 20 -x http://127.0.0.1:10808 -w "\nhttp=%{http_code}\n" \
  "https://api.binance.us/api/v3/exchangeInfo?symbol=ORDIUSDT"

# 复现 52498 下 api.binance.us 的 502
curl -sS -v --max-time 25 -o /dev/null "https://api.binance.us/api/v3/time" 2>&1 \
  | grep -iE 'CONNECT|502|HTTP/|proxy'
```

### 1.3 边缘运行时证据（上下文 B）
只读引用 `D:/cf-deploy/tmp/v15_edge_market_probe.json`（team-lead 提供，采集 2026-11-09T04:24:18~04:24:20Z，端点 `https://jev-cloud-staging.pages.dev/api/jev/market/status` 与 `/api/jev/market/probe`，Basic 预览认证，**本角色未访问、未触碰其凭据**）。摘要归档于 `evidence/EDGE_CONTEXT.json`，标注 `PROVIDED / 本会话未独立复现`。

### 1.4 证据目录
```
D:/jev-v15-wt/v15-market/evidence/
  INDEX.json                       # A1：34 请求索引（52498 上下文）
  p10808__INDEX.json               # A2：17 请求索引（10808 上下文，独立网络上下文）
  PROXY_10808_CONTROL.json         # 两代理 x 三目标 time 端点对照
  NETWORK_CONTROL.json             # A1 直连 vs 经代理对照
  EDGE_CONTEXT.json                # B：边缘运行时证据摘要（PROVIDED）
  binance_global_vision__*.json    # A1：vision 17 份成功证据
  binance_us__*.json               # A1：US 17 份传输失败证据
  binance_us_p10808__*.json        # A2：US 17 份成功/业务错误证据
```
每份证据 JSON 含：请求方法 / 完整 URL / 探测时刻(UTC + epoch ms) / UA / 代理 / `credentials_sent=false`；响应 HTTP 状态、关键响应头、`body_sha256`(64 位) 与 short16、500 字响应摘要、解析出的真实字段、分类、clock skew（time 端点）。**`network_context` 是区分三套证据的第一等字段**。

---

## 2. 网络上下文与源可用性总览（核心）

| 上下文 | 网络环境 | 源 / host | 结果 | 标注 | 证据 |
| --- | --- | --- | --- | --- | --- |
| **A1** | 本地沙箱 proxy `52498` | `api.binance.us` | 502 CONNECT / SSL EOF，17/17 失败 | `LOCAL_SANDBOX_TRANSPORT_FAILURE / UNVERIFIED`（**仅该代理路径**） | `binance_us__*.json` |
| **A1** | 本地沙箱 proxy `52498` | `data-api.binance.vision` | HTTP 200 / nginx / json，17/17 OK | `VERIFIED`（本会话实测） | `binance_global_vision__*.json` |
| **A2** | 本地沙箱 proxy `10808` | `api.binance.us` | **HTTP 200 / nginx / json**，9 OK + 8 业务错误 | `VERIFIED`（本会话实测） | `binance_us_p10808__*.json` |
| **A2** | 本地沙箱 proxy `10808` | `data-api.binance.vision` | HTTP 200 / nginx / json | `VERIFIED`（time 端点） | `PROXY_10808_CONTROL.json` |
| **B** | Cloudflare Pages 边缘运行时 / Staging | `api.binance.us` | HTTP 200 / FRESH / clock skew 38ms | `VERIFIED`（**PROVIDED，本会话未独立复现**） | `EDGE_CONTEXT.json` |
| **B** | Cloudflare Pages 边缘运行时 / Staging | `data-api.binance.vision` | HTTP 403 / text/html | `UNAVAILABLE`（**PROVIDED，本会话未独立复现**） | `EDGE_CONTEXT.json` |

**V1.4 结论（保留，不推翻）**：生产 / Staging 边缘运行时以 `api.binance.us` 为已验证主源；`data-api.binance.vision` 在该上下文返回 403。V1.4 证据来自 `paper-server.mjs` 注入 `env.__fetch` 指向 `api.binance.us` 的边缘运行时，**与本机沙箱网络不同**。

`PROXY_10808_CONTROL.json` 关键对照（time 端点）：

| 代理 | `api.binance.us` | `data-api.binance.vision` | `example.com` |
| --- | --- | --- | --- |
| `52498` | `SSL UNEXPECTED_EOF_WHILE_READING`（失败） | 200 / nginx | 200 / cloudflare |
| `10808` | **200 / nginx / json** | 200 / nginx | 200 / cloudflare |

> 该表证明：连通性差异是**代理 / 网络上下文**属性，不能泛化成「交易所不可用」或「未上市」，也不能据本地结果推翻边缘运行时的 V1.4 结论。

---

## 3. 上下文 A1：本地沙箱 proxy 52498（本会话实测）

### 3.1 `api.binance.us` 逐 symbol 五端点（任务要求 1）
四 symbol 各请求 `exchangeInfo` / `ticker/price` / `ticker/bookTicker` / `trades`，加源级 `time`，共 17 请求：

| symbol | exchangeInfo | ticker/price | bookTicker | trades | 统一结果 |
| --- | --- | --- | --- | --- | --- |
| BTCUSDT | TRANSPORT_FAILURE | TRANSPORT_FAILURE | TRANSPORT_FAILURE | TRANSPORT_FAILURE | 见下 |
| ETHUSDT | TRANSPORT_FAILURE | TRANSPORT_FAILURE | TRANSPORT_FAILURE | TRANSPORT_FAILURE | 见下 |
| ORDIUSDT | TRANSPORT_FAILURE | TRANSPORT_FAILURE | TRANSPORT_FAILURE | TRANSPORT_FAILURE | 见下 |
| 1000SATSUSDT | TRANSPORT_FAILURE | TRANSPORT_FAILURE | TRANSPORT_FAILURE | TRANSPORT_FAILURE | 见下 |
| SERVER/time | — | — | — | — | TRANSPORT_FAILURE |

统一记录：`http_status` 无（未取得 HTTP 响应）；错误 `URLError: <urlopen error Tunnel connection failed: 502 Bad Gateway>`；`latency_s ≈ 10.0`；无响应体、无 server header、**无 sha256（不伪造）**。样例：`evidence/binance_us__ORDIUSDT__exchangeInfo.json`。

### 3.2 定性（任务要求 2）
- 结论：**`LOCAL_SANDBOX_TRANSPORT_FAILURE / UNVERIFIED`**，且**仅适用于 proxy 52498 这一路径**。
- **不**写成「交易所不可用」；**不**写成「未上市」；**不**据此推翻 V1.4；**不**把 UNVERIFIED 写成 VERIFIED。
- 此路径下**无法取得** ORDI/1000SATS 的 `-1121` 业务错误码（请求未到达交易所），不伪造。该错误码已在 **A2（第 4 节）** 与 **B（第 5 节）** 两个可达上下文取得并相互印证。

### 3.3 `data-api.binance.vision` 完整性
17/17 `HTTP 200`、`Server: nginx`、`application/json;charset=UTF-8`、`x-mbx-uuid` / `x-mbx-used-weight`、`cf-ray=null`（**非 Cloudflare 拦截页**），直连与经代理双通。逐 symbol 明细见第 7 节。

---

## 4. 上下文 A2：本地沙箱 proxy 10808（本会话实测，完整四 symbol 核验）

> **全部为 `api.binance.us` / Binance.US / spot / proxy 10808 / 2026-11-09 04:36:20~04:36:27 UTC**。`server=nginx`、`content-type=application/json;charset=UTF-8`。

### 4.1 逐 symbol 五端点结果（任务要求 1）

| symbol | exchangeInfo | ticker/price | bookTicker | trades | 分类 |
| --- | --- | --- | --- | --- | --- |
| BTCUSDT | 200 `TRADING` BTC/USDT | 200 | 200 | 200 | **VERIFIED** |
| ETHUSDT | 200 `TRADING` ETH/USDT | 200 | 200 | 200 | **VERIFIED** |
| ORDIUSDT | **400 `-1121`** | **400 `-1121`** | **400 `-1121`** | **400 `-1121`** | **BLOCKED（未上市）** |
| 1000SATSUSDT | **400 `-1121`** | **400 `-1121`** | **400 `-1121`** | **400 `-1121`** | **BLOCKED（未上市）** |
| SERVER/time | — | — | — | — | 200 `serverTime=1794198983397` |

`-1121` 错误体：`{"code":-1121,"msg":"Invalid symbol."}`（`sha16=882fbec4a8b18dbb`，ORDI 与 1000SATS 四端点响应体一致）。

### 4.2 真实价格 / 盘口 / 成交明细

| symbol | status | base/quote | price (USDT) | bid / ask | 最近成交 price / qty | 成交时间 UTC (ms) | price 证据 sha16 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| BTCUSDT | TRADING | BTC / USDT | 75020.35000000 | 75036.00000000 / 75036.01000000 | 75020.35000000 / 0.00198000 | 04:35:57.487 (1794198957487) | `7807d567cc20cf87` |
| ETHUSDT | TRADING | ETH / USDT | 2143.87000000 | 2144.30000000 / 2144.40000000 | 2143.87000000 / 0.00930000 | 04:36:15.297 (1794198975297) | `5a19c6f8b855bc5c` |

源级 `/api/v3/time`：`{"serverTime":1794198983397}`（04:36:23.397Z），`body_sha256=85429aab19e2df6d6e0bdfa3f5f8f596b95d68fec71bc2dc9ff951000f5326ff`，`clock_skew_ms=+2733`（含 RTT 2.106s 的近似上界，真实漂移亚秒级）。

补充 sha16：BTC `exchangeInfo=53b02394e91180b9` / `bookTicker=a6fe5d2932581e62` / `trades=4555cd6224f369af`；ETH `exchangeInfo=d356e21ef867e03e` / `bookTicker=98d471b1bd98fb0e` / `trades=37c83e2fb023da1b`。

### 4.3 任务要求 1 / 2 的完成说明
- 「逐一验证四 symbol 五端点」已在**可达上下文 A2** 完整完成（17 请求，含 `time`）。
- 「对不支持的 symbol 记录交易所错误码并判 BLOCKED」已完成：ORDIUSDT / 1000SATSUSDT 在 `api.binance.us` 四端点均返回 **`-1121 Invalid symbol`**，判 `BLOCKED`，证据在 `binance_us_p10808__ORDIUSDT__*.json` 与 `binance_us_p10808__1000SATSUSDT__*.json`。
- 未伪造、未拿其它交易所价格冒充。

---

## 5. 上下文 B：Cloudflare 边缘运行时（PROVIDED，本会话未独立复现）

### 5.1 来源与 Provenance
| 项 | 值 |
| --- | --- |
| 来源文件 | `D:/cf-deploy/tmp/v15_edge_market_probe.json`（只读引用） |
| 采集时间 | `2026-11-09T04:24:18.594542Z` |
| 端点 | `https://jev-cloud-staging.pages.dev/api/jev/market/status` 与 `/api/jev/market/probe` |
| 网关 | `paper-server.mjs` 注入 `env.__fetch` 指向 `api.binance.us` |
| 认证 | Basic 预览认证（本角色未访问 / 未读取 / 未触碰） |
| 复现标注 | **`PROVIDED / UNVERIFIED by this session`**；证据内 verdict 由边缘探针自身产生 |
| 归档 | `evidence/EDGE_CONTEXT.json` |

> 注：`server_header=cloudflare` / `cf_ray=a57a0c18bd92b9ec-SEA` 属于**边缘网关自身响应头**；上游可达性以 `data.live.ok` / `http_status` / `content_type` 判定，不与上游 `api.binance.us` 的 `nginx` 响应头混淆。

### 5.2 `/api/jev/market/status`（http 200）

| 字段 | live / Binance.US | original / Binance Global spot |
| --- | --- | --- |
| source_host | `api.binance.us` | `data-api.binance.vision` |
| endpoint | `/api/v3/time` | `/api/v3/ticker/price?symbol=BTCUSDT` |
| http_status / ok | **200 / true** | **403 / false** |
| fetched_at_utc | `2026-11-09T04:24:20.589Z` | 同左 |
| latency_ms | 384 | 385 |
| bytes | 28 | 118 |
| content_type | `application/json;charset=UTF-8` | `text/html` |
| sha256 | `63ea381f9f35483cc9ea449c0e382130c1abb06d5868084cf6cadf676518b9ec` | `58bf2215b395dcac74c009aa98701854e43cbe54a1cd3a95fee6a647ca9910d4` |
| server_time_ms | 1794198260935 | null |
| clock_skew_ms | **38** | null |
| freshness / verdict | **FRESH / VERIFIED** | **UNAVAILABLE / UNAVAILABLE** |
| body | `{"serverTime":1794198260935}` | `<html>...403 Forbidden...` |

### 5.3 `/api/jev/market/probe` 引擎精确探测（Binance.US，逐 symbol）

| id | 请求 | http | verdict / 结果 |
| --- | --- | --- | --- |
| `us_ticker_btc` | ticker/price BTCUSDT | 200 | `REACHABLE`，`BTCUSDT:74937.11000000` |
| `us_ticker_eth` | ticker/price ETHUSDT | 200 | `REACHABLE`，`ETHUSDT:2142.08000000` |
| `us_ticker_ordi` | ticker/price ORDIUSDT | **400** | `{"code":-1121,"msg":"Invalid symbol."}` |
| `us_ticker_sats` | ticker/price 1000SATSUSDT | **400** | `{"code":-1121,"msg":"Invalid symbol."}` |
| `us_ticker_multi` | 四 symbol 批量 | **400** | `{"code":-1121,"msg":"Invalid symbol."}` |
| `us_exchange_btc` | exchangeInfo BTCUSDT | 200 | `TRADING` |
| `us_exchange_eth` | exchangeInfo ETHUSDT | 200 | `TRADING` |
| `us_depth_btc` | depth BTCUSDT | 200 | `lastUpdateId=3410350805`, bids 491 / asks 1000 |

其它源（边缘上下文，摘要）：`api.binance.com` / `api1~4.binance.com` 均 **451**（地域限制条款）；`data.binance.com`、`data-api.binance.vision`、`api.binance.vision` **403 / 530**；Coinbase、Kraken **200**；CoinGecko **429**。

### 5.4 V1.4 结论保留
> 生产 / Staging 边缘运行时以 `api.binance.us` 为**已验证主源（VERIFIED / http 200 / FRESH）**；`data-api.binance.vision` 在该上下文 **403 / UNAVAILABLE**。本报告的本地沙箱结果**不推翻**该结论；二者是不同网络上下文的独立结论。

---

## 6. 跨上下文业务结论一致性

| 业务判定 | A2 本地 10808（本会话实测） | B 边缘运行时（PROVIDED） | 一致性 |
| --- | --- | --- | --- |
| BTCUSDT 在 Binance.US | `exchangeInfo TRADING` + price/bookTicker/trades 200 | `us_ticker_btc 200` + `us_exchange_btc TRADING` | 一致 |
| ETHUSDT 在 Binance.US | `exchangeInfo TRADING` + 各端点 200 | `us_ticker_eth 200` + `us_exchange_eth TRADING` | 一致 |
| ORDIUSDT 在 Binance.US | 4/4 端点 `-1121 Invalid symbol` | `us_ticker_ordi 400 -1121` | 一致 |
| 1000SATSUSDT 在 Binance.US | 4/4 端点 `-1121 Invalid symbol` | `us_ticker_sats 400 -1121` | 一致 |

**结论**：网络可达性随上下文变化，但**交易所业务结论（上市 / 未上市）在两个独立网络上下文下完全一致**。因此 `-1121` 是交易所业务判定，不是网络 `UNAVAILABLE`；ORDI/1000SATS 的 `BLOCKED` 具备可复现证据。

---

## 7. 候选新增来源 `data-api.binance.vision` 逐项核验（上下文 A1，本会话实测）

> **全部为 `data-api.binance.vision` / Binance Global / spot / proxy 52498 / 2026-11-09 04:24:42~04:24:49 UTC**。仅作**候选来源**记录。

### 7.1 来源可区分性（逐项）

| 验证项 | 实测结论 |
| --- | --- |
| 交易所名称 | Binance Global，官方公开行情域 `data-api.binance.vision`（`x-mbx-*` 头 + `Server: nginx`） |
| 交易市场 | `spot`；四 symbol `isSpotTradingAllowed=true`、`permissions=[]` |
| 交易对 | BTCUSDT / ETHUSDT / ORDIUSDT / 1000SATSUSDT 全部 `status=TRADING` |
| 价格单位 | `USDT per 1 baseAsset unit`；`quoteAsset=USDT`。**1000SATSUSDT 的 `baseAsset="1000SATS"`，1 单位 = 1000 SATS**，与按 1 SATS 计价的 `SATSUSDT` 相差 1000 倍 |
| 真实成交 | 有，`trades[].time`(ms) + id/price/qty/isBuyerMaker |
| 盘口 | 有，`bookTicker` bidPrice/bidQty/askPrice/askQty；**响应体无内嵌时间戳** |
| 时间戳来源字段 | 权威毫秒：`/api/v3/time → serverTime`、`/api/v3/trades → trades[].time`；`ticker/price` 与 `bookTicker` **无内嵌 ts**，须用 `HTTP Date`(秒级) / `received_at` 对齐并标 `*_ts_source` |
| 可区分于 Binance.US | **是**：不同 host、不同法人主体 / 法域、不同成交簿、不同 trade id、不同价格取值 |

### 7.2 四 symbol 实测明细

| symbol | status | base/quote | price (USDT) | bid / ask | 最近成交 price / qty | 成交时间 UTC (ms) | HTTP Date | freshness | price sha16 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| BTCUSDT | TRADING | BTC / USDT | 74936.01000000 | 74936.00000000 / 74936.01000000 | 74936.01000000 / 0.02668000 | 04:24:44.544 (1794198284544) | 04:24:45 | ~0.5s FRESH | `c9fd9e01f74e85df` |
| ETHUSDT | TRADING | ETH / USDT | 2141.73000000 | 2141.72000000 / 2141.73000000 | 2141.73000000 / 0.24390000 | 04:24:45.343 (1794198285343) | 04:24:46 | ~0.7s FRESH | `1b6fa4c91c43a31b9` |
| ORDIUSDT | TRADING | ORDI / USDT | 3.09900000 | 3.09800000 / 3.10000000 | 3.09900000 / 26.66000000 | 04:24:29.352 (1794198269352) | 04:24:48 | ~18.6s FRESH | `3a92baba54a842fe` |
| 1000SATSUSDT | TRADING | 1000SATS / USDT | 0.00000841 | 0.00000840 / 0.00000842 | 0.00000841 / 217295.00 | 04:23:12.522 (1794198192522) | 04:24:49 | **~96.5s STALE** | `860be520b7172193` |

补充 sha16：`exchangeInfo` BTC `a96f4117c525d100` / ETH `65152f67bf310f11` / ORDI `4b88e64396fbff2c` / 1000SATS `c37361c85ff7ea5c`；`trades` BTC `276e551ee81ea5db` / ETH `66bbedd72ee8d932` / ORDI `4e8fd9bce85a4ece` / 1000SATS `da6437026dd621ae`；源级 `time` `serverTime=1794198283687`（04:24:43.687Z）、`sha256=2dd83440c7aaa4ecf8ab602ffc2be81800366be4f5133130c0a52d68b0fb499c`、`clock_skew_ms=+1169`（含 RTT 0.407s）。

交叉校验：`ticker/price` == `trades[0].price`（4/4），`price` ∈ `[bid, ask]`，数据自洽。

### 7.3 跨市场价格差异实证（禁止混用的直接证据）
近同时刻（相差约 23 秒，非严格同步）的两市场快照：

| 时刻 UTC | 市场 | BTCUSDT | ETHUSDT | 来源 |
| --- | --- | --- | --- | --- |
| 04:24:20.589Z | Binance.US（边缘运行时，PROVIDED） | 74937.11000000 | 2142.08000000 | `us_ticker_btc/eth` |
| 04:24:43.338Z / 04:24:44.771Z | Binance Global spot（本地 52498，本会话） | 74936.01000000 | 2141.73000000 | `ticker/price` |

差值 BTC 1.10 USDT（~0.0015%）、ETH 0.35 USDT（~0.016%）。说明：两次快照非严格同步，含时间漂移，**不能作为精确跨所基差**；但两市场返回不同价格数值、不同 trade id 与不同盘口，已足以证明它们是**独立成交簿**，因此 **禁止与 `Binance.US` 价格混用或拼接成同一条权益曲线**。

### 7.4 结论：候选来源，接入由主理人决策
- 数据层：`VERIFIED`（本会话本地实测 17/17 HTTP 200 + 真实响应体 + sha256）。
- 定位：**候选新增来源**。ORDIUSDT / 1000SATSUSDT 在 Global 可用 **不自动改判** 其在 `Binance.US` 的 `BLOCKED`；是否接入、以何种市场身份接入，**决策权归主理人**，本角色不做接入批准。
- 若主理人决定接入，须满足第 10 节全部字段与隔离约束，并先确认 Binance Global 法域合规。

---

## 8. 行情新鲜度与可用性判定规则

| 规则 | 阈值 / 触发 | 判定 |
| --- | --- | --- |
| 行情新鲜度 | `fetched_at - upstream_data_time > 60000ms` | 超出即行情陈旧（`STALE`） |
| 时钟偏差 | `|clock_skew| >= 60000ms` | `STALE` |
| 传输 / HTTP | 非 2xx、超时、CONNECT 失败 | `UNAVAILABLE` |
| 交易所业务错误 | 明确业务码（如 `-1121 Invalid symbol`） | **单独归类为上市判定 / `BLOCKED`**，**不**混为 `UNAVAILABLE` |

应用到本次数据：

| 数据 | 观测 | 判定 |
| --- | --- | --- |
| B 边缘 `live` | clock skew 38ms | FRESH / VERIFIED |
| B 边缘 `original` | 403 | UNAVAILABLE |
| A1 `api.binance.us` @52498 | 502 / SSL EOF / 超时 | UNAVAILABLE（仅该代理路径，另标 LOCAL_SANDBOX_TRANSPORT_FAILURE / UNVERIFIED） |
| A1 Global BTC / ETH / ORDI | 滞后 0.5s / 0.7s / 18.6s，均 < 60000ms | FRESH |
| A1 Global 1000SATS | 滞后约 96.5s > 60000ms | **STALE（行情陈旧）** |
| A2 US BTC / ETH | 滞后约 25.5s / 9.7s，均 < 60000ms | FRESH |
| A2 US ORDI / 1000SATS | 400 `-1121` | 上市判定 / BLOCKED（非 UNAVAILABLE） |
| clock skew | A1 +1169ms、A2 +2733ms、B +38ms，均 << 60000ms | 时钟正常 |

> freshness 以 `HTTP Date`（秒级）近似 `fetched_at`；clock skew 为含 RTT 与并发调度偏差的近似上界，非精确漂移测量。

---

## 9. 逐 symbol 最终分类

> 不同市场 / 不同网络上下文**不合并成单一标签**。证据路径相对 `D:/jev-v15-wt/v15-market/evidence/`。

| symbol | Binance.US（A2 本地 10808 实测 + B 边缘 PROVIDED 印证） | Binance.US（A1 52498） | Binance Global spot（A1 52498 实测，候选来源） |
| --- | --- | --- | --- |
| BTCUSDT | **VERIFIED** — `TRADING`、五端点 200、`binance_us_p10808__BTCUSDT__*.json`；边缘 `us_ticker_btc/us_exchange_btc` 印证 | UNVERIFIED（仅传输失败，不改判未上市） | VERIFIED（候选来源，`binance_global_vision__BTCUSDT__*.json`） |
| ETHUSDT | **VERIFIED** — 同上，`binance_us_p10808__ETHUSDT__*.json` | UNVERIFIED（同上） | VERIFIED（候选来源） |
| ORDIUSDT | **BLOCKED** — 4/4 端点 `-1121 Invalid symbol`（A2 本会话可复现 + B `us_ticker_ordi` 印证） | UNVERIFIED（仅传输失败） | VERIFIED 数据，但**仅候选来源；不自动改判 BLOCKED** |
| 1000SATSUSDT | **BLOCKED** — 4/4 端点 `-1121 Invalid symbol`（A2 本会话可复现 + B `us_ticker_sats` 印证） | UNVERIFIED（同上） | VERIFIED 数据，但**仅候选来源；不自动改判 BLOCKED**；且行情判 `STALE` |

状态枚举：
- `VERIFIED`：本会话实测 HTTP 200 + 可解析真实响应体 + 证据文件 + `body_sha256`。
- `PROVIDED / UNVERIFIED by this session`：team-lead 提供的边缘运行时证据，本角色未独立复现（第 5 节）。
- `UNVERIFIED (LOCAL_SANDBOX_TRANSPORT_FAILURE)`：仅 proxy 52498 路径传输失败；**不表示交易所不可用 / 未上市**，不推翻 V1.4。
- `BLOCKED`：交易所业务判定未上市（`-1121`），现有可复现证据。
- `CANDIDATE`：Binance Global spot 数据 VERIFIED，但接入决策归主理人。

---

## 10. 「不得无标识混用」落地约束

### 10.1 字段命名建议（每条价格 / 盘口 / 成交记录必填）

| 字段 | 类型 | 必填 | 含义 | 示例 |
| --- | --- | --- | --- | --- |
| `network_context` | string | 是 | **采集网络上下文（第一等证据字段）** | `local_sandbox_proxy_52498` / `local_sandbox_proxy_10808` / `cloudflare_pages_edge_runtime_staging` |
| `exchange` | string | 是 | 交易所主体 + 市场限定 | `"Binance Global spot"` / `"Binance.US"` |
| `market` | string | 是 | 交易市场类型 | `"spot"` / `"perp"` |
| `symbol` | string | 是 | 交易对 | `"BTCUSDT"` |
| `base_asset` | string | 是 | 基础资产（含倍数语义） | `"1000SATS"` |
| `quote_asset` | string | 是 | 计价资产 | `"USDT"` |
| `price` | string(decimal) | 是 | 价格，字符串保精度 | `"0.00000841"` |
| `price_unit` | string | 是 | 单位语义 | `"USDT per 1 base_asset unit"` |
| `price_ts` | int(ms) | 是 | 价格对应时刻 | `1794198283687` |
| `price_ts_source` | enum | 是 | `price_ts` 来源 | `exchange_serverTime` / `http_date` / `received_at` / `trade_time` |
| `book_ts` | int(ms) | 盘口记录是 | 盘口时刻 | `1794198284000` |
| `book_ts_source` | enum | 盘口记录是 | `book_ts` 来源 | `http_date` / `received_at` |
| `trade_ts` | int(ms) | 成交记录是 | 真实成交时间，来自 `trades[].time` | `1794198284544` |
| `source_endpoint` | string | 是 | 完整请求 URL | `"https://data-api.binance.vision/api/v3/ticker/price?symbol=BTCUSDT"` |
| `received_ts` | int(ms) | 建议 | 本地接收时刻 | |
| `http_status` | int | 建议 | 响应状态 | `200` |
| `server_header` | string | 建议 | 上游服务标识 | `"nginx"` |
| `body_sha256` | string | 建议 | 响应体可验证哈希 | 64 hex |
| `probe_ts_utc` | string | 建议 | 探测时刻 ISO8601 UTC | `"2026-11-09T04:24:43.338Z"` |

### 10.2 不可协商规则
1. 任何价格记录必须同时携带 `network_context` + `exchange` + `market` + `source_endpoint`，缺一不得入仓、不得参与计算 / 估值 / 风控。
2. **禁止与 `Binance.US` 价格混用或拼接成同一条权益曲线**；不同 `(exchange, market)` 不得合并进同一时间序列；禁止跨市场 forward-fill / 兜底 / 平替。
3. 不同 `network_context` 的连通性结论互相独立；**不得**把本地 502 或边缘 403 泛化为交易所本身不可用 / 未上市。
4. 倍数资产（如 `1000SATS`）必须带 `base_asset` + `price_unit`；**禁止**与按 1 SATS 计价的 `SATSUSDT` 混用（相差 1000 倍）。
5. `price_ts` / `book_ts` 必须带 `*_ts_source`；**禁止**把 `received_ts` 冒充 `trade_ts`。
6. `TRANSPORT_FAILURE` / 非 2xx / `API_ERROR` 请求不得写入价格；业务错误码（`-1121`）单独归类为上市判定，不写成网络 `UNAVAILABLE`。
7. **UNVERIFIED 不得写成 VERIFIED**；V1.4 边缘主源结论保留不推翻；ORDI/1000SATS 在 Global 可用**不自动改判 `BLOCKED`**。

### 10.3 合规记录示例（BTCUSDT，Binance Global spot，A1 上下文）
```json
{
  "network_context": "local_sandbox_proxy_52498",
  "exchange": "Binance Global spot",
  "market": "spot",
  "symbol": "BTCUSDT",
  "base_asset": "BTC",
  "quote_asset": "USDT",
  "price": "74936.01000000",
  "price_unit": "USDT per 1 base_asset unit",
  "price_ts": 1794198283687,
  "price_ts_source": "exchange_serverTime",
  "book_ts": 1794198284000,
  "book_ts_source": "http_date",
  "trade_ts": 1794198284544,
  "source_endpoint": "https://data-api.binance.vision/api/v3/ticker/price?symbol=BTCUSDT",
  "http_status": 200,
  "server_header": "nginx",
  "body_sha256": "c9fd9e01f74e85df6cee2abc473c359cbc74051fe011b94752c6bc73cfecbb56"
}
```

---

## 11. VERIFIED / UNVERIFIED 标注清单

| 条目 | 标注 | 依据 |
| --- | --- | --- |
| A1 `data-api.binance.vision` 源可用性 + 四 symbol 上市 / 价格 / 盘口 / 成交 | **VERIFIED**（本会话） | `INDEX.json` + `binance_global_vision__*.json`，17/17 200 |
| A2 `api.binance.us` 源可用性 + BTC/ETH 五端点 | **VERIFIED**（本会话） | `p10808__INDEX.json` + `binance_us_p10808__BTCUSDT/ETHUSDT__*.json` |
| A2 `api.binance.us` ORDI/1000SATS 未上市 | **VERIFIED / BLOCKED**（本会话，`-1121`） | `binance_us_p10808__ORDIUSDT/1000SATSUSDT__*.json`（各 4/4） |
| B 边缘 `api.binance.us` 主源 200 / FRESH | **PROVIDED / UNVERIFIED by this session**（证据自身 verdict=VERIFIED） | `EDGE_CONTEXT.json` |
| B 边缘 `data-api.binance.vision` 403 | **PROVIDED / UNVERIFIED by this session**（证据自身 verdict=UNAVAILABLE） | `EDGE_CONTEXT.json` |
| V1.4「边缘运行时以 api.binance.us 为已验证主源」 | **保留（不推翻）** | 第 5.4 节 |
| A1 `api.binance.us` @52498 | **UNVERIFIED / LOCAL_SANDBOX_TRANSPORT_FAILURE**（仅该代理路径） | `binance_us__*.json`，17/17 失败 |
| A1 下 `-1121` 错误码 | **UNVERIFIED（该路径未取得，不伪造）** | 已在 A2 / B 取得，见第 6 节 |
| Binance Global 对 JEV 的可接入性 | **CANDIDATE（决策归主理人）** | 第 7.4 节 |

---

## 12. RISKS

1. **【最高】网络上下文差异**：同一对来源在 A1 / A2 / B 的连通性不同（`52498` 下 US 502、`10808` 下 US 200、边缘下 US 200 而 Global 403）。**必须把 `network_context` 作为证据字段**；禁止把本地 502 或边缘 403 泛化为交易所不可用 / 未上市；禁止据本地结果推翻 V1.4。
2. **传输失败 ≠ 未上市**：A1 的 502 仅代表 proxy 52498 路径。ORDI/1000SATS 的未上市结论来自 A2 的 `-1121` 与 B 的 `us_ticker_ordi/sats`，两者一致。
3. **市场混淆（硬规则）**：Binance Global spot ≠ Binance.US。近同时刻 BTC 取值不同（74936.01 vs 74937.11），是独立成交簿；**禁止混用或拼接成同一条权益曲线**，否则污染估值 / 风控 / 回测。
4. **1000SATS 单位倍数**：`baseAsset=1000SATS`，1 单位 = 1000 SATS，与按 1 SATS 计价的标的差 1000 倍。
5. **时间戳来源不一致**：仅 `trades[].time` 与 `time.serverTime` 是交易所权威毫秒时间戳；`ticker/price`、`bookTicker` 无内嵌 ts，须显式标 `*_ts_source`。
6. **新鲜度 / 流动性**：A1 Global 1000SATS 最近成交滞后约 96.5s，按 `60000ms` 阈值判 **STALE**；ORDI 约 18.6s。`trades?limit=1` 仅最近一笔，非连续 tick，不足以支撑实时 / 高频策略。
7. **clock skew 为近似**：A1 +1169ms、A2 +2733ms 含 RTT 与并发调度偏差，非精确漂移；均远低于 60000ms STALE 阈值。
8. **边缘证据为 PROVIDED**：第 5 节结论本角色未独立复现（未访问 Basic 预览认证），不得写成「本会话 VERIFIED」。
9. **代理与合规**：出站依赖 `52498` / `10808`，策略可能变化；`data-api.binance.vision` 属 Binance Global，US 用户可用性 / 数据条款需法务确认。
10. **哈希不可复现为固定值**：`body_sha256` 对应本次真实响应体，行情变动后复测必然不同，属预期行为。
11. **范围外未覆盖**：未评估 perp / 合约（`fapi`）、未系统评估其它交易所、未做 WebSocket 实时流验证、未做历史 K 线连续性验证。

---

## 13. 决策归属（DECISION）

**INFORMATION ONLY**。本角色交付的是证据、分类与约束，**不做接入批准**：
- 四个 symbol 的逐项核验已完成（A1 / A2 / B 三上下文，51 个本会话请求 + 边缘 PROVIDED 证据）。
- `api.binance.us`：BTC/ETH `VERIFIED`；ORDI/1000SATS `BLOCKED`（`-1121`，可复现）。V1.4 边缘主源结论保留。
- `data-api.binance.vision`（Binance Global spot）：数据层 `VERIFIED`，定位 **候选来源**；ORDI/1000SATS 在 Global 可用**不自动改判 `BLOCKED`**。
- **是否接入候选来源、以何种市场身份接入、是否调整权益曲线口径，决策权归主理人**；若决定接入，须落实第 10 节全部字段与隔离约束，并先完成法域合规确认。

---

## 14. 变更与合规声明

- 仅新增 / 更新本地交付物：`probe_market.py`、`probe_binance_us_10808.py`、`MARKET_COVERAGE.md`、`evidence/*.json`。
- **未修改 V1.4 报告**；未修改仓库内任何既有代码 / 配置 / 文档；未触碰任何凭据；未创建或撤销 Token。
- 未执行任何 GitHub / Cloudflare 写操作；未执行 `git` 写操作（无 add / commit / push / branch / tag）。
- 全部网络请求为对公开只读行情端点的 `GET`；每份证据 `credentials_sent=false`；边缘证据的 Basic 预览认证未由本角色访问。
- 所有 `BLOCKED` / `UNVERIFIED` / `VERIFIED` 判定均基于本会话可复现实测，或明确标注的 `PROVIDED / 本会话未独立复现` 证据；无伪造、无跨市场价格冒充、未把 UNVERIFIED 写成 VERIFIED。
