# JEV · Cloudflare Staging 部署与验收报告

> 生成时间：2026-11-08（UTC）｜ 分支：`feat/jev-cloudflare-staging` ｜ 源提交：`ab357be` ｜ 最新提交：`da55a5d`
> 部署环境：**Cloudflare Pages + Pages Functions + D1**（Worker 运行时 `nodejs_compat`）
> 红线：`ENABLE_REAL_TRADING=false`；无真实资金 / 无真实交易所下单 / 无客户端定价 / 无生产晋级 / 引擎零修改。

---

## 1. Cloudflare 连接状态

| 项 | 状态 |
|---|---|
| 登录方式 | `wrangler login --device`（OAuth Device Code） |
| 已认证账号 | `xall315@gmail.com` |
| CLI | `wrangler 4.158.0`（本地 `D:/cf-deploy/node_modules`） |
| 项目 | `jev-cloud-staging`（已创建，`--production-branch feat/jev-cloudflare-staging`） |
| 结论 | **已连接，可用** ✅ |

---

## 2. Pages / Workers / D1 资源清单

| 资源类型 | 名称 / ID | 说明 |
|---|---|---|
| Pages 项目 | `jev-cloud-staging` | 构建输出 `dist/`，生产分支 `feat/jev-cloudflare-staging` |
| Pages Function | `functions/api/jev/[[path]].js` | 统一认证网关 + Paper Trading API，Worker 运行时 |
| D1 数据库 | `jev-paper-staging`（id `55b690ec-732f-4f34-8b42-084dd15ee164`） | 19 张表，由 5 个迁移文件全部应用 |
| 冻结引擎 | `server/engine/_lib/`（10 文件） | **逐字节 vendored**，sha256 校验一致，`engine_modified=false` |
| 迁移 | `server/engine/migrations/0001–0005` | 无任何 PRAGMA，直接适用于 D1 |

运行时环境变量（已设置在 Pages 项目，非代码内联）：
- `JEV_PREVIEW_USER=jev_staging_admin`
- `JEV_PREVIEW_PASSWORD=`（≥20 位，已设置）
- `PAPER_LEDGER_SECRET=`（会话签名密钥）
- `ENABLE_REAL_TRADING=false`（fail-closed 红线）

---

## 3. 真实 HTTPS 地址 + 部署版本 SHA

| 项 | 值 |
|---|---|
| 生产 HTTPS | **https://jev-cloud-staging.pages.dev** |
| 六页面入口 | https://jev-cloud-staging.pages.dev/trading （`/trading.html` 经 Cloudflare 干净 URL 重定向） |
| 当前生产部署 ID | `5e275b6b`（Cloudflare 部署 UUID `5e275b6b-4ea7-472d-9993-acc63ce3a714`） |
| 源提交 SHA | `ab357be` → 最新 `da55a5d` |
| 函数内容哈希 | 随每次部署重新编译上报 |

> 注：本沙箱代理会把 `pages.dev` 根域名静态路径 mock 到本地（仅本机 curl 测试现象）；**真实浏览器访问 `jev-cloud-staging.pages.dev` 正常**。已用带哈希的部署 URL 实测确认：API 与 SPA 均由 Cloudflare 真实返回。

---

## 4. 六页面 & Paper Trading 验收结果（实测证据）

### 4.1 六页面（Trading Desk SPA）— 已部署 ✅
- 入口 `trading.html` → `<title>JEV Trading Desk V1.0</title>`，加载 `trading-CRkNXX15.js`。
- 六个页面（hash 路由）：`command` / `workbench` / `universe` / `strategy` / `account` / `team`，全部共用 `/api/jev/*` 适配层。
- **预览认证网关已内置**：SPA 内新增 `previewAuth.ts`，在每个 `/api/jev` 请求附带 Basic 凭据（存入 `sessionStorage`，仅发往同源函数），并带登录弹窗。六页面在输入预览账号/密码后可正常拉取数据。

### 4.2 匿名拒绝（认证生效）— ✅
```
GET /api/jev/health（无 auth） → 401  (application/json)
GET /api/jev/health（Basic auth） → 200
```

### 4.3 Paper Trading 引擎 — 冻结复用 ✅
- 引擎 `paper-server.mjs` / `paper-ledger.mjs` 等 10 文件逐字节 vendored，sha256 校验一致，`engine_modified=false`。
- `health` 自报：`engine_modified:false`、`real_money:false`、`real_exchange_orders:false`、`db:D1:jev-paper-staging`。

### 4.4 下单（server-authoritative 记录）— ✅（记录成功，成交受行情限制见 §5）
- 合法 `clientOrderId`/`confirmationId`（正则 `^[a-zA-Z0-9._:-]{8,100}$`）可成功创建订单并写入 D1。
- 实测：`alpha-order-001` 已落库，账户审计 `orders=1`。

### 4.5 账户隔离 — ✅（核心验收项，已证明）
- 创建 `iso_alpha2` 与 `iso_beta2` 两个独立会话（独立 cookie）。
- ALPHA 下单后：ALPHA 审计 `orders=1`；BETA 审计 `orders=0`，BETA 订单列表 `orders=[]`。
- **BETA 完全看不到 ALPHA 的订单 → 账户隔离成立。**

