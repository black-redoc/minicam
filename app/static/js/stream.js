/**
 * Live camera view over a WebSocket, in one of two formats:
 *
 * - fmp4: the camera's own compressed video (H.264 or HEVC) as fragmented
 *   MP4, played in a <video> through Media Source Extensions. Far lighter
 *   than JPEG frames, which matters through the Cloudflare tunnel.
 * - mjpeg: one JPEG per frame in an <img>. Used when the browser can't
 *   play the camera's codec (e.g. HEVC in Firefox), or fMP4 fails.
 */

// Behind the live edge by more than this, the player jumps forward.
const LIVE_MAX_LAG_SECONDS = 1.5;
// Where it lands: just behind the newest buffered frame.
const LIVE_TARGET_LAG_SECONDS = 0.3;
// Buffered video older than this behind the playhead is freed.
const LIVE_KEEP_SECONDS = 10;

/**
 * Short codec name for the FPS indicator, e.g. "HEVC" for hvc1.1.6.L90.
 * @param {string} mime
 */
function codecLabel(mime) {
    if (/codecs="(hvc1|hev1)/.test(mime)) return 'HEVC';
    if (/codecs="(avc1|avc3)/.test(mime)) return 'H.264';
    return 'fMP4';
}

/** @returns {any} MediaSource, or iOS Safari's ManagedMediaSource, if any. */
function mediaSourceClass() {
    const w = /** @type {any} */ (window);
    return w.ManagedMediaSource ?? w.MediaSource ?? null;
}

/**
 * @typedef {{label: string, confidence: number, color: string,
 *            x1: number, y1: number, x2: number, y2: number}} DetectionBox
 * @typedef {{type: 'detections', width: number, height: number,
 *            boxes: DetectionBox[]}} DetectionsMessage
 */

/**
 * Plays the fMP4 messages of one WebSocket connection: a JSON
 * {type: 'init', mime} followed by the binary init segment (again whenever
 * the camera reconnects), then one binary fragment per frame, plus JSON
 * {type: 'detections'} messages drawn on a canvas over the video.
 */
class Fmp4Player {
    #video
    #canvas
    #onFailure
    #onFragment
    /** @type {any} */
    #mediaSource = null
    /** @type {SourceBuffer | null} */
    #sourceBuffer = null
    #objectUrl = null
    /** @type {ArrayBuffer[]} */
    #queue = []
    /** @type {DetectionsMessage | null} */
    #detections = null
    #resizeObserver

    /**
     * @param {HTMLVideoElement}  video
     * @param {HTMLCanvasElement} canvas
     * @param {() => void}        onFailure  - The browser can't play this stream.
     * @param {() => void}        onFragment - A frame was received.
     */
    constructor(video, canvas, onFailure, onFragment) {
        this.#video      = video;
        this.#canvas     = canvas;
        this.#onFailure  = onFailure;
        this.#onFragment = onFragment;
        this.#resizeObserver = new ResizeObserver(() => this.#drawDetections());
        this.#resizeObserver.observe(canvas);
    }

    /** @param {string} text */
    handleText(text) {
        const message = JSON.parse(text);
        if (message.type === 'init') {
            this.#start(message.mime);
        } else if (message.type === 'detections') {
            this.#detections = message;
            this.#drawDetections();
        }
    }

    /** @param {ArrayBuffer} data */
    handleBinary(data) {
        if (!this.#mediaSource) return;  // no init yet: nothing to play
        this.#queue.push(data);
        this.#pump();
        this.#onFragment();
    }

    destroy() {
        this.#resizeObserver.disconnect();
        this.#detections = null;
        this.#drawDetections();
        this.#teardown();
    }

    // ── Private ──────────────────────────────────────────────

    /** @param {string} mime */
    #start(mime) {
        this.#teardown();
        const MediaSourceImpl = mediaSourceClass();
        if (!MediaSourceImpl || !MediaSourceImpl.isTypeSupported(mime)) {
            this.#onFailure();
            return;
        }
        const mediaSource = new MediaSourceImpl();
        this.#mediaSource = mediaSource;
        mediaSource.addEventListener('sourceopen', () => {
            if (this.#mediaSource !== mediaSource) return;
            try {
                const sourceBuffer = mediaSource.addSourceBuffer(mime);
                sourceBuffer.mode = 'segments';
                sourceBuffer.addEventListener('updateend', () => this.#onUpdateEnd());
                sourceBuffer.addEventListener('error', () => this.#onFailure());
                this.#sourceBuffer = sourceBuffer;
            } catch {
                this.#onFailure();
                return;
            }
            this.#pump();
        }, { once: true });
        // ManagedMediaSource (iOS) only streams with remote playback off.
        this.#video.disableRemotePlayback = true;
        this.#objectUrl = URL.createObjectURL(mediaSource);
        this.#video.src = this.#objectUrl;
    }

    #teardown() {
        this.#queue = [];
        this.#sourceBuffer = null;
        this.#mediaSource = null;
        if (this.#objectUrl) {
            URL.revokeObjectURL(this.#objectUrl);
            this.#objectUrl = null;
            this.#video.removeAttribute('src');
            this.#video.load();
        }
    }

    #pump() {
        const sourceBuffer = this.#sourceBuffer;
        if (!sourceBuffer || sourceBuffer.updating || this.#queue.length === 0) return;
        try {
            sourceBuffer.appendBuffer(this.#queue.shift());
        } catch (err) {
            if (err instanceof DOMException && err.name === 'QuotaExceededError') {
                // Buffer full: drop the backlog and resume at the live edge.
                this.#queue = [];
                this.#trim(0);
            } else {
                this.#onFailure();
            }
        }
    }

    #onUpdateEnd() {
        this.#followLiveEdge();
        if (this.#queue.length > 0) {
            this.#pump();
        } else {
            this.#trim(LIVE_KEEP_SECONDS);
        }
    }

    /** Keep playback near the newest frame instead of drifting behind. */
    #followLiveEdge() {
        const video = this.#video;
        const buffered = video.buffered;
        if (buffered.length === 0) return;
        const start = buffered.start(buffered.length - 1);
        const end   = buffered.end(buffered.length - 1);
        if (video.currentTime < start || end - video.currentTime > LIVE_MAX_LAG_SECONDS) {
            video.currentTime = Math.max(start, end - LIVE_TARGET_LAG_SECONDS);
        }
        if (video.paused) {
            video.play().catch(() => {
                // Autoplay refused: the video is muted, so this is rare.
            });
        }
    }

    /** @param {number} keepSeconds - Buffered video to keep behind the playhead. */
    #trim(keepSeconds) {
        const sourceBuffer = this.#sourceBuffer;
        const buffered = this.#video.buffered;
        if (!sourceBuffer || sourceBuffer.updating || buffered.length === 0) return;
        const removeEnd = this.#video.currentTime - keepSeconds;
        if (removeEnd - buffered.start(0) > 1) {
            sourceBuffer.remove(buffered.start(0), removeEnd);
        }
    }

    #drawDetections() {
        const canvas = this.#canvas;
        const ratio  = window.devicePixelRatio || 1;
        canvas.width  = Math.round(canvas.clientWidth * ratio);
        canvas.height = Math.round(canvas.clientHeight * ratio);
        const ctx = canvas.getContext('2d');
        if (!ctx) return;
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        const message = this.#detections;
        if (!message || !message.width || !message.height) return;

        // The video is letterboxed (object-fit: contain): map frame pixels
        // to where the frame is actually drawn.
        const scale   = Math.min(canvas.width / message.width, canvas.height / message.height);
        const offsetX = (canvas.width - message.width * scale) / 2;
        const offsetY = (canvas.height - message.height * scale) / 2;
        ctx.lineWidth = 3 * ratio;
        ctx.font = `${12 * ratio}px ui-monospace, monospace`;
        ctx.textBaseline = 'bottom';
        for (const box of message.boxes) {
            const x = offsetX + box.x1 * scale;
            const y = offsetY + box.y1 * scale;
            ctx.strokeStyle = box.color;
            ctx.strokeRect(x, y, (box.x2 - box.x1) * scale, (box.y2 - box.y1) * scale);
            const label = `${box.label} ${Math.round(box.confidence * 100)}%`;
            const labelWidth = ctx.measureText(label).width + 8 * ratio;
            const labelHeight = 16 * ratio;
            const labelY = Math.max(labelHeight, y);
            ctx.fillStyle = box.color;
            ctx.fillRect(x, labelY - labelHeight, labelWidth, labelHeight);
            ctx.fillStyle = '#fff';
            ctx.fillText(label, x + 4 * ratio, labelY - 2 * ratio);
        }
    }
}

/**
 * VideoStream — one camera's live view: picks fMP4 when the browser can
 * play the camera's codec, MJPEG otherwise, and reconnects on its own.
 *
 * While the page is hidden (background tab, minimized window) the socket is
 * closed and reopened on return, so hidden tabs cost the server no encode
 * work and no bandwidth through the tunnel.
 */
/* exported VideoStream */
class VideoStream {
    #img
    #video
    #canvas
    #overlay
    #fpsEl
    #badgeEl
    #cameraName
    /** @type {Fmp4Player | null} */
    #player = null
    // Set once fMP4 failed for this view: stay on MJPEG until the camera
    // is switched, instead of failing again on every reconnect.
    #fmp4Failed = false
    // Shown next to the FPS: the codec in fMP4 mode, or "MJPEG".
    #formatLabel = ''
    /** @type {HTMLButtonElement | null} */
    #audioButton = null
    // The viewer turned audio on. Never remembered: every new view of a
    // camera (connect after disconnect, switchCamera) starts without it.
    #audioOn = false

    #ws = null
    #prevUrl = null
    #frameCount = 0
    #fpsTimestamp = 0
    // fMP4: the <video>'s decoded-frame count when the FPS window began.
    #videoFramesBase = 0
    #reconnectTimer = null
    #reconnectDelay = 3000
    // True between connect()/switchCamera() and disconnect().
    #active = false
    // True while closed only because the page is hidden.
    #paused = false
    // Bumped by every connect/pause/disconnect, so a connect() still
    // awaiting its token can tell it has been superseded.
    #connectSeq = 0
    #onVisibilityChange = () => this.#handleVisibilityChange()

    /**
     * @param {object} opts
     * @param {HTMLImageElement}  opts.img         - Target <img> for the camera feed.
     * @param {HTMLElement}       opts.overlay     - Overlay shown when disconnected.
     * @param {HTMLElement}       [opts.fps]       - Element displaying FPS counter (optional).
     * @param {HTMLElement}       opts.badge       - Status badge element.
     * @param {string}            opts.cameraName  - Initial camera name for the stream URL.
     * @param {HTMLButtonElement} [opts.audioButton] - Speaker toggle, shown only when
     *   the camera has audio this viewer may hear and the browser can play.
     */
    constructor({ img, overlay, fps, badge, cameraName, audioButton }) {
        this.#img        = img;
        this.#overlay    = overlay;
        this.#fpsEl      = fps;
        this.#badgeEl    = badge;
        this.#cameraName = cameraName;
        this.#audioButton = audioButton ?? null;
        this.#audioButton?.addEventListener('click', () => this.#toggleAudio());

        // The <video> (fMP4) and the boxes canvas sit over the <img>
        // (MJPEG). In fMP4 mode the <img> keeps showing the last frame
        // (a snapshot) until the video has decoded one of its own.
        this.#video = document.createElement('video');
        this.#video.className = 'live-video';
        this.#video.muted = true;
        this.#video.autoplay = true;
        this.#video.playsInline = true;
        this.#video.style.display = 'none';
        this.#video.addEventListener('loadeddata', () => {
            if (this.#player) this.#img.style.display = 'none';
        });
        this.#canvas = document.createElement('canvas');
        this.#canvas.className = 'detection-canvas';
        img.after(this.#video, this.#canvas);
    }

    /**
     * The frame on screen as a JPEG data: URL, or null if there is none.
     * A copy, so it stays valid after this stream moves on or disconnects.
     *
     * @returns {string | null}
     */
    snapshotUrl() {
        const video = this.#video;
        const fromVideo = video.style.display !== 'none' && video.videoWidth > 0;
        const img = this.#img;
        const fromImg = !fromVideo && img.style.display !== 'none'
                      && img.src.startsWith('blob:') && img.naturalWidth > 0;
        if (!fromVideo && !fromImg) return null;
        const canvas = document.createElement('canvas');
        canvas.width  = fromVideo ? video.videoWidth : img.naturalWidth;
        canvas.height = fromVideo ? video.videoHeight : img.naturalHeight;
        const ctx = canvas.getContext('2d');
        if (!ctx) return null;
        ctx.drawImage(fromVideo ? video : img, 0, 0);
        return canvas.toDataURL('image/jpeg', 0.85);
    }

    /**
     * Show a still frame (e.g. from snapshotUrl() of another view of this
     * camera) until the stream delivers a frame of its own.
     *
     * @param {string} url
     */
    showSnapshot(url) {
        this.#img.src = url;
        this.#img.style.display = '';
        this.#overlay.classList.add('hidden');
    }

    async connect() {
        if (!this.#active) {
            this.#active = true;
            document.addEventListener('visibilitychange', this.#onVisibilityChange);
        }
        clearTimeout(this.#reconnectTimer);
        this.#closeSocket();
        const seq = ++this.#connectSeq;

        if (document.hidden) {
            this.#pause();
            return;
        }
        this.#paused = false;
        this.#setStatus('connecting', 'Connecting…');

        let token, videoMime, audioMime;
        try {
            ({ token, video_mime: videoMime, audio_mime: audioMime } =
                await this.#fetchStreamToken(this.#audioOn));
        } catch {
            if (seq !== this.#connectSeq) return;
            // Audio no longer allowed (can_listen revoked): retry without.
            this.#audioOn = false;
            // No session, no permission on this camera, or WAN access gate
            // closed — same recovery path as a dropped socket: back off
            // and retry, so it self-heals once the caller logs in / is
            // granted access without a page reload.
            this.#setStatus('disconnected', 'Unauthorized');
            this.#overlay.classList.remove('hidden');
            this.#reconnectTimer = setTimeout(() => this.connect(), this.#reconnectDelay);
            return;
        }
        if (seq !== this.#connectSeq) return;

        const MediaSourceImpl = mediaSourceClass();
        let mjpegReason = null;
        if (this.#fmp4Failed) mjpegReason = 'fMP4 playback failed';
        else if (!videoMime) mjpegReason = 'camera codec not known yet';
        else if (!MediaSourceImpl) mjpegReason = 'no Media Source Extensions';
        else if (!MediaSourceImpl.isTypeSupported(videoMime)) {
            mjpegReason = `browser can't play ${videoMime}`;
        }
        const fmp4 = mjpegReason === null;
        const audioPlayable = fmp4 && Boolean(audioMime)
                            && Boolean(MediaSourceImpl.isTypeSupported(audioMime));
        const audio = this.#audioOn && audioPlayable;
        this.#audioOn = audio;
        this.#updateAudioButton(audioPlayable);
        this.#video.muted = !audio;
        const mime = audio ? audioMime : videoMime;
        this.#formatLabel = fmp4 ? codecLabel(mime) + (audio ? ' + audio' : '') : 'MJPEG';
        console.info(`[minicam] ${this.#cameraName}: ${this.#formatLabel} (${fmp4 ? mime : mjpegReason})`);
        if (this.#fpsEl) this.#fpsEl.title = fmp4 ? `fMP4: ${mime}` : `MJPEG: ${mjpegReason}`;

        const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
        const url   = `${proto}//${location.host}/ws/stream/`
                    + `${encodeURIComponent(this.#cameraName)}?token=${encodeURIComponent(token)}`
                    + (fmp4 ? '&format=fmp4' : '')
                    + (audio ? '&audio=true' : '');

        this.#showVideo(fmp4);
        const ws = new WebSocket(url);
        this.#ws = ws;
        if (fmp4) {
            const player = new Fmp4Player(
                this.#video,
                this.#canvas,
                () => this.#onFmp4Failure(ws),
                () => this.#onFrameShown(),
            );
            this.#player = player;
            ws.binaryType = 'arraybuffer';
            ws.onmessage  = (e) => {
                if (typeof e.data === 'string') player.handleText(e.data);
                else player.handleBinary(e.data);
            };
        } else {
            ws.binaryType = 'blob';
            ws.onmessage  = (e) => this.#onFrame(e.data);
        }
        ws.onopen  = () => this.#onOpen();
        ws.onclose = () => this.#onClose();
        ws.onerror = () => this.#onError();
    }

    /**
     * Mint a short-lived, camera-bound token for the WebSocket handshake
     * (see POST /api/stream/{camera_name}/token). Distinct from the
     * session cookie — the WS route only accepts this token, by design.
     *
     * @param {boolean} audio - Also authorize the camera's audio (can_listen).
     * @returns {Promise<{token: string, video_mime: string | null,
     *                    audio_mime: string | null}>}
     */
    async #fetchStreamToken(audio) {
        const res = await authedFetch(
            `/api/stream/${encodeURIComponent(this.#cameraName)}/token`
            + (audio ? '?audio=true' : ''),
            { method: 'POST' },
        );
        if (!res.ok) {
            throw new Error(`Failed to obtain stream token: ${res.status}`);
        }
        return res.json();
    }

    disconnect() {
        this.#active = false;
        this.#paused = false;
        this.#audioOn = false;
        this.#updateAudioButton(false);
        this.#connectSeq++;
        document.removeEventListener('visibilitychange', this.#onVisibilityChange);
        clearTimeout(this.#reconnectTimer);
        this.#closeSocket();
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
        this.#fmp4Failed = false;
        this.#audioOn = false;
        this.#updateAudioButton(false);
        this.connect();
    }

    // ── Private ──────────────────────────────────────────────

    #handleVisibilityChange() {
        if (document.hidden) {
            this.#pause();
        } else if (this.#active && this.#paused) {
            this.connect();
        }
    }

    #pause() {
        clearTimeout(this.#reconnectTimer);
        this.#connectSeq++;
        this.#closeSocket();
        this.#paused = true;
        this.#setStatus('disconnected', 'Paused');
    }

    /** Close the socket without triggering the auto-reconnect in #onClose. */
    #closeSocket() {
        if (this.#ws) {
            this.#ws.onclose = null;
            this.#ws.close();
            this.#ws = null;
        }
        if (this.#player) {
            // Keep the last video frame on screen while reconnecting.
            const snapshot = this.snapshotUrl();
            if (snapshot) this.#img.src = snapshot;
            this.#img.style.display = '';
            this.#player.destroy();
            this.#player = null;
        }
    }

    /**
     * Show the <video> (fMP4) over the <img>, or the <img> alone (MJPEG).
     * The <img> is only hidden once the video has a frame (loadeddata).
     *
     * @param {boolean} fmp4
     */
    #showVideo(fmp4) {
        this.#video.style.display = fmp4 ? '' : 'none';
        if (!fmp4) this.#img.style.display = '';
    }

    #toggleAudio() {
        this.#audioOn = !this.#audioOn;
        // Unmuting inside the click lets the browser play sound; the stream
        // with (or without) audio then replaces the current one.
        this.#video.muted = !this.#audioOn;
        this.#updateAudioButton(true);
        this.connect();
    }

    /** @param {boolean} visible - The camera has audio this viewer can play. */
    #updateAudioButton(visible) {
        const button = this.#audioButton;
        if (!button) return;
        button.classList.toggle('hidden', !visible);
        button.setAttribute('aria-pressed', String(this.#audioOn));
        const label = this.#audioOn ? 'Turn audio off' : 'Turn audio on';
        button.setAttribute('aria-label', label);
        button.title = label;
        button.querySelector('[data-icon-on]')?.classList.toggle('hidden', !this.#audioOn);
        button.querySelector('[data-icon-off]')?.classList.toggle('hidden', this.#audioOn);
    }

    /**
     * The browser couldn't play the fMP4 stream: reconnect as MJPEG.
     * @param {WebSocket} ws - The socket the failing player belongs to.
     */
    #onFmp4Failure(ws) {
        if (ws !== this.#ws) return;
        this.#fmp4Failed = true;
        this.connect();
    }

    #onOpen() {
        this.#setStatus('connected', 'Connected');
        this.#frameCount   = 0;
        this.#fpsTimestamp = performance.now();
        this.#videoFramesBase = this.#decodedVideoFrames();
        if (this.#fpsEl) this.#fpsEl.textContent = `-- FPS · ${this.#formatLabel}`;
    }

    #onFrame(blob) {
        const url = URL.createObjectURL(blob);

        if (this.#prevUrl) URL.revokeObjectURL(this.#prevUrl);
        this.#img.src = url;
        this.#prevUrl = url;
        this.#onFrameShown();
    }

    #onFrameShown() {
        this.#overlay.classList.add('hidden');

        this.#frameCount++;
        const now = performance.now();
        if (this.#fpsEl && now - this.#fpsTimestamp >= 1000) {
            // fMP4 messages include audio fragments: count the frames the
            // <video> actually decoded instead.
            const decoded = this.#decodedVideoFrames();
            const frames  = this.#player ? decoded - this.#videoFramesBase : this.#frameCount;
            this.#fpsEl.textContent = `${frames} FPS · ${this.#formatLabel}`;
            this.#frameCount        = 0;
            this.#videoFramesBase   = decoded;
            this.#fpsTimestamp      = now;
        }
    }

    #decodedVideoFrames() {
        return this.#video.getVideoPlaybackQuality?.().totalVideoFrames ?? 0;
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
