/**
 * Detection notifications on the dashboard.
 *
 *   - Live: an EventSource on /api/notifications/events shows a toast for
 *     every detection on a camera this user can view, while the page is open.
 *   - Web Push: the "Notifications on this device" menu item subscribes this
 *     browser, so the service worker shows a system notification even with
 *     the tab closed. Browsers only allow it on HTTPS (or localhost), so on
 *     plain-HTTP LAN access the item is disabled and only toasts work.
 *
 * Which cameras notify at all is configured by an admin (Admin page for all
 * cameras, camera settings for a single one).
 */
/* exported DetectionNotifications */

const NOTIFICATION_EVENTS_API = '/api/notifications/events';
const PUSH_PUBLIC_KEY_API     = '/api/notifications/push/public-key';
const PUSH_SUBSCRIPTIONS_API  = '/api/notifications/push/subscriptions';
const SERVICE_WORKER_URL      = '/service-worker.js';
const TOAST_DURATION_MS       = 8000;
const MAX_TOASTS              = 4;

class DetectionNotifications {
    #toastsEl
    #pushBtn
    #pushLabelEl
    #source = null

    /**
     * @param {HTMLElement}            toastsEl - Container toasts render into.
     * @param {HTMLButtonElement|null} pushBtn  - Menu item toggling Web Push.
     */
    constructor(toastsEl, pushBtn) {
        this.#toastsEl    = toastsEl;
        this.#pushBtn     = pushBtn;
        this.#pushLabelEl = pushBtn?.querySelector('[data-push-label]') ?? null;
    }

    start() {
        this.#connect();
        this.#initPushToggle();
    }

    #connect() {
        // EventSource reconnects on its own (the server sends `retry:` and
        // closes the stream periodically to re-check the session).
        this.#source = new EventSource(NOTIFICATION_EVENTS_API);
        this.#source.addEventListener('detection', (event) => {
            try {
                this.#showToast(JSON.parse(/** @type {MessageEvent} */ (event).data));
            } catch {
                // Ignore a malformed event rather than break the stream.
            }
        });
    }

    /** @param {{camera: string, body: string, detected_at: string}} payload */
    #showToast(payload) {
        while (this.#toastsEl.children.length >= MAX_TOASTS) {
            this.#toastsEl.firstElementChild?.remove();
        }
        const toast = document.createElement('div');
        toast.setAttribute('role', 'status');
        toast.className = 'pointer-events-auto flex items-start gap-3 w-80 max-w-full px-4 py-3 '
            + 'rounded-xl border border-amber-500/40 bg-slate-800/95 shadow-2xl backdrop-blur';
        const time = new Date(payload.detected_at).toLocaleTimeString();
        toast.innerHTML = `
            <span class="mt-1.5 w-2 h-2 rounded-full bg-amber-400 shrink-0"></span>
            <div class="flex-1 min-w-0">
                <p class="text-sm text-slate-100" data-toast-body></p>
                <p class="text-xs text-slate-500 font-mono tabular-nums" data-toast-time></p>
            </div>
            <button type="button" aria-label="Dismiss" data-toast-close
                    class="text-slate-500 hover:text-slate-200 text-sm leading-none">&times;</button>
        `;
        // textContent, not innerHTML: camera names come from config.
        toast.querySelector('[data-toast-body]').textContent = payload.body;
        toast.querySelector('[data-toast-time]').textContent = time;
        toast.querySelector('[data-toast-close]').addEventListener('click', () => toast.remove());
        this.#toastsEl.appendChild(toast);
        setTimeout(() => toast.remove(), TOAST_DURATION_MS);
    }

    static #pushSupported() {
        return window.isSecureContext
            && 'serviceWorker' in navigator
            && 'PushManager' in window
            && 'Notification' in window;
    }

    async #initPushToggle() {
        if (!this.#pushBtn) return;
        if (!DetectionNotifications.#pushSupported()) {
            this.#pushBtn.disabled = true;
            this.#pushBtn.title = 'Push notifications need HTTPS (e.g. via the Cloudflare Tunnel)';
            this.#setLabel('Notifications unavailable (HTTPS only)');
            return;
        }
        this.#pushBtn.addEventListener('click', () => this.#togglePush());
        const subscription = await this.#currentSubscription();
        this.#renderPushState(subscription !== null);
    }

    async #currentSubscription() {
        const registration = await navigator.serviceWorker.getRegistration(SERVICE_WORKER_URL);
        return registration ? registration.pushManager.getSubscription() : null;
    }

    async #togglePush() {
        this.#pushBtn.disabled = true;
        try {
            const subscription = await this.#currentSubscription();
            if (subscription) {
                await this.#unsubscribe(subscription);
                this.#renderPushState(false);
            } else {
                this.#renderPushState(await this.#subscribe());
            }
        } catch (err) {
            console.error('Push notification toggle failed', err);
            this.#setLabel('Notifications: error, try again');
        } finally {
            this.#pushBtn.disabled = false;
        }
    }

    async #subscribe() {
        const permission = await Notification.requestPermission();
        if (permission !== 'granted') {
            this.#setLabel('Notifications blocked by the browser');
            return false;
        }
        const registration = await navigator.serviceWorker.register(SERVICE_WORKER_URL);
        await navigator.serviceWorker.ready;

        const keyRes = await authedFetch(PUSH_PUBLIC_KEY_API);
        if (!keyRes.ok) throw new Error(`${PUSH_PUBLIC_KEY_API} failed: ${keyRes.status}`);
        const { public_key: publicKey } = await keyRes.json();

        const subscription = await registration.pushManager.subscribe({
            userVisibleOnly: true,
            applicationServerKey: DetectionNotifications.#decodeKey(publicKey),
        });
        const res = await authedFetch(PUSH_SUBSCRIPTIONS_API, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(subscription.toJSON()),
        });
        if (!res.ok) {
            await subscription.unsubscribe();
            throw new Error(`${PUSH_SUBSCRIPTIONS_API} failed: ${res.status}`);
        }
        return true;
    }

    /** @param {PushSubscription} subscription */
    async #unsubscribe(subscription) {
        await authedFetch(PUSH_SUBSCRIPTIONS_API, {
            method: 'DELETE',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ endpoint: subscription.endpoint }),
        });
        await subscription.unsubscribe();
    }

    /** @param {boolean} subscribed */
    #renderPushState(subscribed) {
        this.#pushBtn.setAttribute('aria-pressed', String(subscribed));
        this.#setLabel(subscribed
            ? 'Notifications on this device: on'
            : 'Notifications on this device: off');
    }

    /** @param {string} text */
    #setLabel(text) {
        if (this.#pushLabelEl) this.#pushLabelEl.textContent = text;
    }

    /**
     * base64url VAPID public key → Uint8Array applicationServerKey.
     * @param {string} base64url
     */
    static #decodeKey(base64url) {
        const base64 = (base64url + '='.repeat((4 - base64url.length % 4) % 4))
            .replace(/-/g, '+').replace(/_/g, '/');
        return Uint8Array.from(atob(base64), (c) => c.charCodeAt(0));
    }
}
