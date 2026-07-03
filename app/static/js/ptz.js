/**
 * PTZ controller — sends async HTTP commands for pan-tilt-zoom actions.
 *
 * Direction buttons trigger a move on pointerdown and stop on pointerup/leave,
 * allowing continuous movement while the button is held (mouse or touch).
 */

const PTZ_DIRECTIONS = ['up', 'down', 'left', 'right'];

/**
 * Send a single PTZ action to the backend for a specific camera.
 *
 * @param {string} action      - One of: up | down | left | right | stop
 * @param {string} cameraName  - Target camera name.
 * @returns {Promise<void>}
 */
async function sendPtz(action, cameraName) {
    try {
        const res = await fetch(
            `/api/ptz/${encodeURIComponent(cameraName)}/${action}`,
            { method: 'POST' }
        );
        if (!res.ok) console.warn(`PTZ "${action}" responded ${res.status}`);
    } catch (err) {
        console.error(`PTZ "${action}" failed:`, err);
    }
}

/**
 * Bind PTZ buttons inside a container element and return a control handle.
 *
 * Expects buttons with [data-ptz="<action>"] attributes.
 * Direction buttons auto-send "stop" on release.
 *
 * @param {HTMLElement} container
 * @param {string}      cameraName  - Initial camera name for PTZ requests.
 * @returns {{ setCameraName: (name: string) => void }}
 */
function initPtz(container, cameraName) {
    let currentCameraName = cameraName;

    for (const action of PTZ_DIRECTIONS) {
        const btn = container.querySelector(`[data-ptz="${action}"]`);
        if (!btn) continue;

        btn.addEventListener('pointerdown', (e) => {
            e.preventDefault();
            btn.setPointerCapture(e.pointerId);
            btn.setAttribute('aria-pressed', 'true');
            sendPtz(action, currentCameraName);
        });

        const release = () => {
            btn.removeAttribute('aria-pressed');
            sendPtz('stop', currentCameraName);
        };

        btn.addEventListener('pointerup',     release);
        btn.addEventListener('pointercancel', release);
    }

    const stopBtn = container.querySelector('[data-ptz="stop"]');
    if (stopBtn) {
        stopBtn.addEventListener('pointerdown', (e) => {
            e.preventDefault();
            sendPtz('stop', currentCameraName);
        });
    }

    return {
        setCameraName(name) {
            currentCameraName = name;
        },
    };
}
