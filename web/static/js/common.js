const TASK_SELECTION_KEY = 'xuexitong.currentTaskSelection';

function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, (char) => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;'
    }[char]));
}

function showAlert(message, type = 'success') {
    const existing = document.querySelector('.alert');
    if (existing) existing.remove();

    const alertDiv = document.createElement('div');
    alertDiv.className = `alert alert-${type}`;
    if (type === 'error') {
        alertDiv.setAttribute('role', 'alert');
        alertDiv.setAttribute('aria-live', 'assertive');
    } else {
        alertDiv.setAttribute('role', 'status');
        alertDiv.setAttribute('aria-live', 'polite');
    }
    alertDiv.textContent = message;
    document.body.appendChild(alertDiv);

    setTimeout(() => {
        alertDiv.style.opacity = '0';
        setTimeout(() => alertDiv.remove(), 500);
    }, 4000);
}

async function apiRequest(url, method = 'GET', body = null) {
    const options = {
        method,
        headers: { 'Content-Type': 'application/json' }
    };

    const payload = body ? { ...body } : {};
    if (method.toUpperCase() === 'GET') {
        const params = new URLSearchParams(payload);
        if (params.toString()) {
            url += (url.includes('?') ? '&' : '?') + params.toString();
        }
    } else {
        options.body = JSON.stringify(payload);
    }

    try {
        const response = await fetch(url, { ...options, credentials: 'same-origin' });
        return await response.json();
    } catch (error) {
        console.error('API request failed:', error);
        return { status: 'error', message: '网络请求失败，请检查服务器状态' };
    }
}

function renderSummary(containerId, rows) {
    const container = document.getElementById(containerId);
    if (!container) return;
    container.innerHTML = rows.map((row) => `
        <div class="summary-item">
            <span class="summary-label">${escapeHtml(row.label)}</span>
            <strong class="summary-value">${escapeHtml(row.value)}</strong>
        </div>
    `).join('');
}

function formatTaskType(type) {
    return type === 'exam' ? '课程考试' : '章节任务';
}

function getTaskSelection() {
    try {
        const raw = sessionStorage.getItem(TASK_SELECTION_KEY) || localStorage.getItem(TASK_SELECTION_KEY);
        return raw ? JSON.parse(raw) : null;
    } catch (_error) {
        return null;
    }
}

function setTaskSelection(task) {
    const raw = JSON.stringify(task);
    sessionStorage.setItem(TASK_SELECTION_KEY, raw);
    localStorage.setItem(TASK_SELECTION_KEY, raw);
}

function clearTaskSelection() {
    sessionStorage.removeItem(TASK_SELECTION_KEY);
    localStorage.removeItem(TASK_SELECTION_KEY);
}

function ensureModalHost() {
    let host = document.getElementById('app-modal-host');
    if (host) return host;
    host = document.createElement('div');
    host.id = 'app-modal-host';
    document.body.appendChild(host);
    return host;
}

function closeModal() {
    const host = document.getElementById('app-modal-host');
    if (host) host.innerHTML = '';
}

function showModal({ title = '提示', message = '', primaryText = '确定', secondaryText = '取消', onPrimary, onSecondary, allowBackdropClose = true } = {}) {
    const host = ensureModalHost();
    const safeTitle = escapeHtml(title);
    const safeMessage = escapeHtml(message).replace(/\n/g, '<br>');
    host.innerHTML = `
        <div class="app-modal-backdrop" data-modal-backdrop="1">
            <div class="app-modal" role="dialog" aria-modal="true" aria-label="${safeTitle}">
                <div class="app-modal-header">
                    <h3 class="app-modal-title">${safeTitle}</h3>
                    <button class="app-modal-close" type="button" data-modal-close="1" aria-label="关闭">×</button>
                </div>
                <div class="app-modal-body">${safeMessage}</div>
                <div class="app-modal-actions">
                    ${secondaryText ? `<button class="btn btn-secondary" type="button" data-modal-secondary="1">${escapeHtml(secondaryText)}</button>` : ''}
                    <button class="btn btn-primary" type="button" data-modal-primary="1">${escapeHtml(primaryText)}</button>
                </div>
            </div>
        </div>
    `;

    const backdrop = host.querySelector('[data-modal-backdrop="1"]');
    const btnClose = host.querySelector('[data-modal-close="1"]');
    const btnPrimary = host.querySelector('[data-modal-primary="1"]');
    const btnSecondary = host.querySelector('[data-modal-secondary="1"]');

    const handleClose = () => {
        closeModal();
        document.removeEventListener('keydown', onKeydown, true);
    };

    const onKeydown = (event) => {
        if (event.key === 'Escape') {
            event.preventDefault();
            handleClose();
        }
    };
    document.addEventListener('keydown', onKeydown, true);

    if (allowBackdropClose && backdrop) {
        backdrop.addEventListener('click', (event) => {
            if (event.target === backdrop) handleClose();
        });
    }
    if (btnClose) btnClose.addEventListener('click', handleClose);

    if (btnPrimary) {
        btnPrimary.addEventListener('click', async () => {
            try {
                if (typeof onPrimary === 'function') await onPrimary();
            } finally {
                handleClose();
            }
        });
    }
    if (btnSecondary) {
        btnSecondary.addEventListener('click', async () => {
            try {
                if (typeof onSecondary === 'function') await onSecondary();
            } finally {
                handleClose();
            }
        });
    }

    if (btnPrimary) btnPrimary.focus();
}
