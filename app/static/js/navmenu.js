/**
 * Nav "more options" (⋮) dropdown menu.
 *
 * A lightweight, non-modal `role="menu"` popup — unlike ConfigWidget's
 * dialog it has no focus trap. It closes on Escape, on any click outside
 * the trigger/panel, and whenever a menu item inside the panel is clicked
 * (that item's own click handler, if any, still runs — this only hides
 * the panel afterwards).
 */
/* exported initNavMenu */

/**
 * Wire up a kebab-menu trigger/panel pair.
 *
 * @param {HTMLButtonElement} trigger - The "⋮" button that toggles the panel.
 * @param {HTMLElement}       panel   - The `role="menu"` panel to show/hide.
 * @returns {{ close: () => void }}
 */
function initNavMenu(trigger, panel) {
    function isOpen() {
        return !panel.classList.contains('hidden');
    }

    function open() {
        panel.classList.remove('hidden');
        trigger.setAttribute('aria-expanded', 'true');
    }

    function close() {
        panel.classList.add('hidden');
        trigger.setAttribute('aria-expanded', 'false');
    }

    trigger.addEventListener('click', (e) => {
        e.stopPropagation();
        if (isOpen()) close(); else open();
    });

    panel.addEventListener('click', (e) => {
        if (e.target instanceof Element && e.target.closest('[role="menuitem"]')) {
            close();
        }
    });

    document.addEventListener('click', (e) => {
        if (!isOpen()) return;
        if (e.target instanceof Node && (trigger.contains(e.target) || panel.contains(e.target))) return;
        close();
    });

    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && isOpen()) close();
    });

    return { close };
}
