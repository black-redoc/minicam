-- Mobile push (ADR 0011): one row per phone that opted in. token is the
-- phone's FCM registration token, key its base64url AES-256 key: payloads are
-- encrypted for the phone before they leave the device. session_jti is the
-- session that registered it, so logging that session out drops it.
CREATE TABLE mobile_push_tokens (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    session_jti  TEXT,
    platform     TEXT NOT NULL CHECK (platform IN ('android')),
    token        TEXT NOT NULL UNIQUE,
    key          TEXT NOT NULL,
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX idx_mobile_push_tokens_user ON mobile_push_tokens(user_id);
CREATE INDEX idx_mobile_push_tokens_session ON mobile_push_tokens(session_jti);
