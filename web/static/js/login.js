let qrPollTimer = null;
let qrFetching = false;

function resetQrState(statusText = '正在加载二维码...') {
    if (qrPollTimer) {
        clearInterval(qrPollTimer);
        qrPollTimer = null;
    }
    qrFetching = false;
    const qrContainer = document.getElementById('qrcode');
    if (qrContainer) qrContainer.innerHTML = '';
    const statusEl = document.getElementById('qr-status');
    if (statusEl) statusEl.textContent = statusText;
}

async function fetchQrCode() {
    if (qrFetching) return;
    qrFetching = true;

    const statusEl = document.getElementById('qr-status');
    if (statusEl) statusEl.textContent = '正在加载二维码...';

    const result = await apiRequest('/api/login/qr/create', 'POST');
    if (result.status === 'created') {
        const qrContainer = document.getElementById('qrcode');
        if (qrContainer) qrContainer.innerHTML = '';
        new QRCode(qrContainer, {
            text: result.qr_url,
            width: 180,
            height: 180,
            colorDark: '#000000',
            colorLight: '#ffffff',
            correctLevel: QRCode.CorrectLevel.H
        });
        if (statusEl) statusEl.textContent = '请打开学习通扫一扫';
        startQrPoll();
    } else {
        if (statusEl) statusEl.textContent = '获取失败，3秒后自动重试';
        setTimeout(fetchQrCode, 3000);
    }

    qrFetching = false;
}

function startQrPoll() {
    if (qrPollTimer) clearInterval(qrPollTimer);
    qrPollTimer = setInterval(async () => {
        const result = await apiRequest('/api/login/qr/poll', 'POST');
        const statusEl = document.getElementById('qr-status');

        switch (result.status) {
            case 'success':
                if (statusEl) statusEl.textContent = '登录成功!';
                clearInterval(qrPollTimer);
                showAlert('扫码登录成功');
                window.location.href = '/courses';
                break;
            case 'error':
                if (result.message && (result.message.includes('请先获取二维码') || result.message.includes('二维码'))) {
                    if (statusEl) statusEl.textContent = '二维码已失效，自动刷新中';
                    clearInterval(qrPollTimer);
                    setTimeout(fetchQrCode, 1500);
                } else if (statusEl) {
                    statusEl.textContent = result.message;
                }
                break;
            case 'waiting': {
                const t = String(result.type ?? '');
                if (t === '4') {
                    if (statusEl) statusEl.textContent = result.nickname ? `已扫描，等待确认：${result.nickname}` : '已扫描，等待手机确认';
                } else if (t === '6' || t === '3') {
                    if (statusEl) statusEl.textContent = '二维码已被扫描，等待手机确认';
                } else if (t === '1') {
                    if (statusEl) statusEl.textContent = '验证失败，刷新二维码';
                    clearInterval(qrPollTimer);
                    setTimeout(fetchQrCode, 1500);
                } else if (t === '2') {
                    if (statusEl) statusEl.textContent = '二维码已失效，自动刷新中';
                    clearInterval(qrPollTimer);
                    setTimeout(fetchQrCode, 1500);
                } else if (statusEl) {
                    statusEl.textContent = '等待扫码...';
                }
                break;
            }
        }
    }, 2000);
}

document.addEventListener('DOMContentLoaded', async () => {
    const loginState = await apiRequest('/api/account/info', 'GET');
    if (loginState.logged_in) {
        window.location.href = '/courses';
        return;
    }

    const loginContainer = document.getElementById('login-container');
    const qrFormContainer = document.querySelector('.qr-login-container');
    const passwordFormContainer = document.querySelector('.password-login-container');
    const toQrBtn = document.getElementById('to-qr');
    const toPasswordBtn = document.getElementById('to-password');

    function applyInert(el, inert) {
        if (!el) return;
        if (inert) {
            el.setAttribute('aria-hidden', 'true');
            el.setAttribute('inert', '');
        } else {
            el.removeAttribute('aria-hidden');
            el.removeAttribute('inert');
        }
    }

    function setLoginMode(mode) {
        const isQr = mode === 'qr';
        if (loginContainer) loginContainer.classList.toggle('right-panel-active', isQr);
        applyInert(passwordFormContainer, isQr);
        applyInert(qrFormContainer, !isQr);
    }

    setLoginMode('password');

    if (toQrBtn) {
        toQrBtn.addEventListener('click', () => {
            setLoginMode('qr');
            resetQrState();
            fetchQrCode();
        });
    }

    if (toPasswordBtn) {
        toPasswordBtn.addEventListener('click', () => {
            setLoginMode('password');
            resetQrState();
        });
    }

    document.getElementById('btn-login').addEventListener('click', async () => {
        const phone = document.getElementById('phone').value.trim();
        const password = document.getElementById('password').value;
        if (!phone || !password) {
            showAlert('请输入账号和密码', 'error');
            return;
        }

        const btn = document.getElementById('btn-login');
        btn.disabled = true;
        btn.textContent = '登录中...';
        const result = await apiRequest('/api/login/passwd', 'POST', { phone, password });
        btn.disabled = false;
        btn.textContent = '登 录';

        if (result.status === 'success') {
            showAlert('登录成功');
            window.location.href = '/courses';
        } else {
            showAlert(result.message || '登录失败', 'error');
        }
    });
});
