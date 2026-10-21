/**
 * Model catalog panel — lists the models published by minicam-models
 * (GET /api/detection/catalog) and installs one with a single click
 * (POST /api/detection/catalog/install), which downloads it, verifies its
 * sha256 server-side and makes it the active ONNX model.
 *
 * Catalog entries come from a remote manifest, so they are rendered with
 * textContent only — never as HTML.
 */

const DETECTION_CATALOG_API         = '/api/detection/catalog';
const DETECTION_CATALOG_INSTALL_API = '/api/detection/catalog/install';

/**
 * @typedef {object} CatalogAsset
 * @property {string}  name
 * @property {string}  family
 * @property {string}  precision
 * @property {string}  profile
 * @property {number}  bytes
 * @property {boolean} installed
 * @property {boolean} active
 */

class ModelCatalogPanel {
    #listEl
    #statusEl
    #errorEl
    #releaseEl
    #busy = false

    constructor() {
        this.#listEl    = document.getElementById('model-catalog-list');
        this.#statusEl  = document.getElementById('model-catalog-status');
        this.#errorEl   = document.getElementById('model-catalog-error');
        this.#releaseEl = document.getElementById('model-catalog-release');
    }

    async load() {
        this.#errorEl.classList.add('hidden');
        try {
            const res = await authedFetch(DETECTION_CATALOG_API);
            const body = await res.json().catch(() => ({}));
            if (!res.ok) {
                this.#statusEl.textContent = body.detail ?? `Catalog unavailable (${res.status}).`;
                this.#listEl.replaceChildren();
                return;
            }
            this.#releaseEl.textContent = body.release ? `Release ${body.release}` : '';
            this.#statusEl.textContent = body.assets.length ? '' : 'The catalog is empty.';
            this.#render(body.assets);
        } catch (err) {
            if (err instanceof SessionExpiredError) return;
            this.#statusEl.textContent = 'Catalog unavailable (network error).';
        }
    }

    /** @param {CatalogAsset[]} assets */
    #render(assets) {
        this.#listEl.replaceChildren(...assets.map((asset) => this.#row(asset)));
    }

    /** @param {CatalogAsset} asset */
    #row(asset) {
        const li = document.createElement('li');
        li.className = 'flex items-center justify-between gap-3 py-2';

        const info = document.createElement('div');
        info.className = 'flex flex-col min-w-0';
        const name = document.createElement('span');
        name.className = 'text-sm text-slate-100 font-mono truncate';
        name.textContent = asset.name;
        const meta = document.createElement('span');
        meta.className = 'text-xs text-slate-500';
        meta.textContent = `${asset.family} · ${asset.precision} · ${asset.profile} · `
                         + `${(asset.bytes / 1e6).toFixed(1)} MB`;
        info.append(name, meta);

        li.append(info, asset.active ? this.#activeBadge() : this.#installButton(asset));
        return li;
    }

    #activeBadge() {
        const badge = document.createElement('span');
        badge.className = 'text-xs font-semibold text-emerald-400 shrink-0';
        badge.textContent = 'Active';
        return badge;
    }

    /** @param {CatalogAsset} asset */
    #installButton(asset) {
        const label = asset.installed ? 'Activate' : 'Install & activate';
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'bg-slate-700 hover:bg-slate-600 text-slate-100 font-semibold text-xs '
                      + 'rounded-lg px-3 h-8 shrink-0 transition-colors '
                      + 'disabled:opacity-50 disabled:cursor-not-allowed';
        btn.textContent = label;
        btn.addEventListener('click', () => this.#install(asset.name, btn, label));
        return btn;
    }

    /**
     * @param {string} name
     * @param {HTMLButtonElement} btn
     * @param {string} label
     */
    async #install(name, btn, label) {
        if (this.#busy) return;
        this.#busy = true;
        this.#errorEl.classList.add('hidden');
        btn.disabled = true;
        btn.textContent = 'Installing…';
        try {
            const res = await authedFetch(DETECTION_CATALOG_INSTALL_API, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name, activate: true }),
            });
            if (!res.ok) {
                const body = await res.json().catch(() => ({}));
                this.#showError(body.detail ?? `Failed to install ${name} (${res.status}).`);
                return;
            }
            await Promise.all([this.load(), detectionSettingsPage.load()]);
        } catch (err) {
            if (!(err instanceof SessionExpiredError)) {
                this.#showError(`Failed to install ${name} (network error).`);
            }
        } finally {
            this.#busy = false;
            if (btn.isConnected) {
                btn.disabled = false;
                btn.textContent = label;
            }
        }
    }

    /** @param {string} message */
    #showError(message) {
        this.#errorEl.textContent = message;
        this.#errorEl.classList.remove('hidden');
    }
}

new ModelCatalogPanel().load();
