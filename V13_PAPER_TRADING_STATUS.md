# JEV Trading Desk — V1.3 Paper Trading Status (T05)

> **生成 (UTC)**：2026-11-08 · **任务**：T05 量化研究持续运行闭环（Day 0 基线）
> **结论**：🟡 脚手架就绪，首份真实快照已产出；30 天按真实日历累计

## TL;DR

T05 闭环：真实行情 → AI 研究 → 策略验证 → Paper 订单 → 服务端撮合 → 权益与风险 → 审计回放。当前为 **Day 0**：市场数据质量 VERIFIED、服务端 Paper 引擎 VERIFIED、AI 研究与策略 OOS 因 Provider 未配置而 BLOCKED/NO_DATA。日报告脚本已就绪并产出首份真实快照。

## 1. 每日报告机制

- 脚本：`scripts/v13_paper_trading_daily.mjs`（对 live 适配层 `:4791` 拉取真实数据，无虚构）。
- 产出：`e2e/artifacts/v13/paper_trading_status.json`。
- 调度：待接入每日调度（真实日历，不回放凑天数）。

## 2. Day 0 真实快照（2026-11-08）

### 2.1 数据源审计（全局，VERIFIED）

| id | label | channel | status | note |
|---|---|---|---|---|
| market_rest | LIVE | REST_POLLING | VERIFIED | data-api.binance.vision，真实公开 REST；未用 WebSocket |
| market_websocket | BLOCKED | WEBSOCKET | BLOCKED | `/ws`→404；`stream.binance.com:9443`→timeout，诚实标注 |
| market_backfill | BACKFILL | LOCAL_LEDGER | VERIFIED | V4.5 hash-chained bars ledger，88 条，**不计入实时增量** |
| paper_engine | LIVE | SERVER_ENGINE | VERIFIED | 冻结 paper-server.mjs（原地 import） |
| provider_inference | BLOCKED | PROVIDER_CENTER | BLOCKED | 既有 DPAPI SecretStore，count=0 |
| strategy_oos | NO_DATA | BACKTEST_ENGINE | NO_DATA | count=0 |
| supervisor | VERIFIED | LOCAL_EVIDENCE | VERIFIED | runtime/v46_supervisor_state.json |

### 2.2 行情质量

- `verdict`: **VERIFIED** · `problems`: **0**
- 分类规则：REALTIME = `fetched_at_utc <= bar close_time + 2h`；BACKFILL 永不计入实时/30 天天数。

### 2.3 Paper 账本（新建会话基线）

| 字段 | 值 |
|---|---|
| 初始现金 | 10000（纸面 USDT） |
| 持仓 | 0 |
| 挂单 / 成交 / 总单 | 0 / 0 / 0 |
| 事件 | 1（会话初始化） |
| 权益 | null（无成交） |

### 2.4 研究与策略

| 项 | 值 |
|---|---|
| AI 研究数 | 0（provider_inference BLOCKED） |
| 策略收益 | NO_DATA |
| OOS 样本 | NO_DATA |
| 风险拒绝 | 0 |
| 自动暂停 | 未触发 |

### 2.5 30 天日历

- `start_utc`: 2026-11-08 · `day_index`: **0** · `target_days`: 30 · `mode`: **real_calendar_only**

## 3. 红线

- 不接真实资金 ✅
- 不开放实盘交易 ✅
- 不绕过 OOS 晋级门禁 ✅（OOS=NO_DATA，无自动晋级）
- 不虚构研究/收益/OOS ✅

## 4. 下一步

1. Provider 凭据就绪后，AI 研究数 → 真实推理记录（见 V13_PROVIDER_E2E.md）。
2. 设定每日调度，逐日累计真实绩效与风险拒绝。
3. 30 天仅按实际日历累计，不使用历史回放凑天数。
