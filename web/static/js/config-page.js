const SEARCHER_DEFINITIONS = {
    EnncySearcher: {
        label: 'Enncy 题库',
        description: 'Enncy 题库搜索器，使用前需要注册并获取 Token。',
        links: [
            { label: '注册/登录', url: 'https://tk.enncy.cn/' }
        ],
        fields: [
            { key: 'token', label: 'Token', type: 'text', placeholder: 'xxx', required: true, help: 'Enncy 题库 Token。' }
        ]
    },
    CxSearcher: {
        label: '网课小工具(Go题)',
        description: '网课小工具(Go题)题库搜索器，需要获取授权 Token。',
        links: [
            { label: '获取 Token 教程', url: 'https://cx.icodef.com/1-UserGuide/1-6-gettoken.html#%E8%8E%B7%E5%8F%96token' }
        ],
        fields: [
            { key: 'token', label: 'Token', type: 'text', placeholder: 'xxx', required: true, help: '网课小工具(Go题)题库 Token。' }
        ]
    },
    TiKuHaiSearcher: {
        label: '题库海',
        description: '题库海搜索源，可不填 Token 先试用；如搜索失败再购买 Token。',
        links: [
            { label: '购买 Token', url: 'https://afdian.net/a/jiaoyu666' }
        ],
        fields: [
            { key: 'token', label: 'Token', type: 'text', placeholder: '可留空', keepEmpty: true, help: '没有 Token 时可留空；若搜索失败可前往链接购买。' }
        ]
    },
    MukeSearcher: {
        label: 'Muke 题库',
        description: 'Muke 题库无需额外配置，勾选后即可参与查询。',
        fields: []
    },
    LemonSearcher: {
        label: '柠檬题库',
        description: '柠檬题库搜索器，使用前需要注册并获取 Token。',
        links: [
            { label: '注册/获取 Token', url: 'https://www.lemtk.xyz' }
        ],
        fields: [
            { key: 'token', label: 'Token', type: 'text', placeholder: 'xxx', required: true, help: '柠檬题库平台的有效 Token。' }
        ]
    },
    OpenAISearcher: {
        label: 'OpenAI / ChatGPT',
        description: 'ChatGPT 在线答题：没有题库时可作为补充来源，成本与准确率取决于模型与提示词。',
        links: [
            { label: '模型文档', url: 'https://platform.openai.com/docs/models/continuous-model-upgrades' }
        ],
        fields: [
            { key: 'base_url', label: 'Base URL', type: 'text', placeholder: 'https://api.openai.com/v1/', default: 'https://api.openai.com/v1/', required: true, help: 'API 地址，可使用兼容 OpenAI 的代理地址（例如 https://api.chatnio.net/v1/）。' },
            { key: 'api_key', label: 'API Key', type: 'text', placeholder: 'sk-xxxxxxxxxxxxxx', required: true, help: 'OpenAI API Key。' },
            { key: 'model', label: '模型名称', type: 'text', placeholder: 'gpt-3.5-turbo', default: 'gpt-3.5-turbo', required: true, help: '调用的模型型号。' },
            {
                key: 'system_prompt',
                label: '系统提示词',
                type: 'textarea',
                default: '你是一位乐于回答问题的专家，每为用户回答一道问题你都会开心地获得5美元小费，对于用户提出的每一道问题，你只需要给出答案，不要输出任何其他的内容。\\n对于判断题，只需回复对/错。',
                help: '约束模型输出格式和答题行为。'
            },
            {
                key: 'prompt',
                label: '用户提示模板',
                type: 'textarea',
                default: '请回答下这个{type}：\\n{value}\\n{options}',
                help: '可使用 {type}、{value}、{options} 占位符。'
            }
        ]
    }
};

let latestConfig = null;

function cloneSearcherDefaults(type, initial = {}) {
    const def = SEARCHER_DEFINITIONS[type] || { fields: [] };
    const result = { type, enabled: Boolean(initial.enabled) };
    def.fields.forEach((field) => {
        if (field.type === 'pairs') {
            result[field.key] = initial[field.key] || {};
        } else if (field.type === 'number') {
            result[field.key] = initial[field.key] ?? (field.placeholder ? Number(field.placeholder) || 0 : 0);
        } else if (field.type === 'select') {
            result[field.key] = initial[field.key] ?? field.options[0];
        } else {
            const existing = initial[field.key];
            result[field.key] = (existing === undefined || existing === null) ? (field.default ?? '') : existing;
        }
    });
    return result;
}

