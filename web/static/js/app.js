let socket = null;
let qrPollTimer = null;
let qrFetching = false;
let taskStatusTimer = null;
let currentUser = null;
let currentCourse = null;
let currentTaskSelection = null;
let currentTaskRunning = false;

function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, (char) => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;'
    }[char]));
}

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

function initSocket() {
    socket = io();

    socket.on('connect', () => {
        socket.emit('join', {});
    });

    socket.on('task_log', (data) => {
        appendLog(data.message, data.level);
    });
}

function appendLog(message, level = 'info') {
    const logContainer = document.getElementById('task-log');
    if (!logContainer) return;

    if (message === 'task_finished') {
        const entry = document.createElement('div');
        entry.className = 'log-entry';
        entry.innerHTML = '<span class="log-success">=== 任务执行完毕 ===</span>';
        logContainer.appendChild(entry);
        logContainer.scrollTop = logContainer.scrollHeight;
        currentTaskRunning = false;
        updateTaskStatusUI(false, currentTaskSelection, '任务已完成');
        return;
    }

    const entry = document.createElement('div');
    entry.className = 'log-entry';
    const time = new Date().toLocaleTimeString();
    entry.innerHTML = `<span class="log-${level}">[${time}] ${escapeHtml(message)}</span>`;
    logContainer.appendChild(entry);
    logContainer.scrollTop = logContainer.scrollHeight;
}

