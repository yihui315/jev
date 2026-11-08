-- 0003_fixed_point.sql
-- 将货币/数量相关 REAL 列改为 INTEGER（定点 1e8 缩放）。
-- SQLite 不支持 ALTER COLUMN，故以"建新表→搬数据(缩放)→换名"方式实现。
-- 注意：在已有生产 D1 上执行会按 1e8 缩放现有浮点值；全新部署也可直接以此为准。
-- 金额字段统一约定：INTEGER 值 = 实际值 * 1e8。

-- paper_orders: quantity/filled_quantity/remaining_quantity/limit_price/average_fill_price/fee/
--              realized_pnl/reserved_cash/reserved_quantity 改为 INTEGER
CREATE TABLE IF NOT EXISTS paper_orders_fp (
  order_id TEXT PRIMARY KEY NOT NULL,
  session_id TEXT NOT NULL,
  client_order_id TEXT NOT NULL,
  request_hash TEXT NOT NULL,
  confirmation_id TEXT NOT NULL,
  symbol TEXT NOT NULL,
  side TEXT NOT NULL CHECK(side IN ('BUY', 'SELL')),
  order_type TEXT NOT NULL CHECK(order_type IN ('MARKET', 'LIMIT')),
  quantity INTEGER NOT NULL,
  filled_quantity INTEGER NOT NULL DEFAULT 0,
  remaining_quantity INTEGER NOT NULL DEFAULT 0,
  limit_price INTEGER,
  status TEXT NOT NULL,
  average_fill_price INTEGER,
  fee INTEGER NOT NULL DEFAULT 0,
  realized_pnl INTEGER,
  reserved_cash INTEGER NOT NULL DEFAULT 0,
  reserved_quantity INTEGER NOT NULL DEFAULT 0,
  rejection_code TEXT,
  rejection_reason TEXT,
  book_sequence INTEGER,
  book_received_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  terminal_at TEXT,
  UNIQUE(session_id, client_order_id),
  FOREIGN KEY(session_id) REFERENCES paper_sessions(session_id)
);

INSERT INTO paper_orders_fp (
  order_id, session_id, client_order_id, request_hash, confirmation_id, symbol, side, order_type,
  quantity, filled_quantity, remaining_quantity, limit_price, status, average_fill_price, fee,
  realized_pnl, reserved_cash, reserved_quantity, rejection_code, rejection_reason,
  book_sequence, book_received_at, created_at, updated_at, terminal_at
)
SELECT
  order_id, session_id, client_order_id, request_hash, confirmation_id, symbol, side, order_type,
  CAST(ROUND(quantity * 100000000) AS INTEGER),
  CAST(ROUND(filled_quantity * 100000000) AS INTEGER),
  CAST(ROUND(remaining_quantity * 100000000) AS INTEGER),
  CASE WHEN limit_price IS NULL THEN NULL ELSE CAST(ROUND(limit_price * 100000000) AS INTEGER) END,
  status,
  CASE WHEN average_fill_price IS NULL THEN NULL ELSE CAST(ROUND(average_fill_price * 100000000) AS INTEGER) END,
  CAST(ROUND(fee * 100000000) AS INTEGER),
  CASE WHEN realized_pnl IS NULL THEN NULL ELSE CAST(ROUND(realized_pnl * 100000000) AS INTEGER) END,
  CAST(ROUND(reserved_cash * 100000000) AS INTEGER),
  CAST(ROUND(reserved_quantity * 100000000) AS INTEGER),
  rejection_code, rejection_reason, book_sequence, book_received_at, created_at, updated_at, terminal_at
FROM paper_orders;

DROP TABLE paper_orders;
ALTER TABLE paper_orders_fp RENAME TO paper_orders;

CREATE INDEX IF NOT EXISTS idx_paper_orders_session_status
  ON paper_orders(session_id, status, created_at);

-- paper_fills: quantity/price/notional/fee/realized_pnl 改为 INTEGER
CREATE TABLE IF NOT EXISTS paper_fills_fp (
  fill_id TEXT PRIMARY KEY NOT NULL,
  order_id TEXT NOT NULL,
  session_id TEXT NOT NULL,
  fill_index INTEGER NOT NULL,
  symbol TEXT NOT NULL,
  side TEXT NOT NULL,
  quantity INTEGER NOT NULL,
  price INTEGER NOT NULL,
  notional INTEGER NOT NULL,
  fee INTEGER NOT NULL,
  realized_pnl INTEGER,
  liquidity_model TEXT NOT NULL,
  book_sequence INTEGER,
  book_received_at TEXT,
  created_at TEXT NOT NULL,
  UNIQUE(order_id, fill_index),
  FOREIGN KEY(order_id) REFERENCES paper_orders(order_id),
  FOREIGN KEY(session_id) REFERENCES paper_sessions(session_id)
);

INSERT INTO paper_fills_fp (
  fill_id, order_id, session_id, fill_index, symbol, side, quantity, price, notional, fee,
  realized_pnl, liquidity_model, book_sequence, book_received_at, created_at
)
SELECT
  fill_id, order_id, session_id, fill_index, symbol, side,
  CAST(ROUND(quantity * 100000000) AS INTEGER),
  CAST(ROUND(price * 100000000) AS INTEGER),
  CAST(ROUND(notional * 100000000) AS INTEGER),
  CAST(ROUND(fee * 100000000) AS INTEGER),
  CASE WHEN realized_pnl IS NULL THEN NULL ELSE CAST(ROUND(realized_pnl * 100000000) AS INTEGER) END,
  liquidity_model, book_sequence, book_received_at, created_at
FROM paper_fills;

DROP TABLE paper_fills;
ALTER TABLE paper_fills_fp RENAME TO paper_fills;

CREATE INDEX IF NOT EXISTS idx_paper_fills_session_time
  ON paper_fills(session_id, created_at);
