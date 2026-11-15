-- JEV V1.6 Track B - AI decision persistence (NEW, outside the frozen tree).
--
-- LOCATION RULE: this file lives in server/ai/migrations. The frozen engine
-- migrations in server/engine/migrations are sha256-anchored as a whole
-- directory; adding ANY file there (including a migration) breaks the anchor.
--
-- WHAT THIS ADDS
--   ai_decisions          one row per AI decision, including the risk verdict
--   decision_order_links  side-car attribution AI decision -> paper order
--   ai_market_contexts    the verified mark snapshot a decision refers to
--
-- HARD RULES
--  * No credential column exists here, by construction. There is no
--    authorization header, no api_key, no secret column, and none may be added.
--    Only secretRef NAMES live in provider configuration, never in D1 rows.
--  * data_mode is server-assigned ('mock' | 'real'). A model cannot claim it.
--  * risk_check_result records the SERVER verdict. An AI self-report (e.g.
--    confidence) is audit data only and is never the basis of a PASS.
--  * Monetary / ratio values use the same 1e8 fixed-point INTEGER convention
--    as 0003_fixed_point.sql / 0004_strategy_registry.sql.
--
-- All statements are idempotent so `wrangler d1 migrations apply` is safe.

CREATE TABLE IF NOT EXISTS ai_market_contexts (
  market_context_id TEXT PRIMARY KEY NOT NULL,
  symbol TEXT NOT NULL,
  mark_price INTEGER NOT NULL,            -- 1e8 fixed point, USD
  price_ts TEXT NOT NULL,                 -- ISO-8601 UTC, upstream quote time
  mark_quality TEXT NOT NULL CHECK (mark_quality IN ('verified','unknown')),
  source TEXT NOT NULL,                   -- e.g. 'api.binance.us/api/v3/ticker/price'
  source_sha256 TEXT NOT NULL,            -- sha256 of the raw upstream payload
  expires_at TEXT,                        -- ISO-8601 UTC, optional hard expiry
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ai_market_contexts_symbol
  ON ai_market_contexts(symbol, price_ts);

CREATE TABLE IF NOT EXISTS ai_decisions (
  decision_id TEXT PRIMARY KEY NOT NULL,
  strategy_id TEXT NOT NULL,
  provider_id TEXT NOT NULL,
  model_id TEXT NOT NULL,
  run_id TEXT,
  symbol TEXT NOT NULL,
  action TEXT NOT NULL CHECK (action IN ('HOLD','BUY','SELL')),
  side TEXT CHECK (side IS NULL OR side IN ('BUY','SELL')),
  order_type TEXT CHECK (order_type IS NULL OR order_type IN ('MARKET','LIMIT')),
  quantity INTEGER CHECK (quantity IS NULL OR quantity > 0),
  limit_price INTEGER CHECK (limit_price IS NULL OR limit_price > 0),
  confidence INTEGER NOT NULL CHECK (confidence BETWEEN 0 AND 100000000),
  reason TEXT NOT NULL,
  risk_flags_json TEXT NOT NULL DEFAULT '[]',
  market_context_id TEXT NOT NULL,
  input_snapshot_sha256 TEXT NOT NULL,
  prompt_version TEXT NOT NULL,
  request_payload_sha256 TEXT,
  response_payload_sha256 TEXT,           -- sha256 of the REDACTED response body
  price_ts TEXT,
  data_mode TEXT NOT NULL CHECK (data_mode IN ('mock','real')),
  risk_check_result TEXT NOT NULL DEFAULT 'NOT_RUN'
    CHECK (risk_check_result IN ('PASS','REJECT','NOT_RUN')),
  paper_order_id TEXT,
  paper_fill_ids_json TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'PENDING'
    CHECK (status IN ('PENDING','REJECTED','APPROVED','SUBMITTED','FAILED')),
  error_code TEXT,
  created_at TEXT NOT NULL,
  -- Shape invariants mirror decision-schema.mjs. Kept in SQL as a second line
  -- of defence: a buggy writer still cannot store an inconsistent row.
  CHECK (
    (action = 'HOLD' AND side IS NULL AND order_type IS NULL AND quantity IS NULL AND limit_price IS NULL)
    OR
    (action IN ('BUY','SELL') AND side = action AND order_type IN ('MARKET','LIMIT') AND quantity > 0
      AND ((order_type = 'LIMIT' AND limit_price > 0) OR (order_type = 'MARKET' AND limit_price IS NULL)))
  )
);

CREATE INDEX IF NOT EXISTS idx_ai_decisions_strategy
  ON ai_decisions(strategy_id, created_at);
CREATE INDEX IF NOT EXISTS idx_ai_decisions_run
  ON ai_decisions(run_id, created_at);
CREATE INDEX IF NOT EXISTS idx_ai_decisions_status
  ON ai_decisions(status, created_at);
CREATE INDEX IF NOT EXISTS idx_ai_decisions_context
  ON ai_decisions(market_context_id);

-- Side-car attribution. The frozen paper_orders table is NOT altered: an AI
-- decision is linked to an order it caused through this table only.
CREATE TABLE IF NOT EXISTS decision_order_links (
  decision_id TEXT NOT NULL,
  order_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE (decision_id, order_id),
  FOREIGN KEY(decision_id) REFERENCES ai_decisions(decision_id)
);

CREATE INDEX IF NOT EXISTS idx_decision_order_links_order
  ON decision_order_links(order_id);
