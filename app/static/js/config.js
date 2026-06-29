/**
 * ConfigWidget — modal for viewing and editing camera configuration.
 *
 * Public API:
 *   widget.open()     — open modal, fetch current config via GET /api/config
 *   widget.close()    — close modal, restore focus to trigger element
 *   widget.discover() — scan LAN for ONVIF cameras via GET /api/setup/discover
 *   widget.save()     — persist form values via PUT /api/config
 */

const CONFIG_API   = '/api/config';
const DISCOVER_API = '/api/setup/discover';

class ConfigWidget {
    #modalEl
    #formEl
    #discoverSectionEl
    #discoverListEl
    #discoverBusyEl
    #errorEl
    #saveBtn
    #discoverBtn
    #previousFocusEl = null

    constructor(modalId) {
        this.#modalEl           = document.getElementById(modalId);
        this.#formEl            = this.#modalEl.querySelector('[data-config-form]');
        this.#discoverSectionEl = this.#modalEl.querySelector('[data-discover-section]');
        this.#discoverListEl    = this.#modalEl.querySelector('[data-discover-list]');
        this.#discoverBusyEl    = this.#modalEl.querySelector('[data-discover-busy]');
        this.#errorEl           = this.#modalEl.querySelector('[data-config-error]');
        this.#saveBtn           = this.#modalEl.querySelector('[data-save-btn]');
        this.#discoverBtn       = this.#modalEl.querySelector('[data-discover-btn]');

        this.#bindEvents();
    }

    open() {
        this.#previousFocusEl = document.activeElement;
        this.#clearError();
        this.#hideDiscovery();
        this.#modalEl.classList.remove('hidden');
        this.#modalEl.removeAttribute('aria-hidden');
        document.body.style.overflow = 'hidden';
        requestAnimationFrame(() => {
            this.#formEl.querySelector('input, select')?.focus();
        });
        this.#loadConfig();
    }

    close() {
        this.#modalEl.classList.add('hidden');
        this.#modalEl.setAttribute('aria-hidden', 'true');
        document.body.style.overflow = '';
        this.#previousFocusEl?.focus();
        this.#previousFocusEl = null;
    }

    async discover() {
        this.#discoverSectionEl.classList.remove('hidden');
        this.#discoverBusyEl.classList.remove('hidden');
        this.#discoverListEl.innerHTML = '';
        this.#discoverBtn.disabled = true;

        try {
            const res = await fetch(DISCOVER_API);
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const cameras = await res.json();
            this.#renderDiscoveryResults(cameras);
        } catch {
            this.#renderDiscoveryError();
        } finally {
            this.#discoverBusyEl.classList.add('hidden');
            this.#discoverBtn.disabled = false;
        }
    }

    async save() {
        this.#clearError();
        this.#saveBtn.disabled = true;

        try {
            const res = await fetch(CONFIG_API, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(this.#collectFormData()),
            });
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            this.close();
        } catch {
            this.#showError('Failed to save configuration. Please try again.');
        } finally {
            this.#saveBtn.disabled = false;
        }
    }

    // ── Private ──────────────────────────────────────────────

    #bindEvents() {
        this.#modalEl.querySelector('[data-close-btn]')
            ?.addEventListener('click', () => this.close());

        this.#modalEl.querySelector('[data-cancel-btn]')
            ?.addEventListener('click', () => this.close());

        this.#saveBtn?.addEventListener('click', () => this.save());
        this.#discoverBtn?.addEventListener('click', () => this.discover());

