/**
 * Recordings page — browse and play event clips.
 *
 *   GET    /api/recordings?camera=&before=&limit= - newest first, paginated
 *   GET    /api/recordings/{id}/video[?download=true] - the MP4 clip
 *   DELETE /api/recordings/{id} - admins only
 *
 * The server only returns clips of cameras the user can view, so this page
 * never filters by permission itself.
 */

const RECORDINGS_API = '/api/recordings';
const RECORDINGS_PAGE_SIZE = 30;

/**
 * @typedef {{id: number, camera_name: string, started_at: string,
 *            duration_seconds: number, size_bytes: number,
 *            classes: string[]}} RecordingItem
 */

/** @param {number} seconds */
function formatClipDuration(seconds) {
    const total = Math.max(0, Math.round(seconds));
    const minutes = Math.floor(total / 60);
    return `${minutes}:${String(total % 60).padStart(2, '0')}`;
}

/** @param {number} bytes */
function formatClipSize(bytes) {
    if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
    if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
    return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

/** @param {Date} date */
function dayLabel(date) {
    const today = new Date();
    const yesterday = new Date();
    yesterday.setDate(today.getDate() - 1);
    if (date.toDateString() === today.toDateString()) return 'Today';
    if (date.toDateString() === yesterday.toDateString()) return 'Yesterday';
    return date.toLocaleDateString(undefined, {
        weekday: 'long', year: 'numeric', month: 'long', day: 'numeric',
    });
}

class RecordingsPage {
    #listEl
    #moreBtn
    #errorEl
    #filterEl
    #playerEl
    #videoEl
    #titleEl
    #metaEl
    #downloadEl
    #deleteBtn
    /** @type {RecordingItem[]} */
    #items = [];
    /** @type {number | null} */
    #nextBefore = null;
    /** @type {number | null} */
    #selectedId = null;
    #loading = false;

    constructor() {
        this.#listEl     = /** @type {HTMLElement} */ (document.getElementById('recordings-list'));
        this.#moreBtn    = /** @type {HTMLButtonElement} */ (document.getElementById('recordings-more'));
        this.#errorEl    = /** @type {HTMLElement} */ (document.getElementById('recordings-error'));
        this.#filterEl   = /** @type {HTMLSelectElement} */ (document.getElementById('recordings-camera-filter'));
        this.#playerEl   = /** @type {HTMLElement} */ (document.getElementById('recording-player'));
        this.#videoEl    = /** @type {HTMLVideoElement} */ (document.getElementById('recording-video'));
        this.#titleEl    = /** @type {HTMLElement} */ (document.getElementById('recording-title'));
        this.#metaEl     = /** @type {HTMLElement} */ (document.getElementById('recording-meta'));
        this.#downloadEl = /** @type {HTMLAnchorElement} */ (document.getElementById('recording-download'));
        // Rendered for admins only (the API enforces it as well).
        this.#deleteBtn  = /** @type {HTMLButtonElement | null} */ (document.getElementById('recording-delete'));

        this.#moreBtn.addEventListener('click', () => this.#load(false));
        this.#filterEl.addEventListener('change', () => this.#load(true));
        this.#listEl.addEventListener('click', (e) => {
            const row = e.target instanceof Element ? e.target.closest('[data-recording-id]') : null;
            if (row instanceof HTMLElement) this.#select(Number(row.dataset.recordingId));
        });
        this.#deleteBtn?.addEventListener('click', () => this.#deleteSelected());
    }

    start() {
        this.#load(true);
    }

    /** @param {boolean} reset */
    async #load(reset) {
        if (this.#loading) return;
        this.#loading = true;
        this.#moreBtn.disabled = true;
        this.#errorEl.classList.add('hidden');
        const params = new URLSearchParams({ limit: String(RECORDINGS_PAGE_SIZE) });
        if (this.#filterEl.value) params.set('camera', this.#filterEl.value);
        if (!reset && this.#nextBefore !== null) params.set('before', String(this.#nextBefore));
        try {
            const res = await authedFetch(`${RECORDINGS_API}?${params}`);
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            /** @type {{items: RecordingItem[], next_before: number | null}} */
            const page = await res.json();
            this.#items = reset ? page.items : this.#items.concat(page.items);
            this.#nextBefore = page.next_before;
            this.#render();
        } catch (err) {
            if (err instanceof SessionExpiredError) return;
            this.#showError('Could not load recordings.');
        } finally {
            this.#loading = false;
            this.#moreBtn.disabled = false;
        }
    }

    #render() {
        this.#moreBtn.classList.toggle('hidden', this.#nextBefore === null);
        if (this.#items.length === 0) {
            const empty = document.createElement('p');
            empty.className = 'text-sm text-slate-500 px-1';
            empty.textContent = this.#filterEl.value
                ? 'No recordings for this camera yet.'
                : 'No recordings yet. Clips appear here when a camera detects one of its classes.';
            this.#listEl.replaceChildren(empty);
            return;
        }

        /** @type {Map<string, RecordingItem[]>} */
        const byDay = new Map();
        for (const item of this.#items) {
            const label = dayLabel(new Date(item.started_at));
            const group = byDay.get(label) ?? [];
            group.push(item);
            byDay.set(label, group);
        }

        this.#listEl.replaceChildren(...[...byDay].map(([label, items]) => {
            const section = document.createElement('section');
            section.className = 'flex flex-col gap-2';
            const heading = document.createElement('h3');
            heading.className = 'text-xs text-slate-400 font-medium px-1';
            heading.textContent = label;
            const list = document.createElement('ul');
            list.className = 'card !p-0 flex flex-col divide-y divide-slate-700/60 overflow-hidden';
            list.append(...items.map((item) => this.#renderItem(item)));
            section.append(heading, list);
            return section;
        }));
    }

    /** @param {RecordingItem} item */
    #renderItem(item) {
        const li = document.createElement('li');
        const row = document.createElement('button');
        row.type = 'button';
        row.dataset.recordingId = String(item.id);
        const selected = item.id === this.#selectedId;
        row.setAttribute('aria-current', String(selected));
        row.className =
            'w-full flex items-center gap-3 px-4 py-3 text-left transition-colors ' +
            'focus:outline-none focus:bg-slate-700/60 ' +
            (selected ? 'bg-sky-500/10' : 'hover:bg-slate-700/40');

        const time = document.createElement('span');
        time.className = `font-mono text-sm tabular-nums ${selected ? 'text-sky-400' : 'text-slate-100'}`;
        time.textContent = new Date(item.started_at).toLocaleTimeString();

        const middle = document.createElement('span');
        middle.className = 'flex flex-col sm:flex-row sm:items-center gap-1 sm:gap-2 min-w-0 flex-1';
        const camera = document.createElement('span');
        camera.className = 'text-sm text-slate-300 truncate';
        camera.textContent = item.camera_name;
        const chips = document.createElement('span');
        chips.className = 'flex flex-wrap gap-1';
        for (const cls of item.classes) {
            const chip = document.createElement('span');
            chip.className = 'text-[11px] px-1.5 py-0.5 rounded bg-slate-700 text-slate-300';
            chip.textContent = cls;
            chips.append(chip);
        }
        middle.append(camera, chips);

        const facts = document.createElement('span');
        facts.className = 'flex flex-col items-end text-xs text-slate-400 tabular-nums shrink-0';
        const duration = document.createElement('span');
        duration.textContent = formatClipDuration(item.duration_seconds);
        const size = document.createElement('span');
        size.className = 'text-slate-500';
        size.textContent = formatClipSize(item.size_bytes);
        facts.append(duration, size);

        row.append(time, middle, facts);
        li.append(row);
        return li;
    }

    /** @param {number} id */
    #select(id) {
        const item = this.#items.find((i) => i.id === id);
        if (!item) return;
        this.#selectedId = id;
        const url = `${RECORDINGS_API}/${id}/video`;
        this.#videoEl.src = url;
        this.#downloadEl.href = `${url}?download=true`;
        const started = new Date(item.started_at);
        this.#titleEl.textContent = item.camera_name;
        const details = [
            `${dayLabel(started)}, ${started.toLocaleTimeString()}`,
            formatClipDuration(item.duration_seconds),
            formatClipSize(item.size_bytes),
        ];
        if (item.classes.length) details.push(item.classes.join(', '));
        this.#metaEl.textContent = details.join(' · ');
        this.#playerEl.classList.remove('hidden');
        this.#playerEl.scrollIntoView({ behavior: 'smooth', block: 'start' });
        this.#videoEl.play().catch(() => {
            // Autoplay can be refused (e.g. data saver); the controls remain.
        });
        this.#render();
    }

    async #deleteSelected() {
        const id = this.#selectedId;
        if (id === null) return;
        const confirmed = await confirmDialog({
            title: 'Delete recording?',
            message: 'The clip is deleted from this device for every user.',
            confirmLabel: 'Delete',
            danger: true,
        });
        if (!confirmed) return;
        try {
            const res = await authedFetch(`${RECORDINGS_API}/${id}`, { method: 'DELETE' });
            if (!res.ok && res.status !== 404) throw new Error(`HTTP ${res.status}`);
        } catch (err) {
            if (err instanceof SessionExpiredError) return;
            this.#showError('Could not delete the recording.');
            return;
        }
        this.#items = this.#items.filter((i) => i.id !== id);
        this.#selectedId = null;
        this.#videoEl.removeAttribute('src');
        this.#videoEl.load();
        this.#playerEl.classList.add('hidden');
        this.#render();
    }

    /** @param {string} message */
    #showError(message) {
        this.#errorEl.textContent = message;
        this.#errorEl.classList.remove('hidden');
    }
}

new RecordingsPage().start();
