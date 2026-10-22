-- Web Push (detection notifications) — one row per browser that opted in.
-- endpoint is the push service URL the browser handed out; p256dh/auth are
-- its base64url-encoded payload encryption keys (RFC 8291).
CREATE TABLE web_push_subscriptions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    endpoint    TEXT NOT NULL UNIQUE,
    p256dh      TEXT NOT NULL,
    auth        TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX idx_web_push_subscriptions_user ON web_push_subscriptions(user_id);

-- The device's VAPID signing key (RFC 8292), generated on first start. A
-- single row: rotating it would invalidate every existing subscription.
CREATE TABLE vapid_keys (
    id               INTEGER PRIMARY KEY CHECK (id = 1),
    private_key_pem  TEXT NOT NULL,
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
