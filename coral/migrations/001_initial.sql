-- Coral vault baseline schema (engineering spec §4.1) + internal tables.

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY NOT NULL,
    applied_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS vault_metadata (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    salt BLOB NOT NULL,
    argon2_memory_cost INTEGER NOT NULL,
    argon2_time_cost INTEGER NOT NULL,
    argon2_parallelism INTEGER NOT NULL,
    argon2_hash_len INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    origin TEXT NOT NULL,
    label TEXT,
    created_at INTEGER NOT NULL,
    last_used_at INTEGER,
    expires_at INTEGER,
    status TEXT NOT NULL,
    state_blob BLOB NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_sessions_origin ON sessions(origin);
CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);

CREATE TABLE IF NOT EXISTS policies (
    origin TEXT PRIMARY KEY,
    yaml_body TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL,
    session_id TEXT,
    agent_id TEXT,
    event_type TEXT NOT NULL,
    origin TEXT,
    detail TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_session ON audit_log(session_id);

CREATE TABLE IF NOT EXISTS api_tokens (
    token_hash TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    last_used_at INTEGER,
    expires_at INTEGER NOT NULL
);
