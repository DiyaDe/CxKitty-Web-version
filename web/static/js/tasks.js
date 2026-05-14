let socket = null;
let taskStatusTimer = null;
let currentTaskSelection = null;
let currentTaskRunning = false;
let latestProgress = null;
let latestQueue = null;
let configIssue = '';
let lastConfigIssueShown = '';
let startActionPending = false;
let currentOwnerId = null;
let renderedLogOwnerId = null;
let hasLiveLogContent = false;

function promptConfigIssue(message) {
    showModal({
        title: '配置未完成',
        message: message || '请先完成配置后再开始任务。',
        primaryText: '前往配置页',
        secondaryText: '稍后再说',
        onPrimary: () => {
            window.location.href = '/settings';
        }
    });
}

function formatLogTime(timestamp) {
    const raw = Number(timestamp || 0);
    if (!Number.isFinite(raw) || raw <= 0) {
        return new Date().toLocaleTimeString();
    }
    const ms = raw > 1e12 ? raw : raw * 1000;
    return new Date(ms).toLocaleTimeString();
}

function setLogPlaceholder(message = '等待任务开始...') {
    const logContainer = document.getElementById('task-log');
    if (!logContainer) return;
    logContainer.innerHTML = `<div class="log-entry log-placeholder"><span class="log-info">${escapeHtml(message)}</span></div>`;
    hasLiveLogContent = false;
}

function appendLogEntry(entry) {
    const logContainer = document.getElementById('task-log');
    if (!logContainer || !entry) return;

    const message = entry.message;
    const level = entry.level || 'info';
    if (entry.session_id) {
        renderedLogOwnerId = String(entry.session_id);
    }

    const placeholder = logContainer.querySelector('.log-placeholder');
    if (placeholder) {
        logContainer.innerHTML = '';
    }

    if (message === 'task_finished') {
        const entry = document.createElement('div');
        entry.className = 'log-entry';
        entry.innerHTML = '<span class="log-success">=== 当前任务已结束 ===</span>';
        logContainer.appendChild(entry);
        logContainer.scrollTop = logContainer.scrollHeight;
        hasLiveLogContent = true;
        return;
    }

    const row = document.createElement('div');
    row.className = 'log-entry';
    row.innerHTML = `<span class="log-${level}">[${formatLogTime(entry.timestamp)}] ${escapeHtml(message)}</span>`;
    logContainer.appendChild(row);
    logContainer.scrollTop = logContainer.scrollHeight;
    hasLiveLogContent = true;
}

function appendLog(message, level = 'info') {
    appendLogEntry({
        session_id: currentOwnerId,
        message,
        level,
        timestamp: Date.now()
    });
}

function replaceLogHistory(entries, ownerId = null) {
    const logContainer = document.getElementById('task-log');
    if (!logContainer) return;
    logContainer.innerHTML = '';
    renderedLogOwnerId = ownerId ? String(ownerId) : renderedLogOwnerId;
    hasLiveLogContent = false;
    if (!Array.isArray(entries) || !entries.length) {
        setLogPlaceholder('等待任务开始...');
        return;
    }
    entries.forEach((item) => appendLogEntry(item));
}

function shouldHydrateLogs(ownerId) {
    const logContainer = document.getElementById('task-log');
    if (!logContainer) return false;
    if (!logContainer.children.length) return true;
    if (logContainer.querySelector('.log-placeholder')) return true;
    if (ownerId && renderedLogOwnerId && String(ownerId) !== String(renderedLogOwnerId)) return true;
    return !hasLiveLogContent;
}

function initSocket() {
    socket = io();
    socket.on('connect', () => {
        socket.emit('join', {});
    });
    socket.on('joined', (data) => {
        if (data?.owner_id) {
            currentOwnerId = String(data.owner_id);
        }
    });
    socket.on('task_log', (data) => {
        if (!data) return;
        if (currentOwnerId && data.session_id && String(data.session_id) !== String(currentOwnerId)) return;
        if (data.session_id) {
            currentOwnerId = String(data.session_id);
        }
        appendLogEntry(data);
    });
    socket.on('task_progress', (data) => {
        if (!data) return;
        if (currentOwnerId && data.session_id && String(data.session_id) !== String(currentOwnerId)) return;
        if (data.session_id) {
            currentOwnerId = String(data.session_id);
        }
        latestProgress = data;
        if (data && data.task) {
            currentTaskSelection = data.task;
            setTaskSelection(data.task);
        }
        updateTaskStatusUI(Boolean(data && ['running', 'starting', 'stopping'].includes(data.status)), currentTaskSelection, data?.message || '');
        renderProgress(data);
    });
}