function renderPairEditor(pairs = {}) {
    const entries = Object.entries(pairs);
    const rows = entries.length ? entries : [['', '']];
    return `
        <div class="pair-editor">
            ${rows.map(([key, value]) => `
                <div class="pair-row">
                    <input class="pair-key" type="text" placeholder="键" value="${escapeHtml(key)}">
                    <input class="pair-value" type="text" placeholder="值" value="${escapeHtml(typeof value === 'object' ? JSON.stringify(value) : String(value ?? ''))}">
                    <button type="button" class="btn btn-secondary btn-remove-pair">删除</button>
                </div>
            `).join('')}
            <button type="button" class="btn btn-secondary btn-add-pair">新增键值</button>
        </div>
    `;
}

function renderSearcherCard(searcher, index) {
    const firstType = Object.keys(SEARCHER_DEFINITIONS)[0];
    const type = searcher.type || firstType;
    const def = SEARCHER_DEFINITIONS[type] || SEARCHER_DEFINITIONS[firstType];
    const links = Array.isArray(def.links) ? def.links : [];
    return `
        <div class="searcher-card" data-index="${index}" data-type="${type}">
            <div class="searcher-card-header">
                <div>
                    <label class="searcher-check">
                        <input class="searcher-enabled" type="checkbox" ${searcher.enabled ? 'checked' : ''}>
                        <span>${escapeHtml(def.label)}</span>
                    </label>
                    <span class="meta-text">${escapeHtml(def.description || '')}</span>
                    ${links.length ? `
                        <div class="searcher-links">
                            ${links.map((link) => `<a href="${link.url}" target="_blank" rel="noopener noreferrer">${escapeHtml(link.label)}</a>`).join('')}
                        </div>
                    ` : ''}
                </div>
            </div>
            <div class="searcher-fields ${searcher.enabled ? '' : 'is-disabled'}">
                ${def.fields.map((field) => {
                    const value = searcher[field.key];
                    if (field.type === 'textarea') {
                        return `
                            <label class="searcher-field searcher-field-block">
                                <span>${escapeHtml(field.label)}</span>
                                <textarea data-field="${field.key}" placeholder="${escapeHtml(field.placeholder || '')}">${escapeHtml(value ?? '')}</textarea>
                                ${field.help ? `<small class="field-help">${escapeHtml(field.help)}</small>` : ''}
                            </label>
                        `;
                    }
                    if (field.type === 'pairs') {
                        return `
                            <div class="searcher-field searcher-field-block" data-field="${field.key}">
                                <span>${escapeHtml(field.label)}</span>
                                ${renderPairEditor(value || {})}
                                ${field.help ? `<small class="field-help">${escapeHtml(field.help)}</small>` : ''}
                            </div>
                        `;
                    }
                    if (field.type === 'select') {
                        return `
                            <label class="searcher-field">
                                <span>${escapeHtml(field.label)}</span>
                                <select data-field="${field.key}">
                                    ${field.options.map((option) => `<option value="${option}" ${option === value ? 'selected' : ''}>${option}</option>`).join('')}
                                </select>
                                <small class="field-help">${escapeHtml(field.help || '')}${field.required ? ' 必填。' : ''}</small>
                            </label>
                        `;
                    }
                    return `
                        <label class="searcher-field">
                            <span>${escapeHtml(field.label)}${field.required ? ' *' : ''}</span>
                            <input data-field="${field.key}" type="${field.type}" value="${escapeHtml(value ?? '')}" placeholder="${escapeHtml(field.placeholder || '')}">
                            <small class="field-help">${escapeHtml(field.help || '')}${field.required ? ' 必填。' : ''}</small>
                        </label>
                    `;
                }).join('')}
                ${!def.fields.length ? '<div class="empty-state compact">该搜索器无额外配置项，勾选后即可参与查询。</div>' : ''}
            </div>
        </div>
    `;
}

function setSearchersState(searchers) {
    const editor = document.getElementById('searchers-editor');
    const enabledByType = new Map((searchers || []).map((item) => [item.type, item]));
    const allCards = Object.keys(SEARCHER_DEFINITIONS).map((type) => {
        const stored = enabledByType.get(type) || { type, enabled: false };
        return cloneSearcherDefaults(type, { ...stored, enabled: Boolean(enabledByType.get(type)) });
    });
    editor.innerHTML = allCards.map((item, index) => renderSearcherCard(item, index)).join('');
}

