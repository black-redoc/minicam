/**
 * VideoStream — manages a WebSocket connection that receives binary JPEG
 * frames and renders them to an <img> element.
 */
class VideoStream {
    #img
    #overlay
    #fpsEl
    #badgeEl

    #ws = null
    #prevUrl = null
    #frameCount = 0
    #fpsTimestamp = 0
    #reconnectTimer = null
    #reconnectDelay = 3000

    /**
     * @param {object} els
     * @param {HTMLImageElement}  els.img     - Target <img> for the camera feed.
     * @param {HTMLElement}       els.overlay - Overlay shown when disconnected.
     * @param {HTMLElement}       els.fps     - Element displaying FPS counter.
     * @param {HTMLElement}       els.badge   - Status badge element.
     */
    constructor({ img, overlay, fps, badge }) {
        this.#img     = img;
        this.#overlay = overlay;
        this.#fpsEl   = fps;
        this.#badgeEl = badge;
    }

    connect() {
        const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
        const url   = `${proto}//${location.host}/ws/stream`;

        this.#ws             = new WebSocket(url);
        this.#ws.binaryType  = 'blob';
        this.#ws.onopen      = () => this.#onOpen();
        this.#ws.onmessage   = (e) => this.#onFrame(e.data);
        this.#ws.onclose     = () => this.#onClose();
        this.#ws.onerror     = () => this.#onError();

        this.#setStatus('connecting', 'Connecting…');
    }

    disconnect() {
        clearTimeout(this.#reconnectTimer);
        this.#ws?.close();
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
