"""Process-wide lock around PyAV/FFmpeg container open/close.

PyAV/FFmpeg's container open/close is not thread-safe even across unrelated
streams: concurrent av.open()/close() calls from different capture threads
corrupted the process heap in production. Demux/decode/mux of an already-open
container is unaffected and still runs fully in parallel; only the open/close
sequence is serialized, by every thread that opens a container (RTSP capture
and event recording alike).
"""

from __future__ import annotations

import threading

av_io_lock = threading.Lock()
