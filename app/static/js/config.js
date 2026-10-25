/**
 * ConfigWidget — modal for managing multiple camera configurations.
 *
 * Renders all modal content dynamically inside #config-modal-inner.
 * Two views:
 *   - List view  (default): shows all cameras with Edit / Delete actions.
 *   - Form view  (add/edit): fields for all camera properties + ONVIF discovery.
 *
 * Public API:
 *   widget.open()   — open modal and show the list view.
 *   widget.close()  — close modal and restore focus to the trigger element.
 */
/* exported ConfigWidget */

const CAMERAS_API           = '/api/cameras';
const DISCOVER_API          = '/api/setup/discover';
const DETECTION_CLASSES_API = '/api/detection/classes';

class ConfigWidget {
    #modalEl
    #innerEl
    #onCamerasChanged
    #onCameraUpdated
    #onCameraSelected
    #getActiveCamera
    #previousFocusEl = null
    #editingCamera   = null  // null = creating new; object = editing existing

    /**
     * @param {string}   modalId
     * @param {object}   [opts]
     * @param {Function} [opts.onCamerasChanged]  - Called after any add / edit / delete.
     * @param {Function} [opts.onCameraUpdated]   - Called after a successful edit with
     *                                            (oldName, newName). Use to reconnect
     *                                            streams that target the edited camera.
     * @param {Function} [opts.onCameraSelected]  - Called with (name) when the user picks
     *                                            a camera from the list. Widget closes
     *                                            itself right after.
     * @param {Function} [opts.getActiveCamera]   - Returns the name of the currently active
     *                                            camera, used to highlight it in the list.
     */
    constructor(modalId, { onCamerasChanged, onCameraUpdated, onCameraSelected, getActiveCamera } = {}) {
        this.#modalEl           = document.getElementById(modalId);
        this.#innerEl           = document.getElementById('config-modal-inner');
        this.#onCamerasChanged  = onCamerasChanged  ?? (() => Promise.resolve());
        this.#onCameraUpdated   = onCameraUpdated   ?? (() => {});
        this.#onCameraSelected  = onCameraSelected  ?? (() => {});
        this.#getActiveCamera   = getActiveCamera   ?? (() => null);
        this.#bindGlobalEvents();
    }

    open() {
        this.#previousFocusEl = document.activeElement;
        this.#modalEl.classList.remove('hidden');
        this.#modalEl.removeAttribute('aria-hidden');
        document.body.style.overflow = 'hidden';
        this.#showListView();
    }

    close() {
        this.#modalEl.classList.add('hidden');
        this.#modalEl.setAttribute('aria-hidden', 'true');
        document.body.style.overflow = '';
        this.#previousFocusEl?.focus();
        this.#previousFocusEl = null;
    }

    // ── Global event bindings (survive view switches) ────────

