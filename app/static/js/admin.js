/**
 * Admin page — create users and manage per-camera view/PTZ permissions
 * and each user's WAN access.
 *
 * All data is fetched client-side from the existing admin-only API:
 *   GET  /api/cameras          - cameras configured on this Pi
 *   GET  /api/auth/users       - every account
 *   GET  /api/auth/permissions?user_id=X - one user's camera permissions
 *   GET  /api/auth/wan-access?user_id=X  - one user's WAN access
 *   POST /api/auth/users       - create an account
 *   POST /api/auth/permissions - upsert one (user, camera) permission
 *   POST /api/auth/wan-access  - grant or revoke one user's WAN access
 */

const ADMIN_CAMERAS_API     = '/api/cameras';
const USERS_API       = '/api/auth/users';
const PERMISSIONS_API = '/api/auth/permissions';
const WAN_ACCESS_API  = '/api/auth/wan-access';

class AdminPage {
    #listEl
    #cameras = []

    /**
     * @param {HTMLElement} listEl - Container the user cards render into.
     */
    constructor(listEl) {
        this.#listEl = listEl;
    }

    async load() {
        const [cameras, users] = await Promise.all([
            this.#fetchJson(ADMIN_CAMERAS_API),
            this.#fetchJson(USERS_API),
        ]);
        this.#cameras = cameras;
        await this.#renderUsers(users);
    }

