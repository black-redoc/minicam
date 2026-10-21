/**
 * AI Detection page — configure the YOLO detection backend, upload an
 * ONNX model, and tune confidence/FPS.
 *
 * Uploading a model and activating it are two separate steps: the upload
 * endpoint only writes the file to disk and returns its server-side path;
 * the caller must copy that path into the form and click Save to actually
 * persist it (PUT settings) as the active backend config.
 */

const DETECTION_SETTINGS_API    = '/api/detection/settings';
const DETECTION_MODEL_API       = '/api/detection/model';
const DETECTION_MODEL_FETCH_API = '/api/detection/model/fetch';

class DetectionSettingsPage {
    #formEl
    #backendEl
    #modelPathEl
    #fileEl
    #uploadBtn
    #urlEl
    #fetchBtn
    #confidenceEl
    #fpsEl
    #saveBtn
    #errorEl
    #successEl

    /**
     * @param {HTMLFormElement} formEl
     */
    constructor(formEl) {
        this.#formEl      = formEl;
        this.#backendEl   = /** @type {HTMLSelectElement} */ (formEl.querySelector('#detection-backend'));
        this.#modelPathEl = /** @type {HTMLInputElement} */ (formEl.querySelector('#detection-model-path'));
        this.#fileEl      = /** @type {HTMLInputElement} */ (formEl.querySelector('#detection-model-file'));
        this.#uploadBtn   = /** @type {HTMLButtonElement} */ (formEl.querySelector('#detection-upload-btn'));
        this.#urlEl       = /** @type {HTMLInputElement} */ (formEl.querySelector('#detection-model-url'));
        this.#fetchBtn    = /** @type {HTMLButtonElement} */ (formEl.querySelector('#detection-fetch-btn'));
        this.#confidenceEl = /** @type {HTMLInputElement} */ (formEl.querySelector('#detection-confidence'));
        this.#fpsEl       = /** @type {HTMLInputElement} */ (formEl.querySelector('#detection-fps'));
        this.#saveBtn     = /** @type {HTMLButtonElement} */ (formEl.querySelector('#detection-save-btn'));
        this.#errorEl     = document.getElementById('detection-settings-error');
        this.#successEl   = document.getElementById('detection-settings-success');

        this.#uploadBtn.addEventListener('click', () => this.#uploadModel());
        this.#fetchBtn.addEventListener('click', () => this.#fetchModel());
        this.#formEl.addEventListener('submit', (event) => {
            event.preventDefault();
            this.#save();
        });
    }

    async load() {
        const res = await authedFetch(DETECTION_SETTINGS_API);
        if (!res.ok) throw new Error(`${DETECTION_SETTINGS_API} failed: ${res.status}`);
        this.#populate(await res.json());
    }

    /**
     * @param {{backend: string, model_path: string, confidence_threshold: number, inference_fps: number}} config
     */
    #populate(config) {
        this.#backendEl.value    = config.backend;
        this.#modelPathEl.value  = config.model_path;
        this.#confidenceEl.value = String(config.confidence_threshold);
        this.#fpsEl.value        = String(config.inference_fps);
    }

    #clearMessages() {
        this.#errorEl.classList.add('hidden');
        this.#successEl.classList.add('hidden');
    }

    #showError(message) {
        this.#errorEl.textContent = message;
        this.#errorEl.classList.remove('hidden');
    }

    #showSuccess(message) {
        this.#successEl.textContent = message;
        this.#successEl.classList.remove('hidden');
    }

    async #uploadModel() {
        this.#clearMessages();
        const file = this.#fileEl.files?.[0];
        if (!file) {
            this.#showError('Choose a .onnx file first.');
            return;
        }

        this.#uploadBtn.disabled = true;
        this.#uploadBtn.textContent = 'Uploading…';
        try {
            const formData = new FormData();
            formData.append('file', file);
            // No Content-Type header here on purpose: the browser sets the
            // multipart boundary itself, and authedFetch passes options
            // straight through to fetch() without touching headers.
            const res = await authedFetch(DETECTION_MODEL_API, {
                method: 'POST',
                body: formData,
            });
            if (!res.ok) {
                const body = await res.json().catch(() => ({}));
                this.#showError(body.detail ?? `Failed to upload model (${res.status}).`);
                return;
            }
            const body = await res.json();
            this.#modelPathEl.value = body.model_path;
            this.#showSuccess('Model uploaded. Click Save to activate it.');
        } finally {
            this.#uploadBtn.disabled = false;
            this.#uploadBtn.textContent = 'Upload model';
        }
    }

    async #fetchModel() {
        this.#clearMessages();
        const url = this.#urlEl.value.trim();
        if (!url) {
            this.#showError('Enter a model URL first.');
            return;
        }

        this.#fetchBtn.disabled = true;
        this.#fetchBtn.textContent = 'Fetching…';
        try {
            const res = await authedFetch(DETECTION_MODEL_FETCH_API, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ url }),
            });
            if (!res.ok) {
                const body = await res.json().catch(() => ({}));
                this.#showError(body.detail ?? `Failed to fetch model (${res.status}).`);
                return;
            }
            const body = await res.json();
            this.#modelPathEl.value = body.model_path;
            this.#showSuccess('Model downloaded. Click Save to activate it.');
        } finally {
            this.#fetchBtn.disabled = false;
            this.#fetchBtn.textContent = 'Fetch from URL';
        }
    }

    async #save() {
        this.#clearMessages();
        this.#saveBtn.disabled = true;
        this.#saveBtn.textContent = 'Saving…';
        try {
            const res = await authedFetch(DETECTION_SETTINGS_API, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    backend: this.#backendEl.value,
                    model_path: this.#modelPathEl.value.trim(),
                    confidence_threshold: Number(this.#confidenceEl.value),
                    inference_fps: Number(this.#fpsEl.value),
                }),
            });
            if (!res.ok) {
                const body = await res.json().catch(() => ({}));
                this.#showError(body.detail ?? `Failed to save settings (${res.status}).`);
                return;
            }
            this.#populate(await res.json());
            this.#showSuccess('Settings saved.');
        } finally {
            this.#saveBtn.disabled = false;
            this.#saveBtn.textContent = 'Save';
        }
    }
}

const detectionSettingsPage = new DetectionSettingsPage(
    /** @type {HTMLFormElement} */ (document.getElementById('detection-settings-form')),
);
detectionSettingsPage.load();
