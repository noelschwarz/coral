-- Track N / PR N3 — flag sessions that need user attention.
--
-- When the daemon detects a 401 (or, in the future, a login-redirect) during
-- a navigation in an agent session, it flags the session via these columns.
-- The extension popup reads them via GET /sessions and surfaces a "needs
-- refresh" hint. Cleared on refresh (PR N2) or revoke. See ADR-018 §4 for the
-- write-back / staleness story this complements.

ALTER TABLE sessions ADD COLUMN attention_at INTEGER;
ALTER TABLE sessions ADD COLUMN attention_reason TEXT;
