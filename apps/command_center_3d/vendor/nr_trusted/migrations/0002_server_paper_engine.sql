-- Server-authoritative paper execution tables.
-- The existing paper_events table remains a legacy browser snapshot / research audit journal.
-- Only trusted Pages Functions write to paper_server_events.

CREATE TABLE IF NOT EXISTS paper_accounts (
  session_id TEXT PRIMARY KEY NOT NULL,
  version INTEGER NOT NULL,
  state_json TEXT NOT NULL,
  state_hash TEXT NOT NULL,
  last_command_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(session_id) REFERENCES paper_sessions(session_id)
);

CREATE TABLE IF NOT EXISTS paper_orders (
  order_id TEXT PRIMARY KEY NOT NULL,
  session_id TEXT NOT NULL,
  client_order_id TEXT NOT NULL,
  request_hash TEXT NOT NULL,
  confirmation_id TEXT NOT NULL,
  symbol TEXT NOT NULL,
  side TEXT NOT NULL CHECK(side IN ('BUY', 'SELL')),
  order_type TEXT NOT NULL CHECK(order_type IN ('MARKET', 'LIMIT')),
  quantity REAL NOT NULL,
  filled_quantity REAL NOT NULL DEFAULT 0,
  remaining_quantity REAL NOT NULL DEFAULT 0,
  limit_price REAL,
  status TEXT NOT NULL,
  average_fill_price REAL,
  fee REAL NOT NULL DEFAULT 0,
  realized_pnl REAL,
  reserved_cash REAL NOT NULL DEFAULT 0,
  reserved_quantity REAL NOT NULL DEFAULT 0,
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

CREATE INDEX IF NOT EXISTS idx_paper_orders_session_status
  ON paper_orders(session_id, status, created_at);

CREATE TABLE IF NOT EXISTS paper_fills (
  fill_id TEXT PRIMARY KEY NOT NULL,
  order_id TEXT NOT NULL,
  session_id TEXT NOT NULL,
  fill_index INTEGER NOT NULL,
  symbol TEXT NOT NULL,
  side TEXT NOT NULL,
  quantity REAL NOT NULL,
  price REAL NOT NULL,
  notional REAL NOT NULL,
  fee REAL NOT NULL,
  realized_pnl REAL,
  liquidity_model TEXT NOT NULL,
  book_sequence INTEGER,
  book_received_at TEXT,
  created_at TEXT NOT NULL,
  UNIQUE(order_id, fill_index),
  FOREIGN KEY(order_id) REFERENCES paper_orders(order_id),
  FOREIGN KEY(session_id) REFERENCES paper_sessions(session_id)
);

CREATE INDEX IF NOT EXISTS idx_paper_fills_session_time
  ON paper_fills(session_id, created_at);

CREATE TABLE IF NOT EXISTS paper_server_events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  command_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  schema_version INTEGER NOT NULL DEFAULT 1,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(session_id, event_id),
  FOREIGN KEY(session_id) REFERENCES paper_sessions(session_id)
);

CREATE INDEX IF NOT EXISTS idx_paper_server_events_session_seq
  ON paper_server_events(session_id, seq);

CREATE INDEX IF NOT EXISTS idx_paper_server_events_session_command
  ON paper_server_events(session_id, command_id);