        // Close when clicking the dark backdrop (outside the dialog box)
        this.#modalEl.addEventListener('click', (e) => {
            if (e.target === this.#modalEl) this.close();
        });

        document.addEventListener('keydown', (e) => {
            if (this.#modalEl.classList.contains('hidden')) return;
            if (e.key === 'Escape') { this.close(); return; }
            this.#trapFocus(e);
        });
    }

    async #loadConfig() {
        try {
            const res = await fetch(CONFIG_API);
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const config = await res.json();
            this.#fillForm(config);
        } catch {
            this.#showError('Could not load configuration.');
        }
    }

    #fillForm(config) {
        const f = this.#formEl;
        f.querySelector('[name="camera_host"]').value       = config.camera_host       ?? '';
        f.querySelector('[name="camera_rtsp_port"]').value  = config.camera_rtsp_port  ?? '';
        f.querySelector('[name="camera_user"]').value       = config.camera_user       ?? '';
        f.querySelector('[name="camera_password"]').value   = '';
        f.querySelector('[name="camera_channel"]').value    = config.camera_channel    ?? '';
        f.querySelector('[name="camera_stream"]').value     = config.camera_stream     ?? 'main';
        f.querySelector('[name="camera_onvif_port"]').value = config.camera_onvif_port ?? '';
    }

    #collectFormData() {
        const f = this.#formEl;
        return {
            camera_host:       f.querySelector('[name="camera_host"]').value.trim(),
            camera_rtsp_port:  Number(f.querySelector('[name="camera_rtsp_port"]').value),
            camera_user:       f.querySelector('[name="camera_user"]').value.trim(),
            camera_password:   f.querySelector('[name="camera_password"]').value,
            camera_channel:    Number(f.querySelector('[name="camera_channel"]').value),
            camera_stream:     f.querySelector('[name="camera_stream"]').value,
            camera_onvif_port: Number(f.querySelector('[name="camera_onvif_port"]').value),
        };
    }

    #renderDiscoveryResults(cameras) {
        if (cameras.length === 0) {
            this.#discoverListEl.innerHTML =
                '<p class="text-slate-500 text-sm text-center py-2">No cameras found on the network.</p>';
            return;
        }

        this.#discoverListEl.innerHTML = cameras
            .map((cam) => `
                <div class="flex items-center justify-between gap-2 py-2
                            border-b border-slate-700 last:border-0">
                    <div class="min-w-0">
                        <p class="text-sm font-mono text-slate-200 truncate">${this.#esc(cam.host)}</p>
                        <p class="text-xs text-slate-500 truncate">${this.#esc(cam.name)}</p>
                    </div>
                    <button type="button"
                            class="shrink-0 text-xs px-2.5 py-1 rounded-md bg-sky-700
                                   hover:bg-sky-600 text-white transition-colors
                                   focus:outline-none focus:ring-2 focus:ring-sky-500"
                            data-use-host="${this.#esc(cam.host)}">
                        Use
                    </button>
                </div>
            `)
            .join('');

        this.#discoverListEl.querySelectorAll('[data-use-host]').forEach((btn) => {
            btn.addEventListener('click', () => {
                this.#formEl.querySelector('[name="camera_host"]').value = btn.dataset.useHost;
            });
        });
    }

    #renderDiscoveryError() {
        this.#discoverListEl.innerHTML =
            '<p class="text-red-400 text-sm text-center py-2">Discovery failed. Check network connectivity.</p>';
    }

    #hideDiscovery() {
        this.#discoverSectionEl.classList.add('hidden');
        this.#discoverListEl.innerHTML = '';
    }

    #showError(message) {
        this.#errorEl.textContent = message;
        this.#errorEl.classList.remove('hidden');
    }

    #clearError() {
        this.#errorEl.textContent = '';
        this.#errorEl.classList.add('hidden');
    }

    #trapFocus(e) {
        if (e.key !== 'Tab') return;

        const focusable = [...this.#modalEl.querySelectorAll(
            'button:not([disabled]), input:not([disabled]), select:not([disabled])'
        )].filter((el) => el.closest('.hidden') === null);

        const first = focusable[0];
        const last  = focusable[focusable.length - 1];

        if (e.shiftKey && document.activeElement === first) {
            e.preventDefault();
            last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
            e.preventDefault();
            first.focus();
        }
    }

    // Prevent XSS when interpolating camera names / hosts into innerHTML
    #esc(str) {
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }
}