    async #fetchJson(url, options) {
        const res = await authedFetch(url, options);
        if (!res.ok) throw new Error(`${url} failed: ${res.status}`);
        return res.json();
    }

    async #renderUsers(users) {
        if (users.length === 0) {
            this.#listEl.innerHTML = '<p class="text-sm text-slate-500 px-1">No users yet.</p>';
            return;
        }

        const [permissionsByUser, wanAccessByUser] = await Promise.all([
            Promise.all(users.map((user) => this.#fetchJson(`${PERMISSIONS_API}?user_id=${user.id}`))),
            Promise.all(users.map((user) => this.#fetchJson(`${WAN_ACCESS_API}?user_id=${user.id}`))),
        ]);

        this.#listEl.innerHTML = '';
        users.forEach((user, i) => {
            this.#listEl.appendChild(
                this.#buildUserCard(user, permissionsByUser[i], wanAccessByUser[i].is_active),
            );
        });
    }

    /**
     * @param {{id: number, email: string, is_admin: boolean}} user
     * @param {Array<{camera_name: string, can_view: boolean, can_use_ptz: boolean}>} permissions
     * @param {boolean} wanAccess
     */
    #buildUserCard(user, permissions, wanAccess) {
        const permByCamera = new Map(permissions.map((p) => [p.camera_name, p]));

        const card = document.createElement('div');
        card.className = 'card flex flex-col gap-3';
        card.innerHTML = `
            <div class="flex items-center justify-between gap-2">
                <div class="flex items-center gap-2 min-w-0">
                    <span class="text-sm font-medium text-slate-100 truncate">${this.#escape(user.email)}</span>
                    ${user.is_admin ? '<span class="badge">admin</span>' : ''}
                </div>
                <div class="flex items-center gap-3 shrink-0">
                <label class="flex items-center gap-2 text-xs text-slate-400"
                       title="Allow viewing streams and using PTZ from outside the local network (through Cloudflare Access)">
                    <input type="checkbox" data-wan-access class="accent-sky-500">
                    WAN access
                </label>
                <button type="button" data-delete-user
                        aria-label="Delete user ${this.#escape(user.email)}"
                        title="Delete user"
                        class="flex items-center justify-center w-7 h-7 rounded-md shrink-0
                               text-red-400 hover:text-red-300 hover:bg-red-950/40
                               transition-colors focus:outline-none focus:ring-2 focus:ring-red-500">
                    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"
                         stroke="currentColor" stroke-width="2" stroke-linecap="round"
                         stroke-linejoin="round" class="w-4 h-4 pointer-events-none">
                        <polyline points="3 6 5 6 21 6"/>
                        <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>
                        <path d="M10 11v6"/>
                        <path d="M14 11v6"/>
                        <path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/>
                    </svg>
                </button>
                </div>
            </div>
        `;
        card.querySelector('[data-delete-user]')
            .addEventListener('click', () => this.#deleteUser(user.id, user.email));

        const wanCheckbox = /** @type {HTMLInputElement} */ (card.querySelector('[data-wan-access]'));
        wanCheckbox.checked = wanAccess;
        wanCheckbox.addEventListener('change', () => this.#saveWanAccess(user.id, wanCheckbox));

        if (this.#cameras.length === 0) {
            const empty = document.createElement('p');
            empty.className = 'text-xs text-slate-500';
            empty.textContent = 'No cameras configured yet.';
            card.appendChild(empty);
            return card;
        }

        const table = document.createElement('table');
        table.className = 'w-full text-xs text-slate-300';
        table.innerHTML = `
            <thead>
                <tr class="text-slate-500 text-left">
                    <th class="font-normal py-1">Camera</th>
                    <th class="font-normal py-1 text-center">View</th>
                    <th class="font-normal py-1 text-center">PTZ</th>
                </tr>
            </thead>
        `;

        const tbody = document.createElement('tbody');
        for (const camera of this.#cameras) {
            const existing  = permByCamera.get(camera.name);
            const canView   = existing?.can_view ?? false;
            const canUsePtz = existing?.can_use_ptz ?? false;
            tbody.appendChild(
                this.#buildPermissionRow(user.id, camera.name, canView, canUsePtz),
            );
        }
        table.appendChild(tbody);
        card.appendChild(table);
        return card;
    }

    #buildPermissionRow(userId, cameraName, canView, canUsePtz) {
        const row = document.createElement('tr');
        row.className = 'border-t border-slate-800';

        const viewCheckbox = this.#buildCheckbox(canView);
        const ptzCheckbox  = this.#buildCheckbox(canUsePtz);

        const save = () => this.#savePermission(
            userId, cameraName, viewCheckbox.checked, ptzCheckbox.checked,
        );
        viewCheckbox.addEventListener('change', save);
        ptzCheckbox.addEventListener('change', save);

        row.innerHTML = `<td class="py-1.5">${this.#escape(cameraName)}</td>`;

        const viewCell = document.createElement('td');
        viewCell.className = 'text-center';
        viewCell.appendChild(viewCheckbox);
        row.appendChild(viewCell);

        const ptzCell = document.createElement('td');
        ptzCell.className = 'text-center';
        ptzCell.appendChild(ptzCheckbox);
        row.appendChild(ptzCell);

        return row;
    }

    #buildCheckbox(checked) {
        const input = document.createElement('input');
        input.type = 'checkbox';
        input.checked = checked;
        input.className = 'accent-sky-500';
        return input;
    }

    async #savePermission(userId, cameraName, canView, canUsePtz) {
        await authedFetch(PERMISSIONS_API, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                user_id: userId,
                camera_name: cameraName,
                can_view: canView,
                can_use_ptz: canUsePtz,
            }),
        });
    }

    /**
     * Persist the checkbox state; on failure, put it back and show why.
     *
     * @param {number} userId
     * @param {HTMLInputElement} checkbox
     */
    async #saveWanAccess(userId, checkbox) {
        const errorEl = document.getElementById('users-list-error');
        errorEl.classList.add('hidden');
        checkbox.disabled = true;
        try {
            const res = await authedFetch(WAN_ACCESS_API, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ user_id: userId, is_active: checkbox.checked }),
            });
            if (!res.ok) {
                const body = await res.json().catch(() => ({}));
                throw new Error(body.detail ?? `Failed to update WAN access (${res.status}).`);
            }
        } catch (err) {
            if (err instanceof SessionExpiredError) return; // authedFetch already redirected
            checkbox.checked = !checkbox.checked;
            errorEl.textContent = err instanceof Error ? err.message : String(err);
            errorEl.classList.remove('hidden');
        } finally {
            checkbox.disabled = false;
        }
    }

    /**
     * @param {number} userId
     * @param {string} email
     */
    async #deleteUser(userId, email) {
        const confirmed = await confirmDialog({
            title: 'Delete user',
            message: `Delete "${email}"? This also revokes their sessions and camera permissions.`,
            confirmLabel: 'Delete',
            danger: true,
        });
        if (!confirmed) return;

        const errorEl = document.getElementById('users-list-error');
        errorEl.classList.add('hidden');

        const res = await authedFetch(`${USERS_API}/${userId}`, { method: 'DELETE' });
        if (!res.ok) {
            const body = await res.json().catch(() => ({}));
            errorEl.textContent = body.detail ?? `Failed to delete user (${res.status}).`;
            errorEl.classList.remove('hidden');
            return;
        }
        await this.load();
    }

    #escape(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
}

document.getElementById('create-user-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const errorEl = document.getElementById('create-user-error');
    errorEl.classList.add('hidden');

    const res = await authedFetch(USERS_API, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            email: /** @type {HTMLInputElement} */ (document.getElementById('new-email')).value,
            password: /** @type {HTMLInputElement} */ (document.getElementById('new-password')).value,
            is_admin: /** @type {HTMLInputElement} */ (document.getElementById('new-is-admin')).checked,
        }),
    });

    if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        errorEl.textContent = body.detail ?? `Failed to create user (${res.status}).`;
        errorEl.classList.remove('hidden');
        return;
    }

    /** @type {HTMLFormElement} */ (event.target).reset();
    await adminPage.load();
});

const adminPage = new AdminPage(document.getElementById('users-list'));
adminPage.load();
