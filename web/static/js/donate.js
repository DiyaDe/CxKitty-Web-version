async function submitFeedback(event) {
    event.preventDefault();
    const subjectInput = document.getElementById('feedback-subject');
    const contactInput = document.getElementById('feedback-contact');
    const messageInput = document.getElementById('feedback-message');
    const submitBtn = document.getElementById('btn-send-feedback');

    const payload = {
        subject: subjectInput?.value?.trim() || '',
        contact: contactInput?.value?.trim() || '',
        message: messageInput?.value?.trim() || ''
    };

    if (!payload.message || payload.message.length < 5) {
        showAlert('反馈内容至少需要 5 个字符', 'error');
        return;
    }

    submitBtn.disabled = true;
    submitBtn.textContent = '发送中...';
    try {
        const result = await apiRequest('/api/feedback/send', 'POST', payload);
        if (result.status === 'success') {
            showAlert(result.message || '反馈已发送');
            if (messageInput) messageInput.value = '';
        } else {
            showAlert(result.message || '发送失败，请稍后重试', 'error');
        }
    } finally {
        submitBtn.disabled = false;
        submitBtn.innerHTML = '<i class="fa-solid fa-paper-plane" aria-hidden="true"></i> 发送反馈';
    }
}

document.addEventListener('DOMContentLoaded', async () => {
    const user = await bootWorkspacePage();
    if (!user) return;

    const form = document.getElementById('feedback-form');
    if (form) {
        form.addEventListener('submit', submitFeedback);
    }
});
