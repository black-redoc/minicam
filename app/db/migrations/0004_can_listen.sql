-- Per-camera permission to hear the camera's microphone (live audio and,
-- later, audio in event recordings). Off for every existing grant: audio
-- can carry conversations, so it is never granted implicitly with can_view.
ALTER TABLE camera_permissions
    ADD COLUMN can_listen INTEGER NOT NULL DEFAULT 0 CHECK (can_listen IN (0, 1));
