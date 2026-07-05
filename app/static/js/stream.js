/**
 * VideoStream — manages a WebSocket connection that receives binary JPEG
 * frames and renders them to an <img> element.
 */
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
     * @param {HTMLElement}       opts.fps         - Element displaying FPS counter.
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

    connect() {
        const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
        const url   = `${proto}//${location.host}/ws/stream/${encodeURIComponent(this.#cameraName)}`;

        this.#ws            = new WebSocket(url);
        this.#ws.binaryType = 'blob';
        this.#ws.onopen     = () => this.#onOpen();
        this.#ws.onmessage  = (e) => this.#onFrame(e.data);
        this.#ws.onclose    = () => this.#onClose();
        this.#ws.onerror    = () => this.#onError();

        this.#setStatus('connecting', 'Connecting…');
    }

    disconnect() {
        clearTimeout(this.#reconnectTimer);
        this.#ws?.close();
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
        if (now - this.#fpsTimestamp >= 1000) {
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
        this.#badgeEl.className = `badge badge--${state}`;
        this.#badgeEl.querySelector('[data-label]').textContent = label;
    }
}
