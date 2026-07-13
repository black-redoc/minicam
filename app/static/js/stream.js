/**
 * VideoStream — manages a WebSocket connection that receives binary JPEG
 * frames and renders them to an <img> element.
 */
/* exported VideoStream */
class VideoStream {
    #img
    #overlay
    #fpsEl
    #badgeEl
    #cameraName

    #ws = null
    #prevUrl = null
    #frameCount = 0
    #fpsTimestamp = 0
    #reconnectTimer = null
    #reconnectDelay = 3000

    /**
     * @param {object} opts
     * @param {HTMLImageElement}  opts.img         - Target <img> for the camera feed.
     * @param {HTMLElement}       opts.overlay     - Overlay shown when disconnected.
     * @param {HTMLElement}       [opts.fps]       - Element displaying FPS counter (optional).
     * @param {HTMLElement}       opts.badge       - Status badge element.
     * @param {string}            opts.cameraName  - Initial camera name for the stream URL.
     */
    constructor({ img, overlay, fps, badge, cameraName }) {
        this.#img        = img;
        this.#overlay    = overlay;
        this.#fpsEl      = fps;
        this.#badgeEl    = badge;
        this.#cameraName = cameraName;
    }

    async connect() {
        if (this.#ws) {
            this.#ws.onclose = null;
            this.#ws.close();
            this.#ws = null;
        }

        this.#setStatus('connecting', 'Connecting…');

        let token;
        try {
            token = await this.#fetchStreamToken();
        } catch {
            // No session, no permission on this camera, or WAN access gate
            // closed — same recovery path as a dropped socket: back off
            // and retry, so it self-heals once the caller logs in / is
            // granted access without a page reload.
            this.#setStatus('disconnected', 'Unauthorized');
            this.#overlay.classList.remove('hidden');
            this.#reconnectTimer = setTimeout(() => this.connect(), this.#reconnectDelay);
            return;
        }

        const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
        const url   = `${proto}//${location.host}/ws/stream/`
                    + `${encodeURIComponent(this.#cameraName)}?token=${encodeURIComponent(token)}`;

        this.#ws            = new WebSocket(url);
        this.#ws.binaryType = 'blob';
        this.#ws.onopen     = () => this.#onOpen();
        this.#ws.onmessage  = (e) => this.#onFrame(e.data);
        this.#ws.onclose    = () => this.#onClose();
        this.#ws.onerror    = () => this.#onError();
    }

    /**
     * Mint a short-lived, camera-bound token for the WebSocket handshake
     * (see POST /api/stream/{camera_name}/token). Distinct from the
     * session cookie — the WS route only accepts this token, by design.
     *
     * @returns {Promise<string>}
     */
    async #fetchStreamToken() {
        const res = await authedFetch(
            `/api/stream/${encodeURIComponent(this.#cameraName)}/token`,
            { method: 'POST' },
        );
        if (!res.ok) {
            throw new Error(`Failed to obtain stream token: ${res.status}`);
        }
        const data = await res.json();
        return data.token;
    }

    disconnect() {
        clearTimeout(this.#reconnectTimer);
        if (this.#ws) {
            this.#ws.onclose = null;
            this.#ws.close();
            this.#ws = null;
        }
    }

    /**
     * Close the current stream and immediately reconnect to a different camera.
     * Nullifies the close handler first to prevent the auto-reconnect timer
     * in #onClose from racing with the new connection.
     *
     * @param {string} name - The new camera name.
     */
    switchCamera(name) {
        clearTimeout(this.#reconnectTimer);
        this.#overlay.classList.remove('hidden');
        this.#cameraName = name;
        if (this.#ws) {
            this.#ws.onclose = null;
            this.#ws.close();
            this.#ws = null;
        }
        this.connect();
    }

    // ── Private ──────────────────────────────────────────────

    #onOpen() {
        this.#setStatus('connected', 'Connected');
        this.#frameCount   = 0;
        this.#fpsTimestamp = performance.now();
    }

    #onFrame(blob) {
        const url = URL.createObjectURL(blob);

        if (this.#prevUrl) URL.revokeObjectURL(this.#prevUrl);
        this.#img.src = url;
        this.#prevUrl = url;

        this.#overlay.classList.add('hidden');

        this.#frameCount++;
        const now = performance.now();
        if (this.#fpsEl && now - this.#fpsTimestamp >= 1000) {
            this.#fpsEl.textContent = `${this.#frameCount} FPS`;
            this.#frameCount        = 0;
            this.#fpsTimestamp      = now;
        }
    }

    #onClose() {
        this.#setStatus('disconnected', 'Disconnected');
        this.#overlay.classList.remove('hidden');
        this.#reconnectTimer = setTimeout(() => this.connect(), this.#reconnectDelay);
    }

    #onError() {
        this.#setStatus('disconnected', 'Error');
    }

    #setStatus(state, label) {
        if (!this.#badgeEl) return;
        this.#badgeEl.className = `badge badge--${state}`;
        this.#badgeEl.querySelector('[data-label]').textContent = label;
    }
}
