/**
 * Service worker — shows Web Push detection notifications.
 *
 * Served from /service-worker.js (see app/routers/notifications.py) so its
 * scope covers every page. The push payload is the JSON built by
 * DetectionEvent.to_payload() in app/services/notifications/events.py.
 */

/** @type {any} */
const sw = self;

sw.addEventListener('push', (/** @type {any} */ event) => {
    let payload = {};
    try {
        payload = event.data ? event.data.json() : {};
    } catch {
        // A payload we can't parse still deserves a generic notification.
    }
    const title = payload.title || 'minicam';
    event.waitUntil(sw.registration.showNotification(title, {
        body: payload.body || 'Detection on a camera',
        icon: '/static/img/favicon.svg',
        // One notification per camera: a newer detection replaces it.
        tag: payload.camera ? `detection:${payload.camera}` : 'detection',
        renotify: true,
        data: { url: '/' },
    }));
});

sw.addEventListener('notificationclick', (/** @type {any} */ event) => {
    event.notification.close();
    const url = event.notification.data?.url || '/';
    event.waitUntil((async () => {
        const windows = await sw.clients.matchAll({ type: 'window', includeUncontrolled: true });
        const existing = windows.find((w) => new URL(w.url).origin === sw.location.origin);
        if (existing) return existing.focus();
        return sw.clients.openWindow(url);
    })());
});
