/**
 * Admin page — event recording settings (all cameras).
 *
 *   GET /api/recordings/settings - {enabled, max_storage_gb, used_bytes,
 *                                   free_bytes, options: [{gb, available}]}
 *   PUT /api/recordings/settings - save {enabled, max_storage_gb}
 *
 * The storage limit is a custom listbox rather than a <select>: a limit
 * the disk can't hold stays visible (struck through, with the reason in
 * red) but can't be chosen, and native <option>s can't be styled that way.
 */

const RECORDING_SETTINGS_API = '/api/recordings/settings';

/**
 * @typedef {{gb: number, available: boolean}} StorageOption
 * @typedef {{enabled: boolean, max_storage_gb: number, used_bytes: number,
 *            free_bytes: number, options: StorageOption[]}} RecordingSettings
 */

/** @param {number} bytes */
function formatStorageBytes(bytes) {
    const gib = bytes / 1024 ** 3;
    if (gib >= 1) return `${gib.toFixed(1)} GB`;
    return `${Math.round(bytes / 1024 ** 2)} MB`;
}

class StorageLimitListbox {
    #button
    #valueLabel
    #list
    /** @type {StorageOption[]} */
    #options = [];
    /** @type {number | null} */
    #value = null;
    #activeIndex = -1;

    /**
     * @param {HTMLButtonElement} button
     * @param {HTMLUListElement}  list
     */
    constructor(button, list) {
        this.#button     = button;
        this.#valueLabel = /** @type {HTMLElement} */ (button.querySelector('[data-value-label]'));
        this.#list       = list;

        button.addEventListener('click', (e) => {
            e.stopPropagation();
            if (this.#isOpen()) this.#close(); else this.#open();
        });
        button.addEventListener('keydown', (e) => {
            if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
                e.preventDefault();
                this.#open();
            }
        });
        list.addEventListener('keydown', (e) => this.#onListKeydown(e));
        list.addEventListener('click', (e) => {
            const item = e.target instanceof Element ? e.target.closest('[role="option"]') : null;
            if (item instanceof HTMLElement) this.#choose(Number(item.dataset.index));
        });
        list.addEventListener('focusout', (e) => {
            const next = e.relatedTarget;
            // Focus moving to the button is left to its click handler (toggle).
            if (!(next instanceof Node) || !(list.contains(next) || button.contains(next))) this.#close();
        });
        document.addEventListener('click', (e) => {
            if (!this.#isOpen()) return;
            if (e.target instanceof Node && (button.contains(e.target) || list.contains(e.target))) return;
            this.#close();
        });
    }

    /** @returns {number | null} */
    get value() {
        return this.#value;
    }

    /**
     * @param {StorageOption[]} options
     * @param {number}          value
     */
    setOptions(options, value) {
        this.#options = options;
        this.#value = value;
        this.#valueLabel.textContent = `${value} GB`;
        this.#render();
    }

    #isOpen() {
        return !this.#list.classList.contains('hidden');
    }

    #open() {
        if (this.#options.length === 0) return;
        this.#list.classList.remove('hidden');
        this.#button.setAttribute('aria-expanded', 'true');
        const selected = this.#options.findIndex((o) => o.gb === this.#value);
        this.#setActive(selected >= 0 ? selected : 0);
        this.#list.focus();
    }

    #close() {
        if (!this.#isOpen()) return;
        this.#list.classList.add('hidden');
        this.#button.setAttribute('aria-expanded', 'false');
        this.#list.removeAttribute('aria-activedescendant');
    }

    /** @param {number} index */
    #choose(index) {
        const option = this.#options[index];
        if (!option || !option.available) return;
        this.#value = option.gb;
        this.#valueLabel.textContent = `${option.gb} GB`;
        this.#render();
        this.#close();
        this.#button.focus();
    }

