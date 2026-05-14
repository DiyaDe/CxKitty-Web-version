async function bootWorkspacePage() {
    const result = await apiRequest('/api/account/info', 'GET');
    if (!result.logged_in || !result.user) {
        window.location.href = '/';
        return null;
    }

    renderWorkspaceAccount(result.user);
    bindWorkspaceLogout();
    return result.user;
}

function renderWorkspaceAccount(user) {
    const infoDiv = document.getElementById('account-info');
    if (!infoDiv) return;
    infoDiv.innerHTML = `
        <div class="account-mini-main">
            <strong>${escapeHtml(user.name || '未命名用户')}</strong>
            <span>${escapeHtml(user.school || '未绑定学校')}</span>
        </div>
        <div class="account-mini-side">
            <span>${escapeHtml(user.phone || '-')}</span>
            <span>PUID ${escapeHtml(user.puid || '-')}</span>
        </div>
    `;
}

function bindWorkspaceLogout() {
    const logoutBtn = document.getElementById('btn-logout');
    if (!logoutBtn || logoutBtn.dataset.bound === 'true') return;
    logoutBtn.dataset.bound = 'true';
    logoutBtn.addEventListener('click', async () => {
        await apiRequest('/api/logout', 'POST');
        clearTaskSelection();
        window.location.href = '/';
    });
}

