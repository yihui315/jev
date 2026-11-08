CREATE TABLE IF NOT EXISTS paper_sessions (
  session_id TEXT PRIMARY KEY NOT NULL,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  revoked_at TEXT
);

CREATE TABLE IF NOT EXISTS paper_events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(session_id, event_id),
  FOREIGN KEY(session_id) REFERENCES paper_sessions(session_id)
);

CREATE INDEX IF NOT EXISTS idx_paper_events_session_seq
  ON paper_events(session_id, seq);

CREATE INDEX IF NOT EXISTS idx_paper_events_type_time
  ON paper_events(event_type, created_at);