function renderQueue(queue) {
    const queueStatus = document.getElementById('queue-status-text');
    const queueMeta = document.getElementById('queue-meta');
    latestQueue = queue || latestQueue;
    const info = latestQueue || {};
    const running = Boolean(info.running);
    const total = Number(info.total || 0);
    const completed = Number(info.completed || 0);
    const failed = Number(info.failed || 0);
    const pending = Number(info.pending || 0);
    const current = info.current;
    const currentName = current?.name || current?.exam?.name || '-';
    const items = Array.isArray(info.items) ? info.items : [];
    if (queueStatus) {
        queueStatus.textContent = info.last_message || (running ? '队列运行中' : '队列空闲');
    }
    if (queueMeta) {
        const stats = [
            `<span>总任务：${escapeHtml(total)}</span>`,
            `<span>已完成：${escapeHtml(completed)}</span>`,
            `<span>失败：${escapeHtml(failed)}</span>`,
            `<span>待执行：${escapeHtml(pending)}</span>`,
            `<span>当前：${escapeHtml(currentName)}</span>`
        ].join('');

        let listHtml = '';
        if (items.length) {
            listHtml = `<div class="queue-list">${
                items.map((item) => {
                    const t = item?.enqueued_at ? new Date(Number(item.enqueued_at) * 1000).toLocaleTimeString() : '--:--:--';
                    const name = item?.name || `课程 ${item?.course_id || ''}`.trim();
                    const qid = item?.queue_id || '';
                    return `<div class="queue-item">
                        <span class="queue-time">${escapeHtml(t)}</span>
                        <span class="queue-name">${escapeHtml(name)}</span>
                        <button class="queue-remove" type="button" data-remove-queue-id="${escapeHtml(qid)}">移除</button>
                    </div>`;
                }).join('')
            }</div>`;
        }
        queueMeta.innerHTML = stats + listHtml;
    }
}

function renderProgress(progress) {
    const percentText = document.getElementById('task-progress-text');
    const progressBar = document.getElementById('task-progress-bar');
    const progressMeta = document.getElementById('task-progress-meta');

    const percent = Number(progress?.percent ?? 0);
    percentText.textContent = `${percent}%`;
    progressBar.style.width = `${Math.max(0, Math.min(percent, 100))}%`;

    const lines = [];
    if (progress?.total_chapters) {
        lines.push(`<span>章节完成：${escapeHtml(progress.finished_chapters || 0)} / ${escapeHtml(progress.total_chapters)}</span>`);
    }
    if (progress?.current_chapter_name) {
        lines.push(`<span>当前章节：${escapeHtml((progress.current_chapter_label ? `${progress.current_chapter_label} ` : '') + progress.current_chapter_name)}</span>`);
    }
    if (progress?.current_point_title) {
        lines.push(`<span>当前任务点：${escapeHtml(progress.current_point_type || '')} ${escapeHtml(progress.current_point_title)}</span>`);
    }
    if (!lines.length) {
        lines.push('<span>暂无详细进度数据</span>');
    }
    progressMeta.innerHTML = lines.join('');
}

