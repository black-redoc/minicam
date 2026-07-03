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
 * @param {string} action        - One of: up | down | left | right | stop
 * @param {string} cameraName    - Target camera name.
 * @param {AbortSignal} signal   - Aborts this request if a newer one supersedes it.
 * @returns {Promise<void>}
 */
async function sendPtz(action, cameraName, signal) {
    try {
        const res = await fetch(
            `/api/ptz/${encodeURIComponent(cameraName)}/${action}`,
            { method: 'POST', signal }
        );
        if (!res.ok) console.warn(`PTZ "${action}" responded ${res.status}`);
    } catch (err) {
        if (err.name === 'AbortError') return; // superseded by a newer command
        console.error(`PTZ "${action}" failed:`, err);
    }
}

/**
 * Bind PTZ buttons inside a container element and return a control handle.
 *
 * Expects buttons with [data-ptz="<action>"] attributes.
 * Direction buttons auto-send "stop" on release.
 *
 * Only one PTZ request is ever in flight at a time: dispatching a new action
 * aborts whichever one is still pending. A user mashing buttons (rapid
 * clicks, not just holding one down) would otherwise pile up several
 * concurrent requests for the same camera — this keeps that down to one
 * request on the wire, no matter how fast someone clicks.
 *
 * @param {HTMLElement} container
 * @param {string}      cameraName  - Initial camera name for PTZ requests.
 * @returns {{ setCameraName: (name: string) => void }}
 */
function initPtz(container, cameraName) {
    let currentCameraName = cameraName;
    let inFlight = null;

    function dispatch(action) {
        inFlight?.abort();
        const controller = new AbortController();
        inFlight = controller;
        sendPtz(action, currentCameraName, controller.signal);
    }

    for (const action of PTZ_DIRECTIONS) {
        const btn = container.querySelector(`[data-ptz="${action}"]`);
        if (!btn) continue;

        btn.addEventListener('pointerdown', (e) => {
            e.preventDefault();
            btn.setPointerCapture(e.pointerId);
            btn.setAttribute('aria-pressed', 'true');
            dispatch(action);
        });

        const release = () => {
            btn.removeAttribute('aria-pressed');
            dispatch('stop');
        };

        btn.addEventListener('pointerup',     release);
        btn.addEventListener('pointercancel', release);
    }

    const stopBtn = container.querySelector('[data-ptz="stop"]');
    if (stopBtn) {
        stopBtn.addEventListener('pointerdown', (e) => {
            e.preventDefault();
            dispatch('stop');
        });
    }

    return {
        setCameraName(name) {
            currentCameraName = name;
        },
    };
}