    #bindGlobalEvents() {
        this.#modalEl.addEventListener('click', (e) => {
            if (e.target === this.#modalEl) this.close();
        });

        document.addEventListener('keydown', (e) => {
            if (this.#modalEl.classList.contains('hidden')) return;
            if (e.key === 'Escape') { this.close(); return; }
            this.#trapFocus(e);
        });
    }

    // ── List view ────────────────────────────────────────────

    async #showListView() {
        this.#innerEl.innerHTML = this.#buildListShell();
        this.#innerEl.querySelector('[data-close-btn]')
            ?.addEventListener('click', () => this.close());
        this.#innerEl.querySelector('[data-add-btn]')
            ?.addEventListener('click', () => this.#showFormView(null));

        requestAnimationFrame(() => {
            this.#innerEl.querySelector('button')?.focus();
        });

        await this.#loadCameraList();
    }

    #buildListShell() {
        return `
            <div class="flex items-center justify-between px-5 py-4 border-b border-slate-700 shrink-0">
                <h2 id="config-modal-title"
                    class="text-sm font-semibold text-slate-100 tracking-tight">
                    Camera Settings
                </h2>
                ${this.#closeButtonHtml()}
            </div>
            <div class="overflow-y-auto flex-1 min-h-0 px-5 py-4">
                <p data-list-error role="alert"
                   class="hidden text-xs text-red-400 bg-red-950/40 border border-red-900/60
                          rounded-md px-3 py-2 mb-4"></p>
                <div data-camera-list aria-live="polite" class="space-y-2">
                    ${this.#spinnerHtml('Loading cameras…')}
                </div>
            </div>
            <div class="flex items-center justify-end gap-2 px-5 py-4
                        border-t border-slate-700 shrink-0">
                <button type="button" data-add-btn
                        class="text-xs px-4 py-2 rounded-lg bg-sky-600 hover:bg-sky-500
                               text-white font-medium transition-colors
                               focus:outline-none focus:ring-2 focus:ring-sky-500
                               focus:ring-offset-2 focus:ring-offset-slate-800">
                    Add camera
                </button>
            </div>
        `;
    }

    async #loadCameraList() {
        const listEl  = this.#innerEl.querySelector('[data-camera-list]');
        const errorEl = this.#innerEl.querySelector('[data-list-error]');

        try {
            const res = await authedFetch(CAMERAS_API);
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const cameras = await res.json();
            this.#renderCameraList(cameras, listEl);
        } catch {
            errorEl?.classList.remove('hidden');
            if (errorEl) errorEl.textContent = 'Could not load cameras.';
            if (listEl) listEl.innerHTML = '';
        }
    }

    #renderCameraList(cameras, listEl) {
        if (!listEl) return;

        if (cameras.length === 0) {
            listEl.innerHTML =
                '<p class="text-slate-500 text-sm text-center py-4">No cameras configured yet.</p>';
            return;
        }

        const activeName = this.#getActiveCamera();

        listEl.innerHTML = cameras.map((cam) => {
            const isActive = cam.name === activeName;
            return `
            <div class="flex items-center justify-between gap-2 py-2.5 px-3
                        rounded-lg border cursor-pointer transition-colors
                        focus:outline-none focus:ring-2 focus:ring-sky-500
                        ${isActive
                            ? 'bg-sky-700 border-sky-600 text-white'
                            : 'bg-slate-900/50 border-slate-700 hover:bg-slate-800/70 hover:border-slate-600'}"
                 data-select-cam="${this.#esc(cam.name)}"
                 role="button"
                 tabindex="0"
                 aria-pressed="${isActive}"
                 aria-label="Select camera ${this.#esc(cam.name)}${isActive ? ', currently active' : ''}">
                <div class="min-w-0">
                    <p class="text-sm font-medium truncate ${isActive ? 'text-white' : 'text-slate-200'}">
                        ${this.#esc(cam.name)}
                    </p>
                    <p class="text-xs font-mono truncate ${isActive ? 'text-sky-100/80' : 'text-slate-500'}">
                        ${this.#esc(cam.host)}
                    </p>
                </div>
                <div class="flex items-center gap-2 shrink-0">
                    <button type="button"
                            data-edit-cam="${this.#esc(cam.name)}"
                            class="text-xs px-2.5 py-1 rounded-md border border-slate-600
                                   text-slate-300 hover:text-slate-100 hover:border-slate-500
                                   transition-colors focus:outline-none focus:ring-2
                                   focus:ring-sky-500">
                        Edit
                    </button>
                    <button type="button"
                            data-delete-cam="${this.#esc(cam.name)}"
                            class="text-xs px-2.5 py-1 rounded-md border border-red-900/60
                                   text-red-400 hover:text-red-300 hover:border-red-800
                                   transition-colors focus:outline-none focus:ring-2
                                   focus:ring-red-500">
                        Delete
                    </button>
                </div>
            </div>
        `;
        }).join('');

        listEl.querySelectorAll('[data-select-cam]').forEach((el) => {
            el.addEventListener('click', () => {
                this.#onCameraSelected(el.dataset.selectCam);
                this.close();
            });
            el.addEventListener('keydown', (e) => {
                if (e.target !== el) return;
                if (e.key !== 'Enter' && e.key !== ' ') return;
                e.preventDefault();
                this.#onCameraSelected(el.dataset.selectCam);
                this.close();
            });
        });

        listEl.querySelectorAll('[data-edit-cam]').forEach((btn) => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                const cam = cameras.find((c) => c.name === btn.dataset.editCam);
                if (cam) this.#showFormView(cam);
            });
        });

        listEl.querySelectorAll('[data-delete-cam]').forEach((btn) => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                this.#deleteCamera(btn.dataset.deleteCam);
            });
        });
    }

    async #deleteCamera(name) {
        const confirmed = await confirmDialog({
            title: 'Delete camera',
            message: `Delete camera "${name}"?`,
            confirmLabel: 'Delete',
            danger: true,
        });
        if (!confirmed) return;

        const errorEl = this.#innerEl.querySelector('[data-list-error]');

        try {
            const res = await authedFetch(`${CAMERAS_API}/${encodeURIComponent(name)}`, {
                method: 'DELETE',
            });
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            await this.#onCamerasChanged();
            await this.#loadCameraList();
        } catch {
            if (errorEl) {
                errorEl.textContent = `Failed to delete camera "${name}".`;
                errorEl.classList.remove('hidden');
            }
        }
    }

    // ── Form view ────────────────────────────────────────────

    async #showFormView(camera) {
        this.#editingCamera = camera;
        this.#innerEl.innerHTML = this.#buildFormShell(camera);

        this.#innerEl.querySelector('[data-close-btn]')
            ?.addEventListener('click', () => this.close());
        this.#innerEl.querySelector('[data-back-btn]')
            ?.addEventListener('click', () => this.#showListView());
        this.#innerEl.querySelector('[data-save-btn]')
            ?.addEventListener('click', () => this.#saveForm());
        this.#innerEl.querySelector('[data-discover-btn]')
            ?.addEventListener('click', () => this.#runDiscovery());

        if (camera) this.#fillForm(camera);

        requestAnimationFrame(() => {
            this.#innerEl.querySelector('input, select')?.focus();
        });

        await this.#loadDetectionClasses(camera);
    }

    #buildFormShell(camera) {
        const isNew = camera === null;
        const title = isNew ? 'Add Camera' : 'Edit Camera';

        return `
            <div class="flex items-center justify-between px-5 py-4 border-b border-slate-700 shrink-0">
                <h2 id="config-modal-title"
                    class="text-sm font-semibold text-slate-100 tracking-tight">
                    ${title}
                </h2>
                ${this.#closeButtonHtml()}
            </div>
            <div class="overflow-y-auto flex-1 min-h-0 px-5 py-4">
                <p data-form-error role="alert"
                   class="hidden text-xs text-red-400 bg-red-950/40 border border-red-900/60
                          rounded-md px-3 py-2 mb-4"></p>
                <form data-config-form novalidate class="space-y-4">

                    <div class="flex flex-col gap-1.5">
                        <label for="cfg-name"
                               class="text-xs text-slate-400 font-medium">Name</label>
                        <input id="cfg-name" name="name" type="text"
                               class="input-cfg"
                               placeholder="my-camera"
                               autocomplete="off"
                               spellcheck="false">
                        ${!isNew ? `<p class="text-xs text-slate-600 leading-snug">
                            Rename requires all connected streams to switch to the new name.
                        </p>` : ''}
                    </div>

                    <div class="flex flex-col gap-1.5">
                        <label for="cfg-host"
                               class="text-xs text-slate-400 font-medium">Host / IP</label>
                        <div class="flex gap-2">
                            <input id="cfg-host" name="host" type="text"
                                   class="input-cfg flex-1 min-w-0"
                                   placeholder="192.168.1.x"
                                   autocomplete="off"
                                   spellcheck="false">
                            <button type="button" data-discover-btn
                                    class="shrink-0 text-xs px-3 py-1.5 rounded-lg
                                           border border-slate-600 text-slate-300
                                           hover:text-slate-100 hover:border-slate-500
                                           transition-colors focus:outline-none focus:ring-2
                                           focus:ring-sky-500 disabled:opacity-50
                                           disabled:cursor-not-allowed">
                                Search
                            </button>
                        </div>
                    </div>

                    <div data-discover-section
                         class="hidden rounded-lg bg-slate-900/60 border border-slate-700 px-3 py-2">
                        <p class="text-xs text-slate-500 mb-2 font-medium uppercase tracking-wide">
                            Found on network
                        </p>
                        <div data-discover-busy class="hidden">
                            <div class="flex items-center gap-2 py-1 text-xs text-slate-400">
                                <span class="w-3.5 h-3.5 border-2 border-slate-600 border-t-sky-400
                                             rounded-full animate-spin shrink-0 block"></span>
                                <span>Searching for cameras on the network…</span>
                            </div>
                        </div>
                        <div data-discover-list aria-live="polite"></div>
                    </div>

                    <div class="flex flex-col gap-1.5">
                        <label for="cfg-rtsp-port"
                               class="text-xs text-slate-400 font-medium">RTSP Port</label>
                        <input id="cfg-rtsp-port" name="rtsp_port" type="number"
                               class="input-cfg" min="1" max="65535" placeholder="554">
                    </div>

                    <div class="flex flex-col gap-1.5">
                        <label for="cfg-onvif-port"
                               class="text-xs text-slate-400 font-medium">ONVIF Port</label>
                        <input id="cfg-onvif-port" name="onvif_port" type="number"
                               class="input-cfg" min="1" max="65535" placeholder="8000">
                    </div>

                    <div class="flex flex-col gap-1.5">
                        <label for="cfg-user"
                               class="text-xs text-slate-400 font-medium">Username</label>
                        <input id="cfg-user" name="user" type="text"
                               class="input-cfg" autocomplete="username">
                    </div>

                    <div class="flex flex-col gap-1.5">
                        <label for="cfg-password"
                               class="text-xs text-slate-400 font-medium">Password</label>
                        <input id="cfg-password" name="password" type="password"
                               class="input-cfg" autocomplete="current-password"
                               placeholder="Leave blank to keep current">
                        <p class="text-xs text-slate-600 leading-snug">
                            Leave blank to keep the existing password.
                        </p>
                    </div>

                    <div class="flex flex-col gap-1.5">
                        <label for="cfg-channel"
                               class="text-xs text-slate-400 font-medium">Channel</label>
                        <input id="cfg-channel" name="channel" type="number"
                               class="input-cfg" min="1" placeholder="1">
                    </div>

                    <div class="flex flex-col gap-1.5">
                        <span class="text-xs text-slate-400 font-medium">Object detection</span>
                        <div data-detection-classes aria-live="polite">
                            ${this.#spinnerHtml('Loading detection classes…')}
                        </div>
                    </div>

                    <div class="flex flex-col gap-1.5">
                        <label for="cfg-notifications"
                               class="text-xs text-slate-400 font-medium">Detection notifications</label>
                        <select id="cfg-notifications" name="notifications" class="input-cfg">
                            <option value="inherit">Use global setting (Admin)</option>
                            <option value="on">On for this camera</option>
                            <option value="off">Off for this camera</option>
                        </select>
                        <p class="text-xs text-slate-500">
                            Notifies when any of the detection classes above is seen.
                        </p>
                    </div>

                </form>
            </div>
            <div class="flex items-center justify-between gap-2 px-5 py-4
                        border-t border-slate-700 shrink-0">
                <button type="button" data-back-btn
                        class="text-xs px-4 py-2 rounded-lg text-slate-400
                               hover:text-slate-100 transition-colors
                               focus:outline-none focus:ring-2 focus:ring-sky-500">
                    &larr; Back
                </button>
                <button type="button" data-save-btn
                        class="text-xs px-4 py-2 rounded-lg bg-sky-600 hover:bg-sky-500
                               text-white font-medium transition-colors
                               focus:outline-none focus:ring-2 focus:ring-sky-500
                               focus:ring-offset-2 focus:ring-offset-slate-800
                               disabled:opacity-50 disabled:cursor-not-allowed">
                    Save
                </button>
            </div>
        `;
    }

    #fillForm(camera) {
        const f = this.#innerEl.querySelector('[data-config-form]');
        if (!f) return;
        f.querySelector('[name="name"]').value       = camera.name       ?? '';
        f.querySelector('[name="host"]').value       = camera.host       ?? '';
        f.querySelector('[name="rtsp_port"]').value  = camera.rtsp_port  ?? '';
        f.querySelector('[name="onvif_port"]').value = camera.onvif_port ?? '';
        f.querySelector('[name="user"]').value       = camera.user       ?? '';
        f.querySelector('[name="channel"]').value    = camera.channel    ?? '';
        f.querySelector('[name="notifications"]').value = camera.notifications ?? 'inherit';
        // password is intentionally left blank — backend keeps existing value when empty
    }

    async #loadDetectionClasses(camera) {
        const container = this.#innerEl.querySelector('[data-detection-classes]');
        if (!container) return;

        try {
            const res = await authedFetch(DETECTION_CLASSES_API);
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const { classes } = await res.json();
            this.#renderDetectionClasses(classes, camera, container);
        } catch {
            container.innerHTML =
                '<p class="text-xs text-red-400">Could not load detection classes.</p>';
        }
    }

    #renderDetectionClasses(classes, camera, container) {
        if (classes.length === 0) {
            container.innerHTML =
                '<p class="text-xs text-slate-500">No detection backend configured on this device.</p>';
            return;
        }

        const selected = new Set(camera?.detection_classes ?? []);

        container.innerHTML = `
            <div class="max-h-40 overflow-y-auto flex flex-col gap-1.5 rounded-lg
                        border border-slate-700 bg-slate-900/40 px-3 py-2">
                ${classes.map((cls, i) => `
                    <label for="cfg-detect-${i}"
                           class="flex items-center gap-2 text-sm text-slate-300">
                        <input id="cfg-detect-${i}" type="checkbox"
                               class="accent-sky-500"
                               data-detection-class-checkbox
                               value="${this.#esc(cls)}"
                               ${selected.has(cls) ? 'checked' : ''}>
                        ${this.#esc(cls)}
                    </label>
                `).join('')}
            </div>
        `;
    }

    #collectFormData() {
        const f                = this.#innerEl.querySelector('[data-config-form]');
        const nameEl           = f.querySelector('[name="name"]');
        const detectionClasses = [...f.querySelectorAll('[data-detection-class-checkbox]:checked')]
            .map((el) => el.value);
        return {
            ...(nameEl ? { name: nameEl.value.trim() } : {}),
            host:       f.querySelector('[name="host"]').value.trim(),
            rtsp_port:  Number(f.querySelector('[name="rtsp_port"]').value),
            onvif_port: Number(f.querySelector('[name="onvif_port"]').value),
            user:       f.querySelector('[name="user"]').value.trim(),
            password:   f.querySelector('[name="password"]').value,
            channel:    Number(f.querySelector('[name="channel"]').value),
            detection_classes: detectionClasses,
            notifications: f.querySelector('[name="notifications"]').value,
        };
    }

    async #saveForm() {
        const saveBtn = this.#innerEl.querySelector('[data-save-btn]');
        const errorEl = this.#innerEl.querySelector('[data-form-error]');
        errorEl?.classList.add('hidden');
        if (saveBtn) {
            saveBtn.disabled = true;
            saveBtn.classList.add('inline-flex', 'items-center', 'gap-1.5');
            saveBtn.innerHTML = `
                <span class="w-3 h-3 border-2 border-white/30 border-t-white
                             rounded-full animate-spin block shrink-0"></span>
                <span>Saving…</span>
            `;
        }

        const isNew   = this.#editingCamera === null;
        const oldName = this.#editingCamera?.name ?? null;
        const data    = this.#collectFormData();

        try {
            let res;
            if (isNew) {
                res = await authedFetch(CAMERAS_API, {
                    method:  'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body:    JSON.stringify(data),
                });
            } else {
                res = await authedFetch(
                    `${CAMERAS_API}/${encodeURIComponent(oldName)}`,
                    {
                        method:  'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body:    JSON.stringify(data),
                    }
                );
            }

            if (!res.ok) {
                const body = await res.json().catch(() => null);
                throw new Error(body?.detail ?? '');
            }
            await this.#onCamerasChanged();
            if (!isNew) this.#onCameraUpdated(oldName, data.name);
            this.#showListView();
        } catch (err) {
            if (errorEl) {
                errorEl.textContent = err.message || 'Failed to save. Please try again.';
                errorEl.classList.remove('hidden');
            }
        } finally {
            if (saveBtn) {
                saveBtn.disabled = false;
                saveBtn.classList.remove('inline-flex', 'items-center', 'gap-1.5');
                saveBtn.innerHTML = 'Save';
            }
        }
    }

    // ── ONVIF discovery ──────────────────────────────────────

    async #runDiscovery() {
        const section    = this.#innerEl.querySelector('[data-discover-section]');
        const busyEl     = this.#innerEl.querySelector('[data-discover-busy]');
        const listEl     = this.#innerEl.querySelector('[data-discover-list]');
        const discoverBtn = this.#innerEl.querySelector('[data-discover-btn]');

        if (!section) return;

        section.classList.remove('hidden');
        busyEl?.classList.remove('hidden');
        if (listEl) listEl.innerHTML = '';
        if (discoverBtn) discoverBtn.disabled = true;

        try {
            const res = await authedFetch(DISCOVER_API);
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const cameras = await res.json();
            this.#renderDiscoveryResults(cameras, listEl);
        } catch {
            if (listEl) {
                listEl.innerHTML =
                    '<p class="text-red-400 text-sm text-center py-2">Discovery failed. Check network connectivity.</p>';
            }
        } finally {
            busyEl?.classList.add('hidden');
            if (discoverBtn) discoverBtn.disabled = false;
        }
    }

    #renderDiscoveryResults(cameras, listEl) {
        if (!listEl) return;

        if (cameras.length === 0) {
            listEl.innerHTML =
                '<p class="text-slate-500 text-sm text-center py-2">No cameras found on the network.</p>';
            return;
        }

        listEl.innerHTML = cameras.map((cam) => `
            <div class="flex items-center justify-between gap-2 py-2
                        border-b border-slate-700 last:border-0">
                <div class="min-w-0">
                    <p class="text-sm font-mono text-slate-200 truncate">
                        ${this.#esc(cam.host)}
                    </p>
                    <p class="text-xs text-slate-500 truncate">
                        ${this.#esc(cam.name)}
                    </p>
                </div>
                <button type="button"
                        class="shrink-0 text-xs px-2.5 py-1 rounded-md bg-sky-700
                               hover:bg-sky-600 text-white transition-colors
                               focus:outline-none focus:ring-2 focus:ring-sky-500"
                        data-use-host="${this.#esc(cam.host)}">
                    Use
                </button>
            </div>
        `).join('');

        listEl.querySelectorAll('[data-use-host]').forEach((btn) => {
            btn.addEventListener('click', () => {
                const hostInput = this.#innerEl.querySelector('[name="host"]');
                if (hostInput) hostInput.value = btn.dataset.useHost;
            });
        });
    }

    // ── Accessibility ────────────────────────────────────────

    #trapFocus(e) {
        if (e.key !== 'Tab') return;

        const focusable = [...this.#innerEl.querySelectorAll(
            'button:not([disabled]), input:not([disabled]), select:not([disabled]), [data-select-cam]'
        )].filter((el) => el.closest('.hidden') === null);

        if (focusable.length === 0) return;

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

    // ── Helpers ──────────────────────────────────────────────

    #closeButtonHtml() {
        return `
            <button type="button" data-close-btn aria-label="Close settings"
                    class="flex items-center justify-center w-7 h-7 rounded-md
                           text-slate-400 hover:text-slate-100 hover:bg-slate-700
                           transition-colors focus:outline-none focus:ring-2
                           focus:ring-sky-500">
                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"
                     stroke="currentColor" stroke-width="2" stroke-linecap="round"
                     stroke-linejoin="round" class="w-4 h-4 pointer-events-none">
                    <line x1="18" y1="6" x2="6" y2="18"/>
                    <line x1="6" y1="6" x2="18" y2="18"/>
                </svg>
            </button>
        `;
    }

    #spinnerHtml(label) {
        return `
            <div class="flex items-center justify-center gap-2 py-4 text-slate-500 text-sm">
                <span class="w-4 h-4 border-2 border-slate-600 border-t-sky-400
                             rounded-full animate-spin block shrink-0"></span>
                ${this.#esc(label)}
            </div>
        `;
    }

    /** Prevent XSS when interpolating user-controlled strings into innerHTML. */
    #esc(str) {
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }
}