function updateTaskStatusUI(running, task = null, statusText = '') {
    const indicator = document.getElementById('task-running-indicator');
    const statusEl = document.getElementById('task-status-text');
    const startBtn = document.getElementById('btn-start-task');
    const stopBtn = document.getElementById('btn-stop-task');

    currentTaskRunning = Boolean(running);
    if (startBtn) {
        startBtn.classList.toggle('is-pending', Boolean(startActionPending));
        startBtn.setAttribute('aria-busy', startActionPending ? 'true' : 'false');
    }
    if (indicator) {
        indicator.textContent = running ? '运行中' : '空闲中';
        indicator.classList.toggle('is-running', Boolean(running));
    }
    if (statusEl) {
        const queuePending = Number(latestQueue?.pending || 0);
        const idleHint = queuePending > 0
            ? '已加入队列，可点击开始任务启动队列。'
            : '请先在课程列表中选择课程或考试，或将课程加入队列。';
        statusEl.textContent = statusText || (running ? '任务正在后台执行，请关注右侧日志输出。' : idleHint);
    }
    const queuePending = Number(latestQueue?.pending || 0);
    if (startBtn) startBtn.disabled = Boolean(startActionPending || running || configIssue || (!currentTaskSelection && queuePending <= 0));
    if (stopBtn) stopBtn.disabled = !running;

    if (task) {
        renderSummary('task-info', [
            { label: '执行目标', value: task.name || '未选择' },
            { label: '任务类型', value: formatTaskType(task.type) },
            { label: '课程ID', value: task.course_id || '-' },
            { label: '班级ID', value: task.class_id || '-' }
        ]);
    } else {
        renderSummary('task-info', [
            { label: '执行目标', value: '未选择' },
            { label: '任务类型', value: '未开始' },
            { label: '课程ID', value: '-' },
            { label: '班级ID', value: '-' }
        ]);
    }
}

function restoreTaskSelection() {
    currentTaskSelection = getTaskSelection();
    updateTaskStatusUI(false, currentTaskSelection, currentTaskSelection ? '已恢复上次选择，可直接开始任务。' : '请先在课程列表中选择课程或考试。');
}

async function refreshTaskStatus(showTip = true) {
    const result = await apiRequest('/api/task/status', 'GET');
    currentOwnerId = result.owner_id ? String(result.owner_id) : currentOwnerId;
    if (Array.isArray(result.recent_logs) && result.recent_logs.length && shouldHydrateLogs(currentOwnerId)) {
        replaceLogHistory(result.recent_logs, currentOwnerId);
    } else if (!result.running && !result.waiting && !hasLiveLogContent) {
        setLogPlaceholder('等待任务开始...');
    } else if (result.running && !hasLiveLogContent && (!Array.isArray(result.recent_logs) || !result.recent_logs.length)) {
        setLogPlaceholder('任务执行中，等待日志同步...');
    }
    renderQueue(result.queue || null);
    configIssue = result.config_issue || '';
    if (result.waiting && result.waiting.task) {
        currentTaskSelection = result.waiting.task;
        setTaskSelection(currentTaskSelection);
    }
    if (!currentTaskSelection && result.selected_task) {
        currentTaskSelection = result.selected_task;
        setTaskSelection(currentTaskSelection);
    }
    if (result.running) {
        currentTaskSelection = result.task || currentTaskSelection;
        if (currentTaskSelection) setTaskSelection(currentTaskSelection);
        latestProgress = result.progress || latestProgress;
        updateTaskStatusUI(true, currentTaskSelection, result.progress?.message || '后台任务正在执行中。');
        renderProgress(result.progress);
    } else {
        if (configIssue) {
            updateTaskStatusUI(false, currentTaskSelection, configIssue);
            if (configIssue !== lastConfigIssueShown) {
                lastConfigIssueShown = configIssue;
                promptConfigIssue(configIssue);
            }
        } else {
            if (result.waiting) {
                const ahead = Number(result.waiting.ahead || 0);
                const pos = Number(result.waiting.position || 0);
                updateTaskStatusUI(false, currentTaskSelection, `任务等待中：前方还有 ${ahead} 人（位置 ${pos}）。`);
            } else {
                updateTaskStatusUI(false, currentTaskSelection, currentTaskSelection ? '当前没有运行中的任务，可重新开始。' : '请先在课程列表中选择课程或考试。');
            }
        }
        renderProgress(result.progress || latestProgress);
    }

    if (showTip) {
        showAlert('任务状态已刷新');
    }
}

async function startQueue() {
    if (startActionPending) return;
    if (configIssue) {
        promptConfigIssue(configIssue);
        return;
    }
    startActionPending = true;
    updateTaskStatusUI(currentTaskRunning, currentTaskSelection);
    try {
        const result = await apiRequest('/api/task/queue/start', 'POST');
        if (result.status === 'started') {
            appendLog('任务队列已启动，正在按顺序执行课程。', 'success');
            renderQueue(result.queue || null);
            updateTaskStatusUI(true, currentTaskSelection, result.queue?.last_message || '队列运行中');
        } else if (result.status === 'queued') {
            appendLog(result.message || '队列已进入全局等待队列', 'warning');
            renderQueue(result.queue || null);
            updateTaskStatusUI(false, currentTaskSelection, result.message || '队列等待中');
        } else {
            showAlert(result.message || '启动队列失败', 'error');
        }
    } finally {
        startActionPending = false;
        updateTaskStatusUI(currentTaskRunning, currentTaskSelection);
    }
}

