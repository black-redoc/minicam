/**
 * confirmDialog — themed, promise-based replacement for window.confirm().
 *
 * Lazily builds a single reused overlay in document.body the first time
 * it's called, so any page that needs it only has to load this script —
 * no markup to add to a template. Escape is handled on the capture phase
 * so it always wins over an ancestor modal's own Escape handler (e.g. the
 * camera-delete confirmation can be triggered from inside ConfigWidget's
 * modal — without capture, ConfigWidget's Escape listener would close the
 * whole modal instead of just this dialog).
 */
/* exported confirmDialog */

/** @type {HTMLElement | null} */
let _dialogEl = null;
/** @type {((result: boolean) => void) | null} */
let _resolve = null;
/** @type {HTMLElement | null} */
let _previousFocusEl = null;

/** @returns {HTMLElement} */
function _ensureDialog() {
    if (_dialogEl) return _dialogEl;

    const overlay = document.createElement('div');
    overlay.setAttribute('aria-hidden', 'true');
    overlay.className =
        'hidden fixed inset-0 z-[60] flex items-center justify-center bg-black/60 p-4';
    overlay.innerHTML = `
        <div role="alertdialog" aria-modal="true" aria-labelledby="confirm-dialog-title"
             aria-describedby="confirm-dialog-message"
             class="bg-slate-800 border border-slate-700 rounded-xl shadow-2xl
                    w-full max-w-sm p-5 flex flex-col gap-4">
            <h2 id="confirm-dialog-title" class="text-sm font-semibold text-slate-100"></h2>
            <p id="confirm-dialog-message" class="text-sm text-slate-400"></p>
            <div class="flex items-center justify-end gap-2">
                <button type="button" data-cancel
                        class="text-xs px-4 py-2 rounded-lg text-slate-400
                               hover:text-slate-100 transition-colors focus:outline-none
                               focus:ring-2 focus:ring-sky-500">
                    Cancel
                </button>
                <button type="button" data-confirm
                        class="text-xs px-4 py-2 rounded-lg font-medium transition-colors
                               focus:outline-none focus:ring-2 focus:ring-offset-2
                               focus:ring-offset-slate-800"></button>
            </div>
        </div>
    `;
    document.body.appendChild(overlay);

    overlay.addEventListener('click', (e) => {
        if (e.target === overlay) _close(false);
    });
    overlay.querySelector('[data-cancel]')?.addEventListener('click', () => _close(false));
    overlay.querySelector('[data-confirm]')?.addEventListener('click', () => _close(true));

    document.addEventListener(
        'keydown',
        (e) => {
            if (!_dialogEl || _dialogEl.classList.contains('hidden')) return;
            if (e.key === 'Escape') {
                e.stopPropagation();
                _close(false);
            }
        },
        true
    );

    _dialogEl = overlay;
    return overlay;
}

/** @param {boolean} result */
function _close(result) {
    if (!_dialogEl) return;
    _dialogEl.classList.add('hidden');
    _dialogEl.setAttribute('aria-hidden', 'true');
    document.body.style.overflow = '';
    _previousFocusEl?.focus();
    _previousFocusEl = null;
    if (_resolve) {
        _resolve(result);
        _resolve = null;
    }
}

/**
 * Show a themed confirm dialog and resolve once the user picks an option.
 *
 * @param {object} opts
 * @param {string} opts.title
 * @param {string} opts.message
 * @param {string} [opts.confirmLabel]
 * @param {boolean} [opts.danger] - Style the confirm button as destructive (red).
 * @returns {Promise<boolean>} true if confirmed, false if cancelled/dismissed.
 */
function confirmDialog({ title, message, confirmLabel = 'Confirm', danger = false }) {
    const overlay = _ensureDialog();
    const titleEl = /** @type {HTMLElement} */ (overlay.querySelector('#confirm-dialog-title'));
    const messageEl = /** @type {HTMLElement} */ (
        overlay.querySelector('#confirm-dialog-message')
    );
    const confirmBtn = /** @type {HTMLButtonElement} */ (overlay.querySelector('[data-confirm]'));

    titleEl.textContent = title;
    messageEl.textContent = message;
    confirmBtn.textContent = confirmLabel;
    confirmBtn.className = danger
        ? 'text-xs px-4 py-2 rounded-lg font-medium transition-colors bg-red-600 ' +
          'hover:bg-red-500 text-white focus:outline-none focus:ring-2 focus:ring-red-500 ' +
          'focus:ring-offset-2 focus:ring-offset-slate-800'
        : 'text-xs px-4 py-2 rounded-lg font-medium transition-colors bg-sky-600 ' +
          'hover:bg-sky-500 text-white focus:outline-none focus:ring-2 focus:ring-sky-500 ' +
          'focus:ring-offset-2 focus:ring-offset-slate-800';

    _previousFocusEl = /** @type {HTMLElement | null} */ (document.activeElement);
    overlay.classList.remove('hidden');
    overlay.removeAttribute('aria-hidden');
    document.body.style.overflow = 'hidden';
    confirmBtn.focus();

    return new Promise((resolve) => {
        _resolve = resolve;
    });
}