function bindSearcherEditor() {
    const editor = document.getElementById('searchers-editor');

    editor.addEventListener('click', (event) => {
        const card = event.target.closest('.searcher-card');
        if (!card) return;

        if (event.target.classList.contains('btn-add-pair')) {
            const fieldBlock = event.target.closest('[data-field]');
            fieldBlock.querySelector('.pair-editor').insertAdjacentHTML('afterbegin', `
                <div class="pair-row">
                    <input class="pair-key" type="text" placeholder="键" value="">
                    <input class="pair-value" type="text" placeholder="值" value="">
                    <button type="button" class="btn btn-secondary btn-remove-pair">删除</button>
                </div>
            `);
            return;
        }

        if (event.target.classList.contains('btn-remove-pair')) {
            const row = event.target.closest('.pair-row');
            const parent = row.parentElement;
            row.remove();
            if (!parent.querySelector('.pair-row')) {
                parent.insertAdjacentHTML('afterbegin', `
                    <div class="pair-row">
                        <input class="pair-key" type="text" placeholder="键" value="">
                        <input class="pair-value" type="text" placeholder="值" value="">
                        <button type="button" class="btn btn-secondary btn-remove-pair">删除</button>
                    </div>
                `);
            }
        }
    });

    editor.addEventListener('change', (event) => {
        const card = event.target.closest('.searcher-card');
        if (!card) return;
        if (event.target.classList.contains('searcher-enabled')) {
            card.querySelector('.searcher-fields').classList.toggle('is-disabled', !event.target.checked);
        }
    });
}

function collectPairs(fieldBlock) {
    const result = {};
    fieldBlock.querySelectorAll('.pair-row').forEach((row) => {
        const key = row.querySelector('.pair-key').value.trim();
        const valueRaw = row.querySelector('.pair-value').value.trim();
        if (!key) return;
        let value = valueRaw;
        if (valueRaw.startsWith('{') || valueRaw.startsWith('[')) {
            try {
                value = JSON.parse(valueRaw);
            } catch (_error) {
                value = valueRaw;
            }
        }
        result[key] = value;
    });
    return result;
}

function collectSearchers() {
    return Array.from(document.querySelectorAll('.searcher-card')).map((card) => {
        const enabled = card.querySelector('.searcher-enabled').checked;
        const type = card.dataset.type;
        const def = SEARCHER_DEFINITIONS[type];
        const item = { type };

        if (!enabled) return null;

        def.fields.forEach((field) => {
            if (field.type === 'pairs') {
                const block = card.querySelector(`[data-field="${field.key}"]`);
                const pairs = collectPairs(block);
                if (Object.keys(pairs).length) item[field.key] = pairs;
                return;
            }

            const input = card.querySelector(`[data-field="${field.key}"]`);
            if (!input) return;
            let value = input.value;
            if (field.type === 'number') {
                value = Number(value);
            }
            if (field.required && (value === '' || value === null || Number.isNaN(value))) {
                throw new Error(`${def.label} 的 ${field.label} 为必填项`);
            }
            if (field.keepEmpty) {
                if (field.type === 'number' && Number.isNaN(value)) value = 0;
                item[field.key] = value;
                return;
            }
            if (value !== '' && value !== null && !Number.isNaN(value)) {
                item[field.key] = value;
            }
        });

        return item;
    }).filter(Boolean);
}

function populateConfigForm(cfg) {
    latestConfig = cfg || latestConfig;
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

    setSearchersState(cfg.searchers || []);
}

function collectConfigForm() {
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
        searchers: collectSearchers()
    };
    if (latestConfig && latestConfig.exam) {
        payload.exam = latestConfig.exam;
    }
    return payload;
}

async function loadConfig() {
    const result = await apiRequest('/api/config', 'GET');
    if (result.status !== 'success') {
        showAlert(result.message || '读取配置失败', 'error');
        return;
    }
    latestConfig = result.config;
    populateConfigForm(result.config);
}

async function saveConfig() {
    try {
        const payload = collectConfigForm();
        const result = await apiRequest('/api/config', 'POST', payload);
        if (result.status === 'success') {
            showAlert('配置已保存');
            populateConfigForm(result.config);
        } else {
            const message = result.message || '保存配置失败';
            if (String(message).includes('搜索器')) {
                showModal({
                    title: '保存失败',
                    message,
                    primaryText: '去配置搜索器',
                    secondaryText: '我知道了',
                    onPrimary: () => {
                        document.getElementById('searchers-editor')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
                    }
                });
            } else {
                showModal({
                    title: '保存失败',
                    message,
                    primaryText: '我知道了',
                    secondaryText: ''
                });
            }
            showAlert(message, 'error');
        }
    } catch (error) {
        const message = error.message || '配置格式不正确';
        showModal({
            title: '保存失败',
            message,
            primaryText: '我知道了',
            secondaryText: ''
        });
        showAlert(message, 'error');
    }
}

function removeExamConfigCard() {
    document.querySelectorAll('.config-card h4').forEach((heading) => {
        const text = (heading.textContent || '').trim();
        if (text === '考试任务') {
            const card = heading.closest('.config-card');
            if (card) card.remove();
        }
    });
}

document.addEventListener('DOMContentLoaded', async () => {
    const user = await bootWorkspacePage();
    if (!user) return;
    bindSearcherEditor();
    removeExamConfigCard();
    document.getElementById('btn-reload-config').addEventListener('click', loadConfig);
    document.getElementById('btn-save-config').addEventListener('click', saveConfig);
    await loadConfig();
});
