let allCourses = [];

const courseFilters = {
    state: '',
    teacher: ''
};

function normalizeText(value) {
    return String(value ?? '').trim().toLowerCase();
}

function applyCourseFilters(list) {
    const state = normalizeText(courseFilters.state);
    const teacher = normalizeText(courseFilters.teacher);

    return list.filter((course) => {
        const courseName = normalizeText(course.name);
        const courseTeacher = normalizeText(course.teacher_name || '未知');
        const courseId = normalizeText(course.course_id);
        const courseState = normalizeText(course.state);

        if (state && courseState !== state) return false;
        if (teacher && courseTeacher !== teacher) return false;
        return true;
    });
}

function updateCourseStats(total, shown) {
    const stats = document.getElementById('courses-stats');
    if (!stats) return;
    stats.textContent = `共 ${total} 门，显示 ${shown} 门`;
}

function ensureFilterOptions() {
    const stateSelect = document.getElementById('course-filter-state');
    const teacherSelect = document.getElementById('course-filter-teacher');
    if (!stateSelect || !teacherSelect) return;

    const states = Array.from(new Set(allCourses.map((c) => String(c.state || '').trim()).filter(Boolean)));
    const teachers = Array.from(new Set(allCourses.map((c) => String(c.teacher_name || '未知').trim()).filter(Boolean)));
    states.sort((a, b) => a.localeCompare(b, 'zh-CN'));
    teachers.sort((a, b) => a.localeCompare(b, 'zh-CN'));

    stateSelect.innerHTML = '<option value="">全部状态</option>' + states.map((value) => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`).join('');
    teacherSelect.innerHTML = '<option value="">全部教师</option>' + teachers.map((value) => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`).join('');

    stateSelect.value = courseFilters.state;
    teacherSelect.value = courseFilters.teacher;
}

function clearCourseFilters() {
    courseFilters.state = '';
    courseFilters.teacher = '';
    const stateSelect = document.getElementById('course-filter-state');
    const teacherSelect = document.getElementById('course-filter-teacher');
    if (stateSelect) stateSelect.value = '';
    if (teacherSelect) teacherSelect.value = '';
    renderCourses();
}

function renderCourses() {
    const list = document.getElementById('courses-list');
    if (!list) return;

    const filtered = applyCourseFilters(allCourses);
    updateCourseStats(allCourses.length, filtered.length);

    if (allCourses.length && filtered.length === 0) {
        list.innerHTML = `
            <div class="empty-state">
                <div class="empty-title">没有匹配的课程</div>
                <div class="empty-desc">请调整搜索关键词或筛选条件后重试。</div>
                <div class="inline-actions" style="justify-content:center;">
                    <button id="btn-clear-course-filters-inline" class="btn btn-secondary" type="button">清空筛选</button>
                </div>
            </div>
        `;
        return;
    }

    if (!filtered.length) {
        list.innerHTML = '<div class="empty-state">当前账号暂无课程数据。</div>';
        return;
    }

    list.innerHTML = '';
    filtered.forEach((course) => {
        const item = document.createElement('div');
        item.className = 'course-item';

        const badge = String(course.name || '?').trim().slice(0, 1) || '?';
        const cacheKey = String(course.course_id);

        item.innerHTML = `
            <div class="course-card" data-course-id="${escapeHtml(cacheKey)}">
                <div class="course-card-top">
                    <div class="course-badge">${escapeHtml(badge)}</div>
                    <div class="course-main">
                        <div class="course-topline">
                            <div class="course-name">${escapeHtml(course.name)}</div>
                            <span class="course-state">${escapeHtml(course.state)}</span>
                        </div>
                        <div class="course-teacher">教师：${escapeHtml(course.teacher_name || '未知')}</div>
                        <div class="course-meta">课程ID：${escapeHtml(course.course_id)}</div>
                    </div>
                </div>

                <div class="course-actions">
                    <button class="btn btn-primary" type="button" data-action="start">开始任务</button>
                    <button class="btn btn-secondary" type="button" data-action="queue">加入队列</button>
                </div>
            </div>
        `;

        const card = item.querySelector('.course-card');
        const btnStart = item.querySelector('[data-action="start"]');
        const btnQueue = item.querySelector('[data-action="queue"]');

        btnStart.addEventListener('click', async () => {
            const selection = {
                type: 'chapter',
                name: course.name,
                course_id: course.course_id,
                class_id: course.class_id
            };
            setTaskSelection(selection);
            const saved = await apiRequest('/api/task/selection', 'POST', selection);
            if (saved.status !== 'success') {
                showAlert(saved.message || '同步选择失败', 'error');
            }
            window.location.href = '/tasks';
        });

        btnQueue.addEventListener('click', async () => {
            const result = await apiRequest('/api/task/queue/add', 'POST', {
                type: 'chapter',
                name: course.name,
                course_id: course.course_id,
                class_id: course.class_id
            });
            if (result.status !== 'success') {
                showAlert(result.message || '加入队列失败', 'error');
                return;
            }
            const pending = result.queue?.pending ?? 0;
            showAlert(`已加入队列，当前待执行 ${pending} 个任务`);
        });

        card.addEventListener('click', (event) => {
            if (event.target.closest('button')) return;
            item.classList.toggle('active');
        });

        list.appendChild(item);
    });
}

async function loadCourses() {
    const list = document.getElementById('courses-list');
    list.innerHTML = '<div class="empty-state">正在获取课程列表...</div>';
    updateCourseStats(0, 0);

    const result = await apiRequest('/api/courses', 'GET');
    if (result.status !== 'success') {
        list.innerHTML = `
            <div class="empty-state">
                <div class="empty-title">课程加载失败</div>
                <div class="empty-desc">请检查网络或稍后重试。</div>
                <div class="inline-actions" style="justify-content:center;">
                    <button id="btn-retry-courses" class="btn btn-primary" type="button">重试加载</button>
                </div>
            </div>
        `;
        updateCourseStats(0, 0);
        showAlert(result.message || '加载课程失败', 'error');
        return;
    }

    allCourses = Array.isArray(result.courses) ? result.courses : [];
    ensureFilterOptions();
    renderCourses();
}

document.addEventListener('DOMContentLoaded', async () => {
    const user = await bootWorkspacePage();
    if (!user) return;
    document.getElementById('btn-refresh-courses').addEventListener('click', loadCourses);
    document.getElementById('btn-clear-course-filters').addEventListener('click', clearCourseFilters);

    const stateSelect = document.getElementById('course-filter-state');
    const teacherSelect = document.getElementById('course-filter-teacher');

    if (stateSelect) {
        stateSelect.addEventListener('change', () => {
            courseFilters.state = stateSelect.value;
            renderCourses();
        });
    }
    if (teacherSelect) {
        teacherSelect.addEventListener('change', () => {
            courseFilters.teacher = teacherSelect.value;
            renderCourses();
        });
    }

    document.getElementById('courses-list').addEventListener('click', (event) => {
        if (event.target && event.target.id === 'btn-clear-course-filters-inline') {
            clearCourseFilters();
        }
        if (event.target && event.target.id === 'btn-retry-courses') {
            loadCourses();
        }
    });
    await loadCourses();
});
