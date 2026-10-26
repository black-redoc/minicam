-- Last successful pull of WAN entitlements from the central dashboard
-- (minicam-admin ADR 0007). One row at most. In central mode the WAN gate
-- only trusts wan_access while this is recent: no row, or an old one, means
-- WAN is denied (fail closed). Standalone installs never write it.
CREATE TABLE entitlement_sync (
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    synced_at  TEXT NOT NULL
);
