-- Track E (week 3) — pending operator reviews for policy-flagged actions.

CREATE TABLE IF NOT EXISTS pending_reviews (
    id TEXT PRIMARY KEY,
    session_handle TEXT NOT NULL,
    session_id TEXT NOT NULL,
    agent_id TEXT,
    action_type TEXT NOT NULL,
    action_detail TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending',  -- pending | approved | denied | expired
    created_at INTEGER NOT NULL,
    decided_at INTEGER,
    decided_by TEXT
);

CREATE INDEX IF NOT EXISTS idx_pending_reviews_status ON pending_reviews(status);
CREATE INDEX IF NOT EXISTS idx_pending_reviews_session ON pending_reviews(session_id);
