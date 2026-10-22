/**
 * Admin page — global detection notification settings (all cameras).
 *
 *   GET /api/notifications/settings - current {enabled, cooldown_seconds}
 *   PUT /api/notifications/settings - save them (applies immediately)
 *
 * Per-camera overrides live in the camera settings modal (config.js).
 */

const NOTIFICATION_SETTINGS_API = '/api/notifications/settings';

class NotificationSettingsForm {
    #formEl
    #enabledEl
    #cooldownEl
    #saveBtn
    #errorEl
    #successEl

    /**
     * @param {HTMLFormElement} formEl
     * @param {HTMLElement}     errorEl
     * @param {HTMLElement}     successEl
     */
    constructor(formEl, errorEl, successEl) {
        this.#formEl     = formEl;
        this.#enabledEl  = /** @type {HTMLInputElement} */ (formEl.querySelector('#notifications-enabled'));
        this.#cooldownEl = /** @type {HTMLInputElement} */ (formEl.querySelector('#notifications-cooldown'));
        this.#saveBtn    = /** @type {HTMLButtonElement} */ (formEl.querySelector('button[type="submit"]'));
        this.#errorEl    = errorEl;
        this.#successEl  = successEl;
        this.#formEl.addEventListener('submit', (e) => {
            e.preventDefault();
            this.#save();
        });
    }

    async load() {
        try {
            const res = await authedFetch(NOTIFICATION_SETTINGS_API);
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            this.#fill(await res.json());
        } catch (err) {
            if (err instanceof SessionExpiredError) return;
            this.#showError('Could not load notification settings.');
        }
    }

    /** @param {{enabled: boolean, cooldown_seconds: number}} settings */
    #fill(settings) {
        this.#enabledEl.checked = settings.enabled;
        this.#cooldownEl.value  = String(settings.cooldown_seconds);
    }

    async #save() {
        this.#errorEl.classList.add('hidden');
        this.#successEl.classList.add('hidden');
        this.#saveBtn.disabled = true;
        try {
            const res = await authedFetch(NOTIFICATION_SETTINGS_API, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    enabled: this.#enabledEl.checked,
                    cooldown_seconds: Number(this.#cooldownEl.value),
                }),
            });
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            this.#fill(await res.json());
            this.#successEl.classList.remove('hidden');
        } catch (err) {
            if (err instanceof SessionExpiredError) return;
            this.#showError('Could not save notification settings.');
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

new NotificationSettingsForm(
    /** @type {HTMLFormElement} */ (document.getElementById('notification-settings-form')),
    document.getElementById('notification-settings-error'),
    document.getElementById('notification-settings-success'),
).load();
