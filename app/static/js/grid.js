/**
 * GridWidget — renders multiple VideoStream instances in a responsive CSS grid.
 *
 * Usage:
 *   const grid = new GridWidget(document.getElementById('grid-view'));
 *   grid.mount([{ name: 'cam1' }, { name: 'cam2' }]);
 *   grid.unmount();
 *
 * Dispatches 'grid:select' on document when a cell is clicked:
 *   document.addEventListener('grid:select', (e) => console.log(e.detail.name));
 */
/* exported GridWidget */

// 1×1 transparent GIF — used as initial img src so the browser never shows
// the broken-image icon while the WebSocket is still connecting.
const _BLANK = 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7';

class GridWidget {
    #container
    #streams   = new Map()
    #lastFrames = new Map()   // cameraName → last blob URL, persists across unmount/mount cycles

    /** @param {HTMLElement} containerEl */
    constructor(containerEl) {
        this.#container = containerEl;
    }

    /**
     * Build the grid and connect one VideoStream per camera.
     * If a previous frame is stored for a camera it is shown immediately so
     * the cell does not flash black while the WebSocket reconnects.
     * @param {Array<{name: string}>} cameras
     */
    mount(cameras) {
        if (!cameras.length) return;

        const cols     = this.#colCount(cameras.length);
        const colClass = cols === 1 ? 'grid-cols-1' : cols === 2 ? 'grid-cols-2' : 'grid-cols-3';

        this.#container.innerHTML = `
            <div class="grid ${colClass} gap-px bg-sky-950">
                ${cameras.map((cam, i) => this.#cellHtml(cam, i)).join('')}
            </div>
        `;

        cameras.forEach((cam, i) => {
            const imgEl     = this.#container.querySelector(`#grid-img-${i}`);
            const overlayEl = this.#container.querySelector(`#grid-overlay-${i}`);

            const lastSrc = this.#lastFrames.get(cam.name);
            if (lastSrc) {
                imgEl.src = lastSrc;
                overlayEl.classList.add('hidden');
            }

            const badgeEl   = this.#container.querySelector(`#grid-badge-${i}`);
            const stream = new VideoStream({ img: imgEl, overlay: overlayEl, badge: badgeEl, cameraName: cam.name });
            stream.connect();
            this.#streams.set(cam.name, stream);
        });

        this.#container.querySelectorAll('[data-grid-cam]').forEach((cell) => {
            cell.addEventListener('click', () => {
                document.dispatchEvent(
                    new CustomEvent('grid:select', { detail: { name: cell.dataset.gridCam } })
                );
            });
        });
    }

    /**
     * Snapshot each cell's last rendered frame, disconnect all streams, and
     * clear the container.  Snapshots survive until the next mount() call.
     */
    unmount() {
        this.#container.querySelectorAll('[data-grid-cam]').forEach((cell) => {
            const img = cell.querySelector('img');
            if (img?.src?.startsWith('blob:')) {
                this.#lastFrames.set(cell.dataset.gridCam, img.src);
            }
        });

        this.#streams.forEach((stream) => stream.disconnect());
        this.#streams.clear();
        this.#container.innerHTML = '';
    }

    // ── Private ──────────────────────────────────────────────

    /** @param {number} n @returns {1|2|3} */
    #colCount(n) {
        if (n <= 1) return 1;
        if (n <= 4) return 2;
        return 3;
    }

    /**
     * @param {{ name: string }} cam
     * @param {number} i - Index used for element IDs.
     * @returns {string}
     */
    #cellHtml(cam, i) {
        const name = _gridEsc(cam.name);
        return `
            <div class="relative aspect-video bg-black overflow-hidden cursor-pointer
                        ring-2 ring-inset ring-sky-950 hover:ring-4 hover:ring-sky-400
                        transition-shadow duration-150"
                 data-grid-cam="${name}">
                <img id="grid-img-${i}" src="${_BLANK}"
                     class="w-full h-full object-contain block"
                     draggable="false" alt="">
                <div id="grid-overlay-${i}" class="video-overlay">
                    <div class="video-spinner"></div>
                </div>
                <div class="absolute bottom-0 left-0 right-0
                            bg-gradient-to-t from-black/70 to-transparent
                            px-2 py-1.5 pointer-events-none
                            flex items-center justify-between gap-2">
                    <span class="text-white text-xs font-mono truncate">${name}</span>
                    <span id="grid-badge-${i}" class="badge badge--connecting shrink-0">
                        <span data-label>Connecting…</span>
                    </span>
                </div>
            </div>
        `;
    }
}

function _gridEsc(str) {
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}