    /** @param {number} index */
    #setActive(index) {
        this.#activeIndex = index;
        this.#list.querySelectorAll('[role="option"]').forEach((el, i) => {
            el.classList.toggle('bg-slate-700/60', i === index);
            if (i === index) {
                this.#list.setAttribute('aria-activedescendant', el.id);
                el.scrollIntoView({ block: 'nearest' });
            }
        });
    }

    /** @param {KeyboardEvent} e */
    #onListKeydown(e) {
        const last = this.#options.length - 1;
        switch (e.key) {
            case 'ArrowDown':
                e.preventDefault();
                this.#setActive(Math.min(last, this.#activeIndex + 1));
                break;
            case 'ArrowUp':
                e.preventDefault();
                this.#setActive(Math.max(0, this.#activeIndex - 1));
                break;
            case 'Home':
                e.preventDefault();
                this.#setActive(0);
                break;
            case 'End':
                e.preventDefault();
                this.#setActive(last);
                break;
            case 'Enter':
            case ' ':
                e.preventDefault();
                this.#choose(this.#activeIndex);
                break;
            case 'Escape':
                e.preventDefault();
                this.#close();
                this.#button.focus();
                break;
            case 'Tab':
                this.#close();
                break;
        }
    }

    #render() {
        this.#list.replaceChildren(...this.#options.map((option, index) => {
            const item = document.createElement('li');
            item.id = `${this.#list.id}-${option.gb}`;
            item.dataset.index = String(index);
            item.setAttribute('role', 'option');
            item.setAttribute('aria-selected', String(option.gb === this.#value));
            item.className = 'flex items-center justify-between gap-3 px-3 py-2 text-sm';

            const label = document.createElement('span');
            label.textContent = `${option.gb} GB`;
            item.append(label);

            if (option.available) {
                item.classList.add('cursor-pointer', 'hover:bg-slate-700/60');
                label.className = option.gb === this.#value ? 'text-sky-400 font-medium' : 'text-slate-200';
            } else {
                item.setAttribute('aria-disabled', 'true');
                item.classList.add('cursor-not-allowed');
                label.className = 'line-through text-slate-500';
                const reason = document.createElement('span');
                reason.className = 'text-xs text-red-400';
                reason.textContent = 'Not enough space';
                item.append(reason);
            }
            return item;
        }));
    }
}

class RecordingSettingsForm {
    #enabledEl
    #limit
    #usageEl
    #saveBtn
    #errorEl
    #successEl

    /**
     * @param {HTMLFormElement} formEl
     * @param {HTMLElement}     errorEl
     * @param {HTMLElement}     successEl
     */
    constructor(formEl, errorEl, successEl) {
        this.#enabledEl = /** @type {HTMLInputElement} */ (formEl.querySelector('#recordings-enabled'));
        this.#usageEl   = /** @type {HTMLElement} */ (formEl.querySelector('#recordings-usage'));
        this.#saveBtn   = /** @type {HTMLButtonElement} */ (formEl.querySelector('button[type="submit"]'));
        this.#limit     = new StorageLimitListbox(
            /** @type {HTMLButtonElement} */ (formEl.querySelector('#recordings-limit-btn')),
            /** @type {HTMLUListElement} */ (formEl.querySelector('#recordings-limit-list')),
        );
        this.#errorEl   = errorEl;
        this.#successEl = successEl;
        formEl.addEventListener('submit', (e) => {
            e.preventDefault();
            this.#save();
        });
    }

    async load() {
        try {
            const res = await authedFetch(RECORDING_SETTINGS_API);
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            this.#fill(await res.json());
        } catch (err) {
            if (err instanceof SessionExpiredError) return;
            this.#showError('Could not load recording settings.');
        }
    }

    /** @param {RecordingSettings} settings */
    #fill(settings) {
        this.#enabledEl.checked = settings.enabled;
        this.#limit.setOptions(settings.options, settings.max_storage_gb);
        this.#usageEl.textContent =
            `Recordings use ${formatStorageBytes(settings.used_bytes)} · ` +
            `${formatStorageBytes(settings.free_bytes)} free on disk`;
    }

    async #save() {
        this.#errorEl.classList.add('hidden');
        this.#successEl.classList.add('hidden');
        this.#saveBtn.disabled = true;
        try {
            const res = await authedFetch(RECORDING_SETTINGS_API, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    enabled: this.#enabledEl.checked,
                    max_storage_gb: this.#limit.value,
                }),
            });
            if (res.status === 400) {
                const body = await res.json().catch(() => ({}));
                this.#showError(typeof body.detail === 'string' ? body.detail : 'Invalid settings.');
                return;
            }
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            this.#fill(await res.json());
            this.#successEl.classList.remove('hidden');
        } catch (err) {
            if (err instanceof SessionExpiredError) return;
            this.#showError('Could not save recording settings.');
        } finally {
            this.#saveBtn.disabled = false;
        }
    }

    /** @param {string} message */
    #showError(message) {
        this.#errorEl.textContent = message;
        this.#errorEl.classList.remove('hidden');
    }
}

new RecordingSettingsForm(
    /** @type {HTMLFormElement} */ (document.getElementById('recording-settings-form')),
    document.getElementById('recording-settings-error'),
    document.getElementById('recording-settings-success'),
).load();
