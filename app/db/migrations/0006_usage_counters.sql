-- WAN streaming usage reported to the central dashboard (minicam-admin ADR 0008, minicam ADR 0004).
-- usage_reporter holds this install's random reporter ID, created once and kept, so the dashboard
-- can tell a wiped SD card apart from a counter that went down. usage_counters holds cumulative WAN
-- live-stream bytes per Colombia month (UTC-5), lowercased user email and camera; acked_bytes is the
-- last value the dashboard accepted. Standalone installs never write either table.
CREATE TABLE usage_reporter (
    id           INTEGER PRIMARY KEY CHECK (id = 1),
    reporter_id  TEXT NOT NULL
);

CREATE TABLE usage_counters (
    month        TEXT NOT NULL,
    email        TEXT NOT NULL,
    camera       TEXT NOT NULL,
    wan_bytes    INTEGER NOT NULL CHECK (wan_bytes >= 0),
    acked_bytes  INTEGER NOT NULL DEFAULT 0 CHECK (acked_bytes >= 0 AND acked_bytes <= wan_bytes),
    PRIMARY KEY (month, email, camera)
);