function showAlert(message, type = 'success') {
    const existing = document.querySelector('.alert');
    if (existing) existing.remove();

    const alertDiv = document.createElement('div');
    alertDiv.className = `alert alert-${type}`;
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

function showMainSection(sectionId) {
    const loginContainer = document.getElementById('login-container');
    const appShell = document.getElementById('app-shell');
    if (loginContainer) loginContainer.style.display = sectionId === 'login' ? 'block' : 'none';
    if (appShell) appShell.style.display = sectionId === 'workspace' ? 'block' : 'none';
}

function showWorkspaceView(viewId) {
    document.querySelectorAll('.workspace-view').forEach((view) => {
        view.style.display = view.id === viewId ? 'block' : 'none';
    });
    document.querySelectorAll('.nav-chip').forEach((chip) => {
        chip.classList.toggle('active', chip.dataset.view === viewId);
    });
}

function formatTaskType(type) {
    return type === 'exam' ? '课程考试' : '章节任务';
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

function updateTaskStatusUI(running, task = null, statusText = '') {
    const indicator = document.getElementById('task-running-indicator');
    const statusEl = document.getElementById('task-status-text');
    const startBtn = document.getElementById('btn-start-task');
    const stopBtn = document.getElementById('btn-stop-task');

    currentTaskRunning = Boolean(running);
    if (indicator) {
        indicator.textContent = running ? '运行中' : '空闲中';
        indicator.classList.toggle('is-running', Boolean(running));
    }
    if (statusEl) {
        statusEl.textContent = statusText || (running ? '任务正在后台执行，请关注右侧日志输出。' : '请选择课程或考试后开始执行。');
    }
    if (startBtn) startBtn.disabled = running || !currentTaskSelection;
    if (stopBtn) stopBtn.disabled = !running;

    if (task) {
        renderSummary('task-info', [
            { label: '执行目标', value: task.name || '未选择' },
            { label: '任务类型', value: formatTaskType(task.type) },
            { label: '课程ID', value: task.course_id || '-' },
            { label: '班级ID', value: task.class_id || '-' }
        ]);
    } else if (!currentTaskSelection) {
        renderSummary('task-info', [
            { label: '执行目标', value: '未选择' },
            { label: '任务类型', value: '未开始' },
            { label: '课程ID', value: '-' },
            { label: '班级ID', value: '-' }
        ]);
    }
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
                await enterWorkspace(result.user);
                resetQrState();
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

function resetWorkspaceState() {
    currentUser = null;
    currentCourse = null;
    currentTaskSelection = null;
    currentTaskRunning = false;
    if (taskStatusTimer) {
        clearInterval(taskStatusTimer);
        taskStatusTimer = null;
    }

    const courseEmpty = document.getElementById('course-empty');
    const courseDetail = document.getElementById('course-detail');
    const examsList = document.getElementById('exams-list');
    const coursesList = document.getElementById('courses-list');
    const log = document.getElementById('task-log');
    const examCount = document.getElementById('exam-count');

    if (courseEmpty) courseEmpty.style.display = 'block';
    if (courseDetail) courseDetail.style.display = 'none';
    if (examsList) examsList.innerHTML = '';
    if (coursesList) coursesList.innerHTML = '';
    if (log) log.innerHTML = '<div class="log-entry"><span class="log-info">等待任务开始...</span></div>';
    if (examCount) examCount.textContent = '未加载';

    updateTaskStatusUI(false, null);
}

async function enterWorkspace(user) {
    currentUser = user;
    renderAccountInfo(user);
    showMainSection('workspace');
    showWorkspaceView('courses-view');
    await Promise.all([loadCourses(), loadConfig()]);
    await refreshTaskStatus(false);
    if (taskStatusTimer) clearInterval(taskStatusTimer);
    taskStatusTimer = setInterval(() => refreshTaskStatus(false), 5000);
}

function renderAccountInfo(user) {
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

async function restoreLoginState() {
    const result = await apiRequest('/api/account/info', 'GET');
    if (result.logged_in && result.user) {
        await enterWorkspace(result.user);
    } else {
        showMainSection('login');
    }
}

async function loadCourses() {
    const list = document.getElementById('courses-list');
    if (!list) return;
    list.innerHTML = '<div class="empty-state">正在获取课程列表...</div>';

    const result = await apiRequest('/api/courses', 'GET');
    if (result.status !== 'success') {
        list.innerHTML = '<div class="empty-state">课程加载失败，请稍后重试。</div>';
        showAlert(result.message || '加载课程失败', 'error');
        return;
    }

    if (!result.courses.length) {
        list.innerHTML = '<div class="empty-state">当前账号暂无课程数据。</div>';
        return;
    }

    list.innerHTML = '';
    result.courses.forEach((course) => {
        const item = document.createElement('div');
        item.className = 'course-item';
        item.innerHTML = `
            <div class="course-topline">
                <div class="course-name">${escapeHtml(course.name)}</div>
                <span class="course-state">${escapeHtml(course.state)}</span>
            </div>
            <div class="course-teacher">教师：${escapeHtml(course.teacher_name || '未知')}</div>
            <div class="course-meta">课程ID：${escapeHtml(course.course_id)}</div>
        `;
        item.addEventListener('click', () => selectCourse(course, item));
        list.appendChild(item);
    });
}

function selectCourse(course, element = null) {
    currentCourse = course;
    currentTaskSelection = {
        type: 'chapter',
        name: course.name,
        course_id: course.course_id,
        class_id: course.class_id
    };

    document.querySelectorAll('.course-item').forEach((node) => node.classList.remove('active'));
    if (element) element.classList.add('active');

    const courseEmpty = document.getElementById('course-empty');
    const courseDetail = document.getElementById('course-detail');
    const examCount = document.getElementById('exam-count');
    const examsList = document.getElementById('exams-list');

    if (courseEmpty) courseEmpty.style.display = 'none';
    if (courseDetail) courseDetail.style.display = 'block';
    if (examCount) examCount.textContent = '未加载';
    if (examsList) examsList.innerHTML = '<div class="empty-state compact">点击“加载课程考试”查看当前课程考试。</div>';

    renderSummary('course-summary', [
        { label: '课程名称', value: course.name },
        { label: '授课教师', value: course.teacher_name || '未知' },
        { label: '课程状态', value: course.state },
        { label: '课程ID', value: course.course_id }
    ]);

    updateTaskStatusUI(currentTaskRunning, currentTaskSelection, '已选择课程，可执行章节任务或加载课程考试。');
}

async function loadExamsForCurrentCourse() {
    if (!currentCourse) {
        showAlert('请先选择课程', 'error');
        return;
    }

    const examCount = document.getElementById('exam-count');
    const list = document.getElementById('exams-list');
    if (examCount) examCount.textContent = '加载中';
    if (list) list.innerHTML = '<div class="empty-state compact">正在加载考试列表...</div>';

    const result = await apiRequest('/api/exams', 'GET', { course_id: currentCourse.course_id });
    if (result.status !== 'success') {
        if (examCount) examCount.textContent = '加载失败';
        if (list) list.innerHTML = '<div class="empty-state compact">考试加载失败。</div>';
        showAlert(result.message || '考试加载失败', 'error');
        return;
    }

    if (examCount) examCount.textContent = `${result.exams.length} 个考试`;
    if (!result.exams.length) {
        if (list) list.innerHTML = '<div class="empty-state compact">当前课程暂无考试，可直接执行章节任务。</div>';
        return;
    }

    list.innerHTML = '';
    result.exams.forEach((exam) => {
        const item = document.createElement('div');
        item.className = 'exam-item';
        item.innerHTML = `
            <div class="course-topline">
                <div class="course-name">${escapeHtml(exam.name)}</div>
                <span class="course-state">${escapeHtml(exam.status)}</span>
            </div>
            <div class="course-teacher">截止：${escapeHtml(exam.expire_time || '无')}</div>
            <div class="course-meta">点击切换到任务页执行</div>
        `;
        item.addEventListener('click', () => selectExam(exam));
        list.appendChild(item);
    });
}

function selectExam(exam) {
    currentTaskSelection = {
        ...exam,
        type: 'exam',
        name: exam.name,
        course_name: currentCourse ? currentCourse.name : ''
    };
    updateTaskStatusUI(currentTaskRunning, currentTaskSelection, '已选择考试任务，可直接开始执行。');
    showWorkspaceView('tasks-view');
}

async function loadConfig() {
    const result = await apiRequest('/api/config', 'GET');
    if (result.status !== 'success') {
        showAlert(result.message || '读取配置失败', 'error');
        return;
    }

    const cfg = result.config;
    document.getElementById('cfg-video-enable').checked = Boolean(cfg.video.enable);
    document.getElementById('cfg-video-wait').value = cfg.video.wait;
    document.getElementById('cfg-video-speed').value = cfg.video.speed;
    document.getElementById('cfg-video-report-rate').value = cfg.video.report_rate;
    document.getElementById('cfg-work-enable').checked = Boolean(cfg.work.enable);
    document.getElementById('cfg-work-export').checked = Boolean(cfg.work.export);
    document.getElementById('cfg-work-wait').value = cfg.work.wait;
    document.getElementById('cfg-work-fallback-fuzzer').checked = Boolean(cfg.work.fallback_fuzzer);
    document.getElementById('cfg-work-fallback-save').checked = Boolean(cfg.work.fallback_save);
    document.getElementById('cfg-document-enable').checked = Boolean(cfg.document.enable);
    document.getElementById('cfg-document-wait').value = cfg.document.wait;
    document.getElementById('cfg-exam-fallback-fuzzer').checked = Boolean(cfg.exam.fallback_fuzzer);
    document.getElementById('cfg-exam-persubmit-delay').value = cfg.exam.persubmit_delay;
    document.getElementById('cfg-exam-confirm-submit').checked = Boolean(cfg.exam.confirm_submit);
}

async function saveConfig() {
    const payload = {
        video: {
            enable: document.getElementById('cfg-video-enable').checked,
            wait: Number(document.getElementById('cfg-video-wait').value),
            speed: Number(document.getElementById('cfg-video-speed').value),
            report_rate: Number(document.getElementById('cfg-video-report-rate').value)
        },
        work: {
            enable: document.getElementById('cfg-work-enable').checked,
            export: document.getElementById('cfg-work-export').checked,
            wait: Number(document.getElementById('cfg-work-wait').value),
            fallback_fuzzer: document.getElementById('cfg-work-fallback-fuzzer').checked,
            fallback_save: document.getElementById('cfg-work-fallback-save').checked
        },
        document: {
            enable: document.getElementById('cfg-document-enable').checked,
            wait: Number(document.getElementById('cfg-document-wait').value)
        },
        exam: {
            fallback_fuzzer: document.getElementById('cfg-exam-fallback-fuzzer').checked,
            persubmit_delay: Number(document.getElementById('cfg-exam-persubmit-delay').value),
            confirm_submit: document.getElementById('cfg-exam-confirm-submit').checked
        }
    };

    const result = await apiRequest('/api/config', 'POST', payload);
    if (result.status === 'success') {
        showAlert('配置已保存');
        await loadConfig();
    } else {
        showAlert(result.message || '保存配置失败', 'error');
    }
}

async function startTask() {
    if (!currentTaskSelection) {
        showAlert('请先在课程列表中选择任务目标', 'error');
        showWorkspaceView('courses-view');
        return;
    }

    const log = document.getElementById('task-log');
    if (log) log.innerHTML = '';
    appendLog(`初始化任务: ${currentTaskSelection.name}`, 'info');

    const payload = {
        course_id: currentTaskSelection.course_id,
        type: currentTaskSelection.type
    };
    if (currentTaskSelection.type === 'exam') {
        payload.exam = currentTaskSelection;
    }

    const result = await apiRequest('/api/task/start', 'POST', payload);
    if (result.status === 'started') {
        currentTaskRunning = true;
        appendLog('引擎启动成功，开始执行...', 'success');
        updateTaskStatusUI(true, currentTaskSelection, '任务已启动，正在执行。');
        await refreshTaskStatus(false);
    } else {
        showAlert(result.message || '启动失败', 'error');
    }
}

async function stopTask() {
    const result = await apiRequest('/api/task/stop', 'POST');
    if (result.status === 'success') {
        appendLog('正在发出停止指令...', 'warning');
        updateTaskStatusUI(false, currentTaskSelection, '已发送停止指令，等待任务线程结束。');
    } else {
        showAlert(result.message || '停止失败', 'error');
    }
}

async function refreshTaskStatus(showSuccessTip = true) {
    const result = await apiRequest('/api/task/status', 'GET');
    if (result.running) {
        const task = result.task || currentTaskSelection;
        currentTaskSelection = task || currentTaskSelection;
        updateTaskStatusUI(true, task, '后台任务正在执行中。');
    } else {
        updateTaskStatusUI(false, currentTaskSelection, currentTaskSelection ? '当前没有运行中的任务，可重新开始。' : '请选择课程或考试后开始执行。');
    }

    if (showSuccessTip) {
        showAlert('任务状态已刷新');
    }
}

document.addEventListener('DOMContentLoaded', () => {
    initSocket();

    const loginContainer = document.getElementById('login-container');
    const toQrBtn = document.getElementById('to-qr');
    const toPasswordBtn = document.getElementById('to-password');

    if (toQrBtn) {
        toQrBtn.addEventListener('click', () => {
            loginContainer.classList.add('right-panel-active');
            resetQrState();
            fetchQrCode();
        });
    }

    if (toPasswordBtn) {
        toPasswordBtn.addEventListener('click', () => {
            loginContainer.classList.remove('right-panel-active');
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
            await enterWorkspace(result.user);
        } else {
            showAlert(result.message || '登录失败', 'error');
        }
    });

    document.getElementById('btn-logout').addEventListener('click', async () => {
        await apiRequest('/api/logout', 'POST');
        if (loginContainer) loginContainer.classList.remove('right-panel-active');
        resetQrState();
        resetWorkspaceState();
        showMainSection('login');
    });

    document.querySelectorAll('.nav-chip').forEach((chip) => {
        chip.addEventListener('click', () => showWorkspaceView(chip.dataset.view));
    });

    document.getElementById('btn-refresh-courses').addEventListener('click', loadCourses);
    document.getElementById('btn-run-chapter-task').addEventListener('click', () => {
        if (!currentCourse) {
            showAlert('请先选择课程', 'error');
            return;
        }
        currentTaskSelection = {
            type: 'chapter',
            name: currentCourse.name,
            course_id: currentCourse.course_id,
            class_id: currentCourse.class_id
        };
        updateTaskStatusUI(currentTaskRunning, currentTaskSelection, '已切换为章节任务，前往任务页即可执行。');
        showWorkspaceView('tasks-view');
    });
    document.getElementById('btn-load-exams').addEventListener('click', loadExamsForCurrentCourse);

    document.getElementById('btn-reload-config').addEventListener('click', loadConfig);
    document.getElementById('btn-save-config').addEventListener('click', saveConfig);

    document.getElementById('btn-start-task').addEventListener('click', startTask);
    document.getElementById('btn-stop-task').addEventListener('click', stopTask);
    document.getElementById('btn-refresh-task-status').addEventListener('click', () => refreshTaskStatus(true));
    document.getElementById('btn-back-to-courses').addEventListener('click', () => showWorkspaceView('courses-view'));

    resetWorkspaceState();
    restoreLoginState();
});