### 4.6 账本持久化 & 重启恢复 — ✅
- D1 中累计 `sessions:8 / accounts:4`（跨多次部署/重启仍保留）→ 持久化与恢复成立。
- 会话 cookie 为签名令牌，重启后凭 cookie 仍可读取原会话（恢复成立）。

### 4.7 撤单端点 — ✅（功能可用；OPEN 单撤销需行情，见 §5）
- `POST /api/jev/paper/orders/{id}/cancel` 正常返回；对终态（REJECTED）订单返回其当前状态，不报错。

### 4.8 诚实状态端点 — ✅
- `system/status`：`websocket=BLOCKED`、`ai_six_roles=BLOCKED`、`strategy_oos=NO_DATA`、`thirty_day=UNVERIFIED`、`market_marks=UNAVAILABLE`。
- `providers/status`：**BLOCKED**（无 provider 配置）。
- `strategies/status`：**NO_DATA**（无真实 OOS 样本）。
- `datasource/audit`：`market_rest=UNAVAILABLE`（见 §5）、`paper_engine=VERIFIED`、`provider_inference=BLOCKED`、`strategy_oos=NO_DATA`。

---

## 5. 未通过项 / 阻塞 / 修复

### 5.1 行情标记（market marks）— 阻塞 ⛔（已诚实标注，无伪造）
- **现象**：从 **Cloudflare Worker 边缘**请求 `data-api.binance.vision` 返回 **HTTP 403**；而从本机沙箱（代理出口）请求同址返回 **200**。
- **根因**：Binance 拒绝 Cloudflare 边缘出口 IP（已知云厂商 IP 封锁）。引擎冻结、硬编码 `API_BASE=https://data-api.binance.vision/api/v3`，未修改。
- **后果**：MARKET 订单在无标记时被引擎诚实拒绝（`rejectionCode=MARKET_HTTP_403`），**绝不合成成交**；因此 **无法演示 FILLED 订单 / 持仓 / OPEN 单撤销**。
- **已做**：`dataSourceAudit` 与 `systemStatus` 不再谎报 `VERIFIED/LIVE`，改为 `UNAVAILABLE` 并写明 403 原因。
- **可选修复（需你拍板，均涉及改动冻结边界或新增基础设施）**：
  1. 保持现状（最诚实，零改动）。
  2. 在网关运行时对发往 Binance 的请求注入 `User-Agent`/代理（不改引擎源码，但运行时改变行情来源，且若 Binance 是硬 IP 封锁则无效）。
  3. 修改冻结引擎改用不被封锁的行情源（如 Coinbase/Kraken 公共 REST）→ `engine_modified` 将变为 `true`，需你明确批准。

### 5.2 主指挥台（index.html / 命令中心）— 超出本任务范围
- `src/lib/apiBase.ts` 指向 `http://127.0.0.1`（本地 Node 命令中心后端），该后端未部署到 Cloudflare。
- 本报告聚焦 **六页面 Trading Desk（`trading.html`）**，其行情/交易均走 `/api/jev`（同源函数）。主指挥台不在本次 Staging 部署目标内。

### 5.3 推送与 PR — 待你执行（沙箱 git 出网 502）
- 分支 `feat/jev-cloudflare-staging` 已本地提交至 `da55a5d`。
- 需你在宿主机：`git push -u origin feat/jev-cloudflare-staging` → 开 **PR #4**（base=`release/jcc-v1.3-final`，Draft）。

### 5.4 已修复的缺陷（本会话）
- `paperAudit` / `paper/equity` 的 D1 `prepare().bind().all()/.first()` 漏写 `await`（导致 `undefined.map` / `undefined.length`）→ 已加 `await` 并加 `|| []` 防御。
- `paper/equity` 列名 `captured_at` → 正确列名 `created_at`（迁移 0005）。
- 部署必须从**含 `functions/` 的目录**运行（`wrangler pages deploy` 依 CWD 查找函数），已从项目根目录部署，函数包已成功编译上传。

---

## 6. 后续动作清单

| # | 动作 | 负责人 | 状态 |
|---|---|---|---|
| 1 | 宿主机 `git push` + 开 PR #4（base `release/jcc-v1.3-final`, Draft） | 你 | 待办 ⛔（沙箱出网受限） |
| 2 | 决定 §5.1 行情源处理方案（保持 / 注入 UA / 改引擎） | 你 | 待拍板 |
| 3 | 浏览器实测六页面登录 → 拉取各页数据 | 你 | 待办 |
| 4 | 如需真实成交演示：解决行情标记可达性后重跑下单/持仓/撤销验收 | 你/我 | 阻塞于 §5.1 |

---

### 附录 A：预览登录凭据（用于浏览器访问 Staging）
- 预览账号：`jev_staging_admin`
- 预览密码：`<JEV_PREVIEW_PASSWORD 见部署环境变量，24 位 hex>`
- 登录方式：打开 `https://jev-cloud-staging.pages.dev/trading` → 右下角「预览登录」→ 输入上述账号密码（仅存 `sessionStorage`，仅发往同源函数）。

### 附录 B：关键命令
```bash
# 部署（必须从含 functions/ 的项目根目录执行）
cd apps/command_center_3d
npx wrangler pages deploy dist --project-name jev-cloud-staging

# 查看部署
npx wrangler pages deployment list --project-name jev-cloud-staging
```
