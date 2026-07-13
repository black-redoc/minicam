PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    is_admin      INTEGER NOT NULL DEFAULT 0 CHECK (is_admin IN (0, 1)),
    is_active     INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- Session JWT (login). Revocable: logout / account kill-switch.
CREATE TABLE IF NOT EXISTS sessions (
    jti         TEXT PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    issued_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    expires_at  TEXT NOT NULL,
    revoked_at  TEXT,
    user_agent  TEXT,
    ip_address  TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);

-- Per-camera authorization (deny-by-default: no row means no access).
CREATE TABLE IF NOT EXISTS camera_permissions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    camera_name  TEXT NOT NULL,  -- logical reference to CameraConfig.name in config.toml, no real FK
    can_view     INTEGER NOT NULL DEFAULT 0 CHECK (can_view IN (0, 1)),
    can_use_ptz  INTEGER NOT NULL DEFAULT 0 CHECK (can_use_ptz IN (0, 1)),
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (user_id, camera_name)
);
CREATE INDEX IF NOT EXISTS idx_camera_permissions_user   ON camera_permissions(user_id);
CREATE INDEX IF NOT EXISTS idx_camera_permissions_camera ON camera_permissions(camera_name);

-- Compact remote-access (WAN) flag: streaming via mobile app + push notifications.
-- NOT a billing table — no price, plan, or payment history here.
-- That detail lives only in D1 (central Worker). Today it is set by hand (CLI/admin
-- endpoint); once the Worker exists, it overwrites this flag without a schema change.
CREATE TABLE IF NOT EXISTS wan_access (
    user_id     INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    is_active   INTEGER NOT NULL DEFAULT 0 CHECK (is_active IN (0, 1)),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