async function clearQueue() {
    const result = await apiRequest('/api/task/queue/clear', 'POST');
    if (result.status === 'success') {
        renderQueue(result.queue || null);
        showAlert('队列已清空');
    } else {
        showAlert(result.message || '清空队列失败', 'error');
    }
}

async function startTask() {
    if (startActionPending) return;
    if (configIssue) {
        promptConfigIssue(configIssue);
        return;
    }
    const queuePending = Number(latestQueue?.pending || 0);
    if (!currentTaskSelection && queuePending > 0) {
        await startQueue();
        return;
    }
    if (!currentTaskSelection) {
        showAlert('请先在课程列表中选择任务目标，或先将课程加入队列', 'error');
        return;
    }

    document.getElementById('task-log').innerHTML = '';
    appendLog(`初始化任务: ${currentTaskSelection.name}`, 'info');

    const payload = {
        course_id: currentTaskSelection.course_id,
        type: currentTaskSelection.type
    };
    if (currentTaskSelection.type === 'exam') {
        payload.exam = currentTaskSelection;
    }

    startActionPending = true;
    updateTaskStatusUI(currentTaskRunning, currentTaskSelection);
    try {
        const result = await apiRequest('/api/task/start', 'POST', payload);
        if (result.status === 'started') {
            currentTaskRunning = true;
            appendLog('引擎启动成功，开始执行...', 'success');
            updateTaskStatusUI(true, currentTaskSelection, '任务已启动，正在执行。');
            latestProgress = result.progress || latestProgress;
        } else if (result.status === 'queued') {
            appendLog(result.message || '并发已满，任务已进入全局等待队列', 'warning');
            updateTaskStatusUI(false, currentTaskSelection, result.message || '任务等待中');
        } else {
            showAlert(result.message || '启动失败', 'error');
        }
    } finally {
        startActionPending = false;
        updateTaskStatusUI(currentTaskRunning, currentTaskSelection);
    }
}

async function stopTask() {
    const result = await apiRequest('/api/task/stop', 'POST');
    if (result.status === 'success') {
        currentTaskRunning = false;
        renderQueue(result.queue || null);
        appendLog('正在发出停止指令...', 'warning');
        updateTaskStatusUI(false, currentTaskSelection, result.message || '已发送停止指令，等待任务线程结束。');
    } else {
        showAlert(result.message || '停止失败', 'error');
    }
}

document.addEventListener('DOMContentLoaded', async () => {
    const user = await bootWorkspacePage();
    if (!user) return;
    initSocket();

    document.getElementById('btn-start-task').addEventListener('click', startTask);
    document.getElementById('btn-stop-task').addEventListener('click', stopTask);
    document.getElementById('btn-refresh-task-status').addEventListener('click', () => refreshTaskStatus(true));
    document.getElementById('btn-clear-task-selection').addEventListener('click', async () => {
        clearTaskSelection();
        currentTaskSelection = null;
        latestProgress = null;
        await apiRequest('/api/task/selection/clear', 'POST');
        updateTaskStatusUI(false, null, '当前任务选择已清空。');
        renderProgress(null);
    });
    document.getElementById('queue-meta').addEventListener('click', async (event) => {
        const btn = event.target.closest('[data-remove-queue-id]');
        if (!btn) return;
        const queueId = btn.getAttribute('data-remove-queue-id');
        if (!queueId) return;
        const result = await apiRequest('/api/task/queue/remove', 'POST', { queue_id: queueId });
        if (result.status !== 'success') {
            showAlert(result.message || '移除失败', 'error');
            return;
        }
        renderQueue(result.queue || null);
        showAlert('已从队列移除');
    });

    setLogPlaceholder('等待任务开始...');
    restoreTaskSelection();
    renderProgress(null);
    renderQueue(null);
    await refreshTaskStatus(false);

    taskStatusTimer = setInterval(() => refreshTaskStatus(false), 5000);
});
