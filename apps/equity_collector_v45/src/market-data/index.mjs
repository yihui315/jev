/**
 * P1 正式数据层 — 聚合导出（纯 ESM）。
 *
 * 仅重导出 market-data/ 下模块；不引入任何网络 / 付费模型 / 外部依赖，
 * 模块加载零副作用（不发请求、不读时钟、不写文件）。
 *
 * 未来接线目标登记（team-lead 追加裁定）：
 *   AI_MARKET_CONTEXTS_BINDING = "DECLARED_NOT_WIRED"（常量实定义见 provenance-guard.mjs，
 *   经本文件 export * 透出）。本轮不写 ai_market_contexts D1 表、不建 0007 迁移、
 *   不碰数据库；market_context_id 指向 P1 本地文件 snapshot store，store_kind:"file" /
 *   db_persisted:false，绝不假装已落库。
 * 权威哈希命名契约见 provenance-guard.mjs 的 MARKET_SNAPSHOT_HASH_CONTRACT。
 */
export * from "./append-only-log.mjs";
export * from "./provenance-guard.mjs";
export * from "./snapshot-store.mjs";
export * from "./bar-store.mjs";
export * from "./orderbook-journal.mjs";
export * from "./quality-monitor.mjs";
export * from "./replay-dataset.mjs";
