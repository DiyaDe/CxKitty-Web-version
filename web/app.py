import os
import sys
import threading
import time
import uuid
from pathlib import Path
import re
import logging
import json
import smtplib
from datetime import datetime
from email.message import EmailMessage
from collections import deque

from flask import Flask, jsonify, redirect, render_template, request, send_from_directory, session, url_for
from flask_socketio import SocketIO, emit, join_room

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from cxapi import ChaoXingAPI, ChapterContainer
from cxapi.exam import ExamDto
from logger import clear_log_session_id, get_log_session_id, set_log_emitter, set_log_filename, set_log_session_id
from web import task_store

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOG_ROOT = PROJECT_ROOT / "logs"
task_store.init_db(PROJECT_ROOT)


def _to_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}

app = Flask(__name__, template_folder="static/html", static_folder="static")
_secret_from_env = os.environ.get("FLASK_SECRET_KEY")
if _secret_from_env:
    app.config["SECRET_KEY"] = _secret_from_env
else:
    _secret_file = PROJECT_ROOT / ".flask_secret_key"
    try:
        if _secret_file.exists():
            app.config["SECRET_KEY"] = _secret_file.read_text(encoding="utf8").strip()
        else:
            _secret_value = uuid.uuid4().hex
            _secret_file.write_text(_secret_value, encoding="utf8")
            app.config["SECRET_KEY"] = _secret_value
    except Exception:
        app.config["SECRET_KEY"] = uuid.uuid4().hex
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")


def _emit_log(session_id: str, name: str, level: str, message: str):
    text = f"[{name}] {message}" if name else message
    _emit_owner_log_event(session_id, text, level)


set_log_emitter(_emit_log)

api_instances = {}
task_threads = {}
task_log_local = threading.local()

_server_conf = config.get_default_conf().get("server") or {}
MAX_CONCURRENT_USERS = int(os.getenv("MAX_CONCURRENT_USERS") or _server_conf.get("max_concurrent_users") or 60)
global_queue_lock = threading.Lock()
global_queue_cond = threading.Condition(global_queue_lock)
owner_last_client_id = {}
user_worker_lock = threading.Lock()
user_workers = {}
user_workers_active = set()
start_request_lock = threading.Lock()
start_request_inflight = set()
log_history_lock = threading.Lock()
owner_log_history = {}
LOG_HISTORY_LIMIT = 200


def _store_owner_log(owner_id: str, message: str, level: str = "info", timestamp: float | None = None) -> dict | None:
    owner_id = str(owner_id or "").strip()
    if not owner_id:
        return None
    event_ts = float(timestamp if timestamp is not None else time.time())
    created_at = task_store.now_str()
    entry = {
        "session_id": owner_id,
        "message": str(message or ""),
        "level": str(level or "info"),
        "timestamp": event_ts,
        "created_at": created_at,
    }
    with log_history_lock:
        history = owner_log_history.get(owner_id)
        if history is None:
            history = deque(maxlen=LOG_HISTORY_LIMIT)
            owner_log_history[owner_id] = history
        history.append(entry)
    try:
        task_store.append_user_log(
            PROJECT_ROOT,
            owner_id,
            entry["message"],
            entry["level"],
            keep_limit=LOG_HISTORY_LIMIT,
            created_at=created_at,
        )
    except Exception:
        pass
    return entry


def _emit_owner_log_event(owner_id: str, message: str, level: str = "info", timestamp: float | None = None):
    entry = _store_owner_log(owner_id, message, level, timestamp)
    if not entry:
        return
    socketio.emit("task_log", entry, room=entry["session_id"])


def _get_recent_owner_logs(owner_id: str, limit: int = 80) -> list[dict]:
    owner_id = str(owner_id or "").strip()
    if not owner_id:
        return []
    try:
        persisted = task_store.get_recent_user_logs(PROJECT_ROOT, owner_id, limit=limit)
    except Exception:
        persisted = []
    with log_history_lock:
        history = owner_log_history.get(owner_id)
        memory_logs = list(history)[-max(1, int(limit)) :] if history else []
    if not persisted:
        return memory_logs
    if not memory_logs:
        return persisted
    merged = []
    seen = set()
    for item in [*persisted, *memory_logs]:
        key = (
            str(item.get("session_id") or ""),
            str(item.get("level") or ""),
            str(item.get("message") or ""),
            str(item.get("created_at") or item.get("timestamp") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged[-max(1, int(limit)) :]


def _acquire_start_request(owner_id: str) -> bool:
    owner_id = str(owner_id or "")
    if not owner_id:
        return True
    with start_request_lock:
        if owner_id in start_request_inflight:
            return False
        start_request_inflight.add(owner_id)
        return True


def _release_start_request(owner_id: str) -> None:
    owner_id = str(owner_id or "")
    if not owner_id:
        return
    with start_request_lock:
        start_request_inflight.discard(owner_id)


def _emit_owner_log(owner_id: str, message: str, level: str = "info"):
    _emit_owner_log_event(owner_id, message, level)


def _active_owner_ids() -> set[str]:
    active = set()
    try:
        for owner_id, runner in (task_threads or {}).items():
            if getattr(runner, "running", False):
                active.add(str(owner_id))
    except Exception:
        pass
    try:
        with user_worker_lock:
            for owner_id in user_workers_active:
                active.add(str(owner_id))
    except Exception:
        pass
    return active


def _remove_global_pending(owner_id: str) -> bool:
    owner_id = str(owner_id or "")
    if not owner_id:
        return False
    pos = task_store.get_user_queue_position(PROJECT_ROOT, owner_id)
    if pos is None:
        return False
    task_store.dequeue_user(PROJECT_ROOT, owner_id)
    with global_queue_lock:
        global_queue_cond.notify_all()
    return True


def _run_user_worker(owner_id: str):
    with user_worker_lock:
        user_workers_active.add(owner_id)
    try:
        profile = task_store.get_user_profile(PROJECT_ROOT, owner_id)
        client_id = str(profile.get("last_client_id") or owner_last_client_id.get(owner_id) or "").strip()
        config_id = str(profile.get("config_id") or owner_id or "").strip()
        if not client_id:
            _emit_owner_log(owner_id, "无法启动：缺少有效登录会话，请重新登录后再开始任务", "error")
            return
        api = get_or_create_api(client_id)
        if not is_client_logged_in(client_id):
            _emit_owner_log(owner_id, "无法启动：登录会话已失效，请重新登录后再开始任务", "error")
            return

        while True:
            next_item = task_store.get_next_pending_task(PROJECT_ROOT, owner_id)
            if not next_item:
                break
            task_id = int(next_item["id"])
            task_payload = next_item["task"]
            task_store.mark_task_running(PROJECT_ROOT, owner_id, task_id)
            try:
                task_obj = build_task_object(api, task_payload)
            except Exception as exc:
                task_store.mark_task_finished(PROJECT_ROOT, owner_id, task_id, "failed", str(exc))
                _emit_owner_log(owner_id, f"任务创建失败: {str(exc)}", "error")
                if task_store.pop_requeue_after_current(PROJECT_ROOT, owner_id):
                    task_store.enqueue_user(PROJECT_ROOT, owner_id, enqueued_at=time.time())
                    break
                continue

            runner = TaskRunner(owner_id, api, task_obj, runtime_config=load_client_config(config_id))
            runner.progress["status"] = "starting"
            runner.progress["message"] = "任务已创建，正在启动..."
            task_threads[owner_id] = runner
            owner_last_client_id[owner_id] = client_id
            runner.run()
            status = str((runner.progress or {}).get("status") or "")
            message = str((runner.progress or {}).get("message") or "")
            if status == "completed":
                task_store.mark_task_finished(PROJECT_ROOT, owner_id, task_id, "completed", message)
            elif status == "stopped":
                task_store.mark_task_finished(PROJECT_ROOT, owner_id, task_id, "stopped", message)
                break
            else:
                task_store.mark_task_finished(PROJECT_ROOT, owner_id, task_id, "failed", message)

            if task_store.pop_requeue_after_current(PROJECT_ROOT, owner_id):
                task_store.enqueue_user(PROJECT_ROOT, owner_id, enqueued_at=time.time())
                break
    finally:
        with user_worker_lock:
            user_workers_active.discard(owner_id)
            user_workers.pop(owner_id, None)
        try:
            with global_queue_lock:
                global_queue_cond.notify_all()
        except Exception:
            pass


def _start_user_worker(owner_id: str) -> bool:
    owner_id = str(owner_id or "")
    if not owner_id:
        return False
    with user_worker_lock:
        if owner_id in user_workers_active or owner_id in user_workers:
            return False
        thread = threading.Thread(target=_run_user_worker, args=(owner_id,), daemon=True)
        user_workers[owner_id] = thread
        thread.start()
    return True


def _global_dispatch_loop():
    while True:
        with global_queue_lock:
            global_queue_cond.wait(timeout=1.0)
            while True:
                active = _active_owner_ids()
                if len(active) >= MAX_CONCURRENT_USERS:
                    break
                candidates = task_store.list_next_users(PROJECT_ROOT, limit=100)
                next_owner = None
                for cand in candidates:
                    if str(cand) not in active:
                        next_owner = str(cand)
                        break
                if not next_owner:
                    break
                task_store.dequeue_user(PROJECT_ROOT, next_owner)
                if not _start_user_worker(next_owner):
                    task_store.enqueue_user(PROJECT_ROOT, next_owner, enqueued_at=time.time())
                    break
                _emit_owner_log(next_owner, "已从全局等待队列启动：开始执行你的课程队列", "success")
                continue


global_dispatcher_started = False
global_dispatcher_thread = None


def ensure_global_dispatcher():
    global global_dispatcher_started, global_dispatcher_thread
    if global_dispatcher_started:
        return
    with global_queue_lock:
        if global_dispatcher_started:
            return
        global_dispatcher_thread = threading.Thread(target=_global_dispatch_loop, daemon=True)
        global_dispatcher_thread.start()
        global_dispatcher_started = True


class SocketLogHandler(logging.Handler):
    def emit(self, record):
        session_id = get_log_session_id() or getattr(task_log_local, "session_id", None)
        if not session_id:
            return
        name = getattr(record, "name", "") or ""
        message = record.getMessage()
        text = f"[{name}] {message}" if name else message
        levelno = int(getattr(record, "levelno", logging.INFO))
        if levelno >= logging.ERROR:
            level = "error"
        elif levelno >= logging.WARNING:
            level = "warning"
        else:
            level = "info"
        try:
            _emit_owner_log_event(session_id, text, level)
        except Exception:
            pass


root_logger = logging.getLogger()
root_logger.addHandler(SocketLogHandler())


def user_to_dict(acc):
    return {
        "puid": acc.puid,
        "name": acc.name,
        "phone": acc.phone,
        "school": acc.school,
        "stu_id": getattr(acc, "stu_id", None),
    }


def get_api_acc(api):
    if not api:
        return None
    try:
        return api.acc
    except AttributeError:
        return None


def is_user_config_saved(config_id: str | None) -> bool:
    if not config_id:
        return False
    return task_store.has_user_config(PROJECT_ROOT, str(config_id))

def validate_effective_config(runtime_conf: dict) -> None:
    normalized = config.normalize_conf(runtime_conf)
    work = normalized.get("work") or {}
    if bool(work.get("enable")) and not normalized.get("searchers"):
        raise ValueError("已启用作业，但未配置搜索器：请到配置页面勾选至少一个搜索器并保存配置")


def load_client_config(config_id: str | None) -> dict:
    if not config_id:
        return config.get_default_conf()
    owner_id = str(config_id)
    saved = task_store.get_user_config(PROJECT_ROOT, owner_id)
    if isinstance(saved, dict):
        normalized = config.normalize_conf(saved)
        try:
            snapshot = config.db_conf_snapshot(normalized)
            if isinstance(snapshot, dict) and snapshot != saved:
                task_store.save_user_config(PROJECT_ROOT, owner_id, snapshot)
        except Exception:
            pass
        return normalized
    return config.get_default_conf()


def save_client_config(config_id: str, payload: dict) -> dict:
    normalized = config.normalize_conf(payload)
    snapshot = config.db_conf_snapshot(normalized)
    task_store.save_user_config(PROJECT_ROOT, str(config_id), snapshot)
    return normalized


def get_logged_in_puid(client_id: str | None) -> str | None:
    if not client_id:
        return None
    api = api_instances.get(client_id)
    acc = get_api_acc(api)
    puid = getattr(acc, "puid", None) if acc else None
    return str(puid) if puid else None


def get_config_id(client_id: str | None) -> str | None:
    puid = session.get("puid")
    if puid:
        return str(puid)
    puid = get_logged_in_puid(client_id)
    if puid:
        session["puid"] = puid
        return puid
    return client_id


def get_owner_id() -> str:
    client_id = get_client_id()
    return get_config_id(client_id) or client_id


def get_client_id() -> str:
    client_id = session.get("client_id")
    if not client_id:
        client_id = uuid.uuid4().hex
        session["client_id"] = client_id
    config_id = get_config_id(client_id)
    config.set_runtime_conf(load_client_config(config_id))
    return client_id


def get_feedback_mail_settings() -> dict:
    default_conf = config.get_default_conf()
    feedback_conf = default_conf.get("feedback", {}) if isinstance(default_conf, dict) else {}
    mail_conf = feedback_conf.get("mail", {}) if isinstance(feedback_conf, dict) else {}
    return {
        "smtp_host": os.getenv("FEEDBACK_SMTP_HOST", str(mail_conf.get("smtp_host", "")).strip()),
        "smtp_port": int(os.getenv("FEEDBACK_SMTP_PORT", mail_conf.get("smtp_port", 465) or 465)),
        "username": os.getenv("FEEDBACK_SMTP_USER", str(mail_conf.get("username", "")).strip()),
        "password": os.getenv("FEEDBACK_SMTP_PASS", str(mail_conf.get("password", "")).strip()),
        "from_addr": os.getenv("FEEDBACK_FROM_EMAIL", str(mail_conf.get("from_addr", "")).strip()),
        "to_addr": os.getenv("FEEDBACK_TO_EMAIL", str(mail_conf.get("to_addr", "")).strip()),
        "use_ssl": _to_bool(os.getenv("FEEDBACK_USE_SSL", mail_conf.get("use_ssl", True)), True),
        "use_starttls": _to_bool(os.getenv("FEEDBACK_USE_STARTTLS", mail_conf.get("use_starttls", False)), False),
    }


def get_donate_settings() -> dict:
    default_conf = config.get_default_conf()
    feedback_conf = default_conf.get("feedback", {}) if isinstance(default_conf, dict) else {}
    donate_conf = feedback_conf.get("donate", {}) if isinstance(feedback_conf, dict) else {}

    def _normalize_qr(value: str) -> str:
        raw = str(value or "").strip()
        if not raw:
            return ""
        if raw.startswith(("http://", "https://", "/")):
            return raw
        normalized = raw.replace("\\", "/")
        if normalized.startswith("png/"):
            return f"/png/{normalized.split('/', 1)[1]}"
        name = Path(normalized).name
        if name:
            candidate = PROJECT_ROOT / "png" / name
            if candidate.exists():
                return f"/png/{name}"
        return raw

    donate_qr = _normalize_qr(os.getenv("DONATE_QR", str(donate_conf.get("qr", "")).strip()))
    wechat_qr = _normalize_qr(os.getenv("DONATE_WECHAT_QR", str(donate_conf.get("wechat_qr", "")).strip()))
    alipay_qr = _normalize_qr(os.getenv("DONATE_ALIPAY_QR", str(donate_conf.get("alipay_qr", "")).strip()))
    if not wechat_qr and not alipay_qr and donate_qr:
        wechat_qr = donate_qr
        alipay_qr = donate_qr
    if not donate_qr:
        donate_qr = wechat_qr or alipay_qr
    return {
        "title": os.getenv("DONATE_TITLE", str(donate_conf.get("title", "")).strip() or "感谢支持本项目"),
        "description": os.getenv(
            "DONATE_DESCRIPTION",
            str(donate_conf.get("description", "")).strip() or "如果这个项目对你有帮助，欢迎打赏支持持续维护与更新。",
        ),
        "qr": donate_qr,
        "wechat_qr": wechat_qr,
        "alipay_qr": alipay_qr,
        "donate_link": os.getenv("DONATE_LINK", str(donate_conf.get("donate_link", "")).strip()),
    }


def get_user_log_path(api) -> Path | None:
    acc = get_api_acc(api)
    phone = getattr(acc, "phone", "") if acc else ""
    if not phone:
        return None
    safe_phone = re.sub(r"[^0-9a-zA-Z_-]+", "", str(phone))
    if not safe_phone:
        return None
    candidates = list(LOG_ROOT.glob(f"*_{safe_phone}.log"))
    candidates = [item for item in candidates if item.is_file()]
    if not candidates:
        return None
    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def read_tail_text(file_path: Path, max_bytes: int = 512 * 1024) -> str:
    with file_path.open("rb") as fp:
        fp.seek(0, os.SEEK_END)
        size = fp.tell()
        read_size = min(max_bytes, size)
        fp.seek(-read_size, os.SEEK_END)
        raw = fp.read(read_size)
    return raw.decode("utf8", errors="replace")


def build_feedback_context(client_id: str, owner_id: str, api):
    acc = get_api_acc(api)
    user = user_to_dict(acc) if acc else {}
    queue = get_queue_snapshot(owner_id)
    runner = task_threads.get(owner_id)
    context = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "client_id": client_id,
        "owner_id": owner_id,
        "request_ip": request.headers.get("X-Forwarded-For") or request.remote_addr or "",
        "user_agent": request.headers.get("User-Agent", ""),
        "user": user,
        "task_running": bool(runner.running) if runner else False,
        "task_progress": runner.progress if runner else None,
        "queue": queue,
    }
    return context, get_user_log_path(api)


def send_feedback_email(subject: str, message: str, contact: str, context: dict, log_path: Path | None) -> None:
    mail = get_feedback_mail_settings()
    required = ("smtp_host", "smtp_port", "username", "password")
    missing = [key for key in required if not mail.get(key)]
    if missing:
        raise ValueError(f"邮件配置不完整: 缺少 {', '.join(missing)}")

    from_addr = mail.get("from_addr") or mail["username"]
    to_addr = mail.get("to_addr") or mail["username"]
    ctx_text = json.dumps(context, ensure_ascii=False, indent=2)
    body = (
        f"用户反馈\n\n"
        f"主题: {subject}\n"
        f"联系方式: {contact or '未填写'}\n\n"
        f"反馈内容:\n{message}\n\n"
        f"----- 用户上下文信息 -----\n"
        f"{ctx_text}\n"
    )

    msg = EmailMessage()
    msg["Subject"] = f"[反馈] {subject}"
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg.set_content(body, subtype="plain", charset="utf-8")
    msg.add_attachment(
        ctx_text.encode("utf-8"),
        maintype="application",
        subtype="json",
        filename="feedback_context.json",
    )

    if log_path and log_path.exists():
        log_text = read_tail_text(log_path)
        msg.add_attachment(
            log_text.encode("utf-8"),
            maintype="text",
            subtype="plain",
            filename=log_path.name,
        )

    timeout = 20
    if mail["use_ssl"]:
        with smtplib.SMTP_SSL(mail["smtp_host"], int(mail["smtp_port"]), timeout=timeout) as smtp:
            smtp.login(mail["username"], mail["password"])
            smtp.send_message(msg)
        return

    with smtplib.SMTP(mail["smtp_host"], int(mail["smtp_port"]), timeout=timeout) as smtp:
        if mail["use_starttls"]:
            smtp.starttls()
        smtp.login(mail["username"], mail["password"])
        smtp.send_message(msg)


def get_or_create_api(client_id: str):
    if client_id not in api_instances:
        api_instances[client_id] = ChaoXingAPI()
    return api_instances[client_id]


def is_client_logged_in(client_id: str | None) -> bool:
    if not client_id:
        return False
    api = api_instances.get(client_id)
    return bool(get_api_acc(api))


def ensure_page_login():
    client_id = session.get("client_id")
    if not is_client_logged_in(client_id):
        return redirect(url_for("login_page"))
    return None


def find_course_meta(api, course_id):
    classes = api.fetch_classes()
    for index, course in enumerate(classes.classes):
        if str(course.course_id) == str(course_id):
            return classes, index, course
    return None, None, None


def get_queue_snapshot(client_id: str):
    owner_id = str(client_id or "")
    snapshot = task_store.get_queue_snapshot(PROJECT_ROOT, owner_id)
    pending_pos = task_store.get_user_queue_position(PROJECT_ROOT, owner_id)
    running = owner_id in _active_owner_ids()
    last_message = "队列空闲"
    if running:
        last_message = "任务执行中"
    elif pending_pos is not None:
        ahead = max(0, int(pending_pos) - 1)
        last_message = f"任务等待中，前方还有 {ahead} 人"
    elif int(snapshot.get("pending") or 0) > 0:
        last_message = f"待执行任务: {int(snapshot.get('pending') or 0)}"
    return {
        "running": bool(running),
        "total": int(snapshot.get("total") or 0),
        "completed": int(snapshot.get("completed") or 0),
        "failed": int(snapshot.get("failed") or 0),
        "pending": int(snapshot.get("pending") or 0),
        "current": None,
        "last_message": last_message,
        "last_error": "",
        "items": snapshot.get("items") or [],
    }


def normalize_task_payload(payload: dict):
    task_type = str(payload.get("type") or "chapter").strip()
    if task_type == "chapter":
        course_id = payload.get("course_id")
        if not course_id:
            raise ValueError("课程任务缺少 course_id")
        return {
            "type": "chapter",
            "course_id": course_id,
            "name": payload.get("name") or f"课程 {course_id}",
            "class_id": payload.get("class_id"),
        }

    if task_type == "exam":
        exam_data = payload.get("exam") if isinstance(payload.get("exam"), dict) else payload
        required = ("exam_id", "course_id", "class_id", "cpi", "enc_task")
        for key in required:
            if key not in exam_data:
                raise ValueError(f"考试任务缺少字段: {key}")
        return {
            "type": "exam",
            "name": exam_data.get("name") or "课程考试",
            "exam": {
                "exam_id": exam_data["exam_id"],
                "course_id": exam_data["course_id"],
                "class_id": exam_data["class_id"],
                "cpi": exam_data["cpi"],
                "enc_task": exam_data["enc_task"],
                "name": exam_data.get("name"),
            },
        }

    raise ValueError("未知任务类型")


def build_task_object(api, payload: dict):
    task_type = payload.get("type", "chapter")
    if task_type == "chapter":
        classes, index, course = find_course_meta(api, payload.get("course_id"))
        if classes is None or course is None:
            raise ValueError("课程不存在")
        chapters = classes.get_chapters_by_index(index)
        return ChapterContainer(
            session=api.session,
            acc=api.acc,
            courseid=course.course_id,
            name=course.name,
            classid=course.class_id,
            cpi=course.cpi,
            chapters=chapters,
        )

    if task_type == "exam":
        exam_data = payload.get("exam") or payload
        return ExamDto(
            session=api.session,
            acc=api.acc,
            exam_id=exam_data["exam_id"],
            course_id=exam_data["course_id"],
            class_id=exam_data["class_id"],
            cpi=exam_data["cpi"],
            enc_task=exam_data["enc_task"],
        )

    raise ValueError("未知任务类型")


def describe_task(task_obj):
    if isinstance(task_obj, ExamDto):
        return {
            "type": "exam",
            "name": getattr(task_obj, "title", None) or "课程考试",
            "course_id": task_obj.course_id,
            "class_id": task_obj.class_id,
        }
    if isinstance(task_obj, ChapterContainer):
        return {
            "type": "chapter",
            "name": task_obj.name,
            "course_id": task_obj.course_id,
            "class_id": task_obj.class_id,
            "total_chapters": len(task_obj),
        }
    return {"type": "unknown", "name": "未知任务"}


def describe_point(task_point):
    point_type = task_point.__class__.__name__
    if point_type == "PointVideoDto":
        return "视频"
    if point_type == "PointWorkDto":
        return "章节测验"
    if point_type == "PointDocumentDto":
        return "文档"
    return point_type


@app.before_request
def bind_runtime_config():
    if request.endpoint == "static":
        return
    client_id = session.get("client_id")
    config_id = get_config_id(client_id) if client_id else None
    config.set_runtime_conf(load_client_config(config_id) if config_id else config.get_default_conf())


@app.teardown_request
def clear_runtime_config(_error=None):
    config.clear_runtime_conf()


class TaskRunner:
    def __init__(self, session_id, api, task_obj, runtime_config):
        self.session_id = session_id
        self.api = api
        self.task_obj = task_obj
        self.runtime_config = config.normalize_conf(runtime_config)
        self.running = True
        self.stop_event = threading.Event()
        self.progress = self._build_initial_progress()

    def send_log(self, message, level="info"):
        _emit_owner_log_event(self.session_id, message, level)

    def send_progress(self):
        socketio.emit(
            "task_progress",
            {**self.progress, "session_id": self.session_id},
            room=self.session_id,
        )

    def _build_initial_progress(self):
        task = describe_task(self.task_obj)
        return {
            "task": task,
            "status": "idle",
            "percent": 0,
            "current_chapter_label": "",
            "current_chapter_name": "",
            "current_point_title": "",
            "current_point_type": "",
            "total_chapters": task.get("total_chapters", 0),
            "finished_chapters": 0,
            "message": "等待任务开始",
        }

    def update_progress(self, **kwargs):
        self.progress.update(kwargs)
        self.send_progress()

    def sync_chapter_progress(self, chap, message=None):
        total = len(chap)
        finished = sum(1 for idx in range(total) if chap.is_finished(idx))
        current_percent = round((finished / total) * 100, 1) if total else 0

        current_label = self.progress.get("current_chapter_label")
        current_name = self.progress.get("current_chapter_name")
        partial_bonus = 0.0
        if current_label or current_name:
            for chapter in chap.chapters:
                if chapter.label == current_label and chapter.name == current_name and chapter.point_total:
                    partial_bonus = (chapter.point_finished / chapter.point_total) * (100 / total)
                    break
        percent = min(100.0, round(current_percent + partial_bonus, 1)) if total else 0

        self.update_progress(
            total_chapters=total,
            finished_chapters=finished,
            percent=percent,
            status="running" if self.running else "stopping",
            message=message or self.progress.get("message", ""),
        )

    def run(self):
        task_log_local.session_id = self.session_id
        set_log_session_id(self.session_id)
        try:
            from cxapi.exception import ChapterNotOpened, TaskPointError
            from cxapi.task_point import PointDocumentDto, PointVideoDto, PointWorkDto
            from resolver import DocumetResolver, MediaPlayResolver, QuestionResolver

            work_conf = self.runtime_config["work"]
            video_conf = self.runtime_config["video"]
            document_conf = self.runtime_config["document"]
            exam_conf = self.runtime_config["exam"]

            config.set_runtime_conf(self.runtime_config)
            if self.api.acc and self.api.acc.phone:
                set_log_filename(self.api.acc.phone)

            self.update_progress(status="running", message="任务线程已启动")
            if isinstance(self.task_obj, ChapterContainer):
                chap = self.task_obj
                self.send_log(f"开始处理课程: {chap.name}", "info")
                self.update_progress(status="running", message="正在拉取任务点状态...")
                chap.fetch_point_status()
                self.update_progress(
                    status="running",
                    total_chapters=len(chap),
                    finished_chapters=sum(1 for idx in range(len(chap)) if chap.is_finished(idx)),
                    percent=0,
                    message="已进入章节任务流程",
                )


                for index in range(len(chap)):
                    if not self.running:
                        self.update_progress(status="stopped", message="任务被用户停止")
                        self.send_log("任务被用户停止", "warning")
                        return

                    chapter = chap.chapters[index]
                    self.update_progress(
                        current_chapter_label=chapter.label,
                        current_chapter_name=chapter.name,
                        current_point_title="",
                        current_point_type="",
                        message=f"正在处理章节 {chapter.label} {chapter.name}",
                    )
                    self.sync_chapter_progress(chap)

                    if chap.is_finished(index) and work_conf["export"] is False:
                        self.send_log(f"跳过已完成章节: {chap.chapters[index].name}", "info")
                        self.sync_chapter_progress(chap, f"已跳过章节 {chapter.label} {chapter.name}")
                        continue

                    refresh_flag = True
                    for task_point in chap[index]:
                        if (not self.running) or self.stop_event.is_set():
                            self.update_progress(status="stopped", message="任务被用户停止")
                            self.send_log("任务被用户停止", "warning")
                            return

                        try:
                            task_point.fetch_attachment()
                        except ChapterNotOpened:
                            if refresh_flag:
                                chap.refresh_chapter(index - 1)
                                refresh_flag = False
                                continue
                            self.send_log(f"章节未开放: {chap.chapters[index].name}", "error")
                            self.update_progress(status="error", message=f"章节未开放: {chap.chapters[index].name}")
                            return

                        refresh_flag = True
                        self.update_progress(
                            current_point_title=getattr(task_point, "title", "未命名任务点"),
                            current_point_type=describe_point(task_point),
                            message=f"正在处理 {describe_point(task_point)}: {getattr(task_point, 'title', '未命名任务点')}",
                        )
                        self.send_log(
                            f"开始任务点: {describe_point(task_point)} - {getattr(task_point, 'title', '未命名任务点')}",
                            "info",
                        )

                        try:
                            if isinstance(task_point, PointWorkDto) and work_conf["enable"]:
                                if not task_point.parse_attachment():
                                    self.send_log(f"跳过章节测验(无附件): {getattr(task_point, 'title', '')}", "warning")
                                    continue
                                task_point.fetch_all()
                                resolver = QuestionResolver(
                                    exam_dto=task_point,
                                    fallback_save=work_conf["fallback_save"],
                                    fallback_fuzzer=work_conf["fallback_fuzzer"],
                                )
                                resolver.execute()
                                self.send_log(f"完成章节测验: {task_point.title}", "success")

                            elif isinstance(task_point, PointVideoDto) and video_conf["enable"]:
                                if not task_point.parse_attachment():
                                    self.send_log(f"跳过视频(无附件): {getattr(task_point, 'title', '')}", "warning")
                                    continue
                                self.send_log(f"开始观看视频: {getattr(task_point, 'title', '')}", "info")
                                if not task_point.fetch():
                                    self.send_log(f"跳过视频(获取失败): {getattr(task_point, 'title', '')}", "warning")
                                    continue
                                resolver = MediaPlayResolver(
                                    media_dto=task_point,
                                    speed=video_conf["speed"],
                                    report_rate=video_conf["report_rate"],
                                    stop_event=self.stop_event,
                                )
                                resolver.execute()
                                if (not self.running) or self.stop_event.is_set():
                                    self.update_progress(status="stopped", message="任务被用户停止")
                                    self.send_log("任务被用户停止", "warning")
                                    return
                                self.send_log(f"完成视频: {task_point.title}", "success")
                                self.stop_event.wait(float(video_conf["wait"]))

                            elif isinstance(task_point, PointDocumentDto) and document_conf["enable"]:
                                if not task_point.parse_attachment():
                                    self.send_log(f"跳过文档(无附件): {getattr(task_point, 'title', '')}", "warning")
                                    continue
                                resolver = DocumetResolver(document_dto=task_point)
                                resolver.execute()
                                self.send_log(f"完成文档: {task_point.title}", "success")
                                self.stop_event.wait(float(document_conf["wait"]))

                        except (TaskPointError, NotImplementedError) as e:
                            self.send_log(f"任务点执行异常: {str(e)}", "error")
                            self.update_progress(status="error", message=f"任务点执行异常: {str(e)}")
                            return

                        chap.fetch_point_status()
                        self.sync_chapter_progress(chap)

                self.send_log("课程任务全部完成!", "success")
                self.update_progress(
                    status="completed",
                    finished_chapters=len(chap),
                    percent=100,
                    current_point_title="",
                    current_point_type="",
                    message="课程任务全部完成",
                )

            elif isinstance(self.task_obj, ExamDto):
                exam = self.task_obj
                exam.get_meta()
                exam.start()
                self.send_log(f"开始考试: {exam.title}", "info")
                self.update_progress(
                    status="running",
                    percent=5,
                    current_point_title="考试答题中",
                    current_point_type="考试",
                    message=f"正在处理考试 {exam.title}",
                )

                resolver = QuestionResolver(
                    exam_dto=exam,
                    fallback_save=False,
                    fallback_fuzzer=exam_conf["fallback_fuzzer"],
                    persubmit_delay=exam_conf["persubmit_delay"],
                    auto_final_submit=not exam_conf["confirm_submit"],
                )
                resolver.execute()
                self.send_log("考试答题完成!", "success")
                self.update_progress(
                    status="completed",
                    percent=100,
                    current_point_title="",
                    current_point_type="考试",
                    message="考试答题完成",
                )

        except Exception as e:
            self.send_log(f"任务执行异常: {str(e)}", "error")
            self.update_progress(status="error", message=f"任务执行异常: {str(e)}")
        finally:
            self.send_log("task_finished", "info")
            if self.session_id in task_threads:
                del task_threads[self.session_id]
            self.running = False
            config.clear_runtime_conf()
            try:
                with global_queue_lock:
                    global_queue_cond.notify_all()
            except Exception:
                pass
            try:
                if getattr(task_log_local, "session_id", None) == self.session_id:
                    delattr(task_log_local, "session_id")
            except Exception:
                pass
            clear_log_session_id()


@socketio.on("join")
def socket_join(_data=None):
    owner_id = get_owner_id()
    join_room(owner_id)
    emit("joined", {"status": "success", "owner_id": owner_id})


@app.route("/")
def login_page():
    if is_client_logged_in(session.get("client_id")):
        return redirect(url_for("courses_page"))
    return render_template("login.html")


@app.route("/static/css/style.css")
def legacy_style_css_redirect():
    resp = redirect("/static/css/app.css", code=302)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/courses")
def courses_page():
    guarded = ensure_page_login()
    if guarded:
        return guarded
    return render_template("courses.html", active_page="courses")


@app.route("/settings")
def settings_page():
    guarded = ensure_page_login()
    if guarded:
        return guarded
    return render_template("config.html", active_page="settings")


@app.route("/tasks")
def tasks_page():
    guarded = ensure_page_login()
    if guarded:
        return guarded
    return render_template("tasks.html", active_page="tasks")


@app.route("/donate")
def donate_page():
    guarded = ensure_page_login()
    if guarded:
        return guarded
    return render_template("donate.html", active_page="donate", donate=get_donate_settings())


@app.route("/png/<path:filename>")
def png_static(filename: str):
    return send_from_directory(str(PROJECT_ROOT / "png"), filename)


@app.route("/api/client/id", methods=["GET"])
def client_id():
    return jsonify({"status": "success", "client_id": get_client_id()})


@app.route("/api/login/qr/create", methods=["POST"])
def qr_create():
    client_id = get_client_id()
    api = get_or_create_api(client_id)
    api.qr_get()
    qr_url = api.qr_geturl()
    return jsonify({"qr_url": qr_url, "status": "created"})


@app.route("/api/login/qr/poll", methods=["POST"])
def qr_poll():
    client_id = get_client_id()
    api = get_or_create_api(client_id)

    if not hasattr(api, "qr_enc") or not api.qr_enc:
        return jsonify({"status": "error", "message": "请先获取二维码"})

    qr_status = api.login_qr()
    if qr_status.get("status") is True:
        api.accinfo()
        puid = getattr(api.acc, "puid", None)
        if puid:
            session["puid"] = str(puid)
        return jsonify({"status": "success", "message": "登录成功", "user": user_to_dict(api.acc)})

    return jsonify(
        {
            "status": "waiting",
            "type": qr_status.get("type"),
            "nickname": qr_status.get("nickname"),
            "uid": qr_status.get("uid"),
        }
    )


@app.route("/api/login/passwd", methods=["POST"])
def login_passwd():
    payload = request.json or {}
    phone = payload.get("phone")
    password = payload.get("password")
    client_id = get_client_id()
    api = get_or_create_api(client_id)

    status, result = api.login_passwd(phone, password)
    if status:
        api.accinfo()
        puid = getattr(api.acc, "puid", None)
        if puid:
            session["puid"] = str(puid)
        return jsonify({"status": "success", "message": "登录成功", "user": user_to_dict(api.acc)})
    return jsonify({"status": "failed", "message": result.get("msg", "登录失败")})


@app.route("/api/account/info", methods=["GET"])
def account_info():
    client_id = get_client_id()
    api = get_or_create_api(client_id)
    acc = get_api_acc(api)
    if acc:
        return jsonify({"logged_in": True, "user": user_to_dict(acc)})
    return jsonify({"logged_in": False})


@app.route("/api/config", methods=["GET"])
def get_runtime_config():
    client_id = get_client_id()
    config_id = get_config_id(client_id)
    return jsonify({"status": "success", "config": load_client_config(config_id)})


@app.route("/api/config", methods=["POST"])
def update_runtime_config():
    client_id = get_client_id()
    config_id = get_config_id(client_id)
    payload = request.json or {}
    try:
        normalized = config.normalize_conf(payload)
        validate_effective_config(normalized)
        saved = save_client_config(config_id, normalized)
        config.set_runtime_conf(saved)
        return jsonify({"status": "success", "message": "配置已保存", "config": saved})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})


@app.route("/api/courses", methods=["GET"])
def get_courses():
    client_id = get_client_id()
    api = get_or_create_api(client_id)
    try:
        classes = api.fetch_classes()
        course_list = []
        for cla in classes.classes:
            course_list.append(
                {
                    "course_id": cla.course_id,
                    "name": cla.name,
                    "teacher_name": cla.teacher_name,
                    "state": cla.state.name,
                    "class_id": cla.class_id,
                    "cpi": cla.cpi,
                }
            )
        return jsonify({"status": "success", "courses": course_list})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})


@app.route("/api/exams", methods=["GET"])
def get_exams():
    course_id = request.args.get("course_id")
    client_id = get_client_id()
    api = get_or_create_api(client_id)

    try:
        classes, index, _course = find_course_meta(api, course_id)
        if classes is None:
            return jsonify({"status": "error", "message": "课程不存在"})
        exams = classes.get_exam_by_index(index)

        exam_list = []
        for exam in exams:
            exam_list.append(
                {
                    "exam_id": exam.exam_id,
                    "name": exam.name,
                    "expire_time": exam.expire_time,
                    "status": exam.status.name,
                    "course_id": exam.course_id,
                    "class_id": exam.class_id,
                    "cpi": exam.cpi,
                    "enc_task": exam.enc_task,
                }
            )
        return jsonify({"status": "success", "exams": exam_list})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})


@app.route("/api/task/selection", methods=["POST"])
def save_task_selection():
    client_id = get_client_id()
    owner_id = get_config_id(client_id) or client_id
    api = get_or_create_api(client_id)
    if not get_api_acc(api):
        return jsonify({"status": "error", "message": "请先登录后再选择任务"})
    payload = request.json or {}
    try:
        normalized = normalize_task_payload(payload)
        if normalized.get("type") == "exam":
            return jsonify({"status": "error", "message": "考试任务已禁用"})
        config_id = get_config_id(client_id)
        task_store.upsert_user_profile(PROJECT_ROOT, owner_id, config_id=config_id, client_id=client_id, selected_task=normalized)
        return jsonify({"status": "success", "selected_task": normalized})
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)})


@app.route("/api/task/selection/clear", methods=["POST"])
def clear_task_selection_api():
    client_id = get_client_id()
    owner_id = get_config_id(client_id) or client_id
    api = get_or_create_api(client_id)
    if not get_api_acc(api):
        return jsonify({"status": "error", "message": "请先登录后再操作"})
    task_store.clear_selected_task(PROJECT_ROOT, str(owner_id))
    return jsonify({"status": "success"})


@app.route("/api/task/start", methods=["POST"])
def start_task():
    payload = request.json or {}

    client_id = get_client_id()
    owner_id = get_config_id(client_id) or client_id
    if not _acquire_start_request(owner_id):
        return jsonify({"status": "error", "message": "启动请求处理中，请勿重复点击"})
    try:
        config_id = get_config_id(client_id)
        if not is_user_config_saved(config_id):
            return jsonify({"status": "error", "message": "请先前往配置页面保存配置，否则无法开始任务"})

        try:
            validate_effective_config(load_client_config(config_id))
        except Exception as exc:
            return jsonify({"status": "error", "message": str(exc)})

        normalized = normalize_task_payload(payload)
        if normalized.get("type") == "exam":
            return jsonify({"status": "error", "message": "考试任务已禁用"})
        task_store.upsert_user_profile(PROJECT_ROOT, owner_id, config_id=config_id, client_id=client_id, selected_task=normalized)
        task_store.add_tasks(PROJECT_ROOT, owner_id, [normalized])

        active = _active_owner_ids()
        if str(owner_id) in active:
            task_store.set_requeue_after_current(PROJECT_ROOT, owner_id, True)
            task_store.enqueue_user(PROJECT_ROOT, owner_id, enqueued_at=time.time())
            pos = task_store.get_user_queue_position(PROJECT_ROOT, owner_id) or 1
            ahead = max(0, int(pos) - 1)
            _emit_owner_log(owner_id, f"已添加新课程，当前课程结束后将重新排队，前方还有 {ahead} 人", "warning")
            return jsonify(
                {
                    "status": "queued",
                    "message": f"已添加新课程，当前课程结束后将重新排队，前方还有 {ahead} 人",
                    "position": int(pos),
                    "ahead": ahead,
                    "running": len(active),
                    "limit": MAX_CONCURRENT_USERS,
                    "task": normalized,
                }
            )

        task_store.enqueue_user(PROJECT_ROOT, owner_id, enqueued_at=time.time())
        pos = task_store.get_user_queue_position(PROJECT_ROOT, owner_id) or 1
        active_cnt = len(active)
        if int(pos) == 1 and active_cnt < MAX_CONCURRENT_USERS:
            task_store.dequeue_user(PROJECT_ROOT, owner_id)
            ensure_global_dispatcher()
            _start_user_worker(owner_id)
            return jsonify(
                {
                    "status": "started",
                    "message": "任务已开始",
                    "task": normalized,
                    "progress": {"status": "starting", "message": "任务已创建，正在启动..."},
                }
            )

        ahead = max(0, int(pos) - 1)
        _emit_owner_log(owner_id, f"任务已进入全局等待队列，前方还有 {ahead} 人", "warning")
        ensure_global_dispatcher()
        with global_queue_lock:
            global_queue_cond.notify_all()
        return jsonify(
            {
                "status": "queued",
                "message": f"任务等待中，前方还有 {ahead} 人",
                "position": int(pos),
                "ahead": ahead,
                "running": active_cnt,
                "limit": MAX_CONCURRENT_USERS,
                "task": normalized,
            }
        )
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})
    finally:
        _release_start_request(owner_id)


@app.route("/api/task/queue/add", methods=["POST"])
def add_task_queue():
    payload = request.json or {}
    tasks_payload = payload.get("tasks")
    if tasks_payload is None:
        tasks_payload = [payload]
    if not isinstance(tasks_payload, list):
        return jsonify({"status": "error", "message": "tasks 必须是数组"})
    if not tasks_payload:
        return jsonify({"status": "error", "message": "队列为空"})

    client_id = get_client_id()
    owner_id = get_config_id(client_id) or client_id
    config_id = get_config_id(client_id)
    if not is_user_config_saved(config_id):
        return jsonify({"status": "error", "message": "请先前往配置页面保存配置，否则无法加入队列"})
    try:
        validate_effective_config(load_client_config(config_id))
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)})
    normalized_tasks = []
    try:
        for item in tasks_payload:
            task_payload = normalize_task_payload(item or {})
            if task_payload.get("type") == "exam":
                raise ValueError("考试任务已禁用")
            normalized_tasks.append(task_payload)
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)})
    last_task = normalized_tasks[-1] if normalized_tasks else None
    task_store.upsert_user_profile(PROJECT_ROOT, owner_id, config_id=config_id, client_id=client_id, selected_task=last_task)
    added = task_store.add_tasks(PROJECT_ROOT, owner_id, normalized_tasks)

    active = _active_owner_ids()
    if str(owner_id) in active:
        task_store.set_requeue_after_current(PROJECT_ROOT, owner_id, True)
        task_store.enqueue_user(PROJECT_ROOT, owner_id, enqueued_at=time.time())
    else:
        if added > 0:
            task_store.enqueue_user(PROJECT_ROOT, owner_id, enqueued_at=time.time())

    pos = task_store.get_user_queue_position(PROJECT_ROOT, owner_id)
    if pos is not None and int(pos) == 1 and len(active) < MAX_CONCURRENT_USERS:
        task_store.dequeue_user(PROJECT_ROOT, owner_id)
        ensure_global_dispatcher()
        _start_user_worker(owner_id)

    return jsonify({"status": "success", "added": int(added), "queue": get_queue_snapshot(owner_id)})


@app.route("/api/task/queue/remove", methods=["POST"])
def remove_task_queue_item():
    payload = request.json or {}
    queue_id = payload.get("queue_id")
    enqueued_at = payload.get("enqueued_at")
    if not queue_id and not enqueued_at:
        return jsonify({"status": "error", "message": "缺少 queue_id"})

    client_id = get_client_id()
    owner_id = get_config_id(client_id) or client_id
    removed = False
    if queue_id:
        try:
            removed = bool(task_store.remove_task(PROJECT_ROOT, owner_id, int(queue_id)))
        except Exception:
            removed = False
    return jsonify({"status": "success", "removed": bool(removed), "queue": get_queue_snapshot(owner_id)})


@app.route("/api/task/queue/start", methods=["POST"])
def start_task_queue():
    client_id = get_client_id()
    owner_id = get_config_id(client_id) or client_id
    if not _acquire_start_request(owner_id):
        return jsonify({"status": "error", "message": "启动请求处理中，请勿重复点击"})
    config_id = get_config_id(client_id)
    try:
        if not is_user_config_saved(config_id):
            return jsonify({"status": "error", "message": "请先前往配置页面保存配置，否则无法开始任务"})

        try:
            validate_effective_config(load_client_config(config_id))
        except Exception as exc:
            return jsonify({"status": "error", "message": str(exc)})

        queue_snapshot = get_queue_snapshot(owner_id)
        if int(queue_snapshot.get("pending") or 0) <= 0:
            return jsonify({"status": "error", "message": "队列为空，请先加入课程"})

        task_store.upsert_user_profile(PROJECT_ROOT, owner_id, config_id=config_id, client_id=client_id, selected_task=None)
        active = _active_owner_ids()
        if str(owner_id) in active:
            task_store.set_requeue_after_current(PROJECT_ROOT, owner_id, True)
            task_store.enqueue_user(PROJECT_ROOT, owner_id, enqueued_at=time.time())
        else:
            task_store.enqueue_user(PROJECT_ROOT, owner_id, enqueued_at=time.time())

        pos = task_store.get_user_queue_position(PROJECT_ROOT, owner_id) or 1
        active_cnt = len(active)
        if int(pos) == 1 and active_cnt < MAX_CONCURRENT_USERS:
            task_store.dequeue_user(PROJECT_ROOT, owner_id)
            ensure_global_dispatcher()
            _start_user_worker(owner_id)
            return jsonify({"status": "started", "queue": get_queue_snapshot(owner_id)})

        ahead = max(0, int(pos) - 1)
        _emit_owner_log(owner_id, f"队列已进入全局等待队列，前方还有 {ahead} 人", "warning")
        ensure_global_dispatcher()
        with global_queue_lock:
            global_queue_cond.notify_all()
        return jsonify(
            {
                "status": "queued",
                "message": f"队列等待中，前方还有 {ahead} 人",
                "position": int(pos),
                "ahead": ahead,
                "running": active_cnt,
                "limit": MAX_CONCURRENT_USERS,
                "queue": get_queue_snapshot(owner_id),
            }
        )
    finally:
        _release_start_request(owner_id)


@app.route("/api/task/queue/clear", methods=["POST"])
def clear_task_queue():
    client_id = get_client_id()
    owner_id = get_config_id(client_id) or client_id
    task_store.clear_pending_tasks(PROJECT_ROOT, owner_id)
    task_store.dequeue_user(PROJECT_ROOT, owner_id)
    return jsonify({"status": "success", "queue": get_queue_snapshot(owner_id)})


@app.route("/api/task/stop", methods=["POST"])
def stop_task():
    client_id = get_client_id()
    owner_id = get_config_id(client_id) or client_id
    removed_waiting = _remove_global_pending(owner_id)
    task_store.set_requeue_after_current(PROJECT_ROOT, owner_id, False)

    if owner_id in task_threads:
        runner = task_threads[owner_id]
        runner.running = False
        try:
            runner.stop_event.set()
        except Exception:
            pass
        msg = "任务已停止，待执行课程已保留"
        if removed_waiting:
            msg = f"{msg}，并已从全局等待队列移除"
        with global_queue_lock:
            global_queue_cond.notify_all()
        return jsonify({"status": "success", "message": msg, "queue": get_queue_snapshot(owner_id)})
    msg = "任务已停止，待执行课程已保留"
    if removed_waiting:
        msg = f"{msg}，并已从全局等待队列移除"
    with global_queue_lock:
        global_queue_cond.notify_all()
    return jsonify({"status": "success", "message": msg, "queue": get_queue_snapshot(owner_id)})


@app.route("/api/task/status", methods=["GET"])
def task_status():
    client_id = get_client_id()
    owner_id = get_config_id(client_id) or client_id
    queue = get_queue_snapshot(owner_id)
    config_id = get_config_id(client_id)
    config_saved = is_user_config_saved(config_id)
    config_issue = ""
    if not config_saved:
        config_issue = "请先前往配置页面保存配置，否则无法开始任务"
    else:
        try:
            validate_effective_config(load_client_config(config_id))
        except Exception as exc:
            config_issue = str(exc)
    selected_task = task_store.get_selected_task(PROJECT_ROOT, str(owner_id))
    waiting_pos = task_store.get_user_queue_position(PROJECT_ROOT, str(owner_id))
    waiting = None
    if waiting_pos is not None:
        profile = task_store.get_user_profile(PROJECT_ROOT, str(owner_id))
        waiting = {
            "position": int(waiting_pos),
            "ahead": max(0, int(waiting_pos) - 1),
            "action": None,
            "task": profile.get("selected_task") if isinstance(profile.get("selected_task"), dict) else None,
            "enqueued_at": None,
        }
    recent_logs = _get_recent_owner_logs(owner_id)
    if owner_id in task_threads:
        runner = task_threads[owner_id]
        progress = dict(runner.progress or {})
        progress["session_id"] = owner_id
        return jsonify(
            {
                "owner_id": owner_id,
                "running": bool(runner.running or queue["running"]),
                "session_id": owner_id,
                "task": describe_task(runner.task_obj),
                "progress": progress,
                "queue": queue,
                "waiting": waiting,
                "global_running": len(_active_owner_ids()),
                "global_limit": MAX_CONCURRENT_USERS,
                "selected_task": selected_task,
                "config_saved": config_saved,
                "config_issue": config_issue,
                "recent_logs": recent_logs,
            }
        )
    return jsonify(
        {
            "owner_id": owner_id,
            "running": bool(queue["running"]),
            "progress": None,
            "queue": queue,
            "waiting": waiting,
            "global_running": len(_active_owner_ids()),
            "global_limit": MAX_CONCURRENT_USERS,
            "selected_task": selected_task,
            "config_saved": config_saved,
            "config_issue": config_issue,
            "recent_logs": recent_logs,
        }
    )
@app.route("/api/feedback/send", methods=["POST"])
def send_feedback():
    client_id = get_client_id()
    if not is_client_logged_in(client_id):
        return jsonify({"status": "error", "message": "请先登录后再提交反馈"})

    payload = request.json or {}
    subject = str(payload.get("subject") or "").strip() or "未命名反馈"
    message = str(payload.get("message") or "").strip()
    contact = str(payload.get("contact") or "").strip()
    if len(message) < 5:
        return jsonify({"status": "error", "message": "反馈内容太短，请至少填写 5 个字符"})

    owner_id = get_owner_id()
    api = get_or_create_api(client_id)
    try:
        context, log_path = build_feedback_context(client_id, owner_id, api)
        send_feedback_email(subject, message, contact, context, log_path)
        return jsonify({"status": "success", "message": "反馈已发送，感谢你的支持与建议"})
    except Exception as exc:
        return jsonify({"status": "error", "message": f"发送失败: {str(exc)}"})

@app.route("/api/logout", methods=["POST"])
def logout():
    client_id = session.get("client_id")
    owner_id = get_config_id(client_id) or client_id if client_id else None
    if owner_id:
        _remove_global_pending(str(owner_id))
    if client_id and client_id in api_instances:
        del api_instances[client_id]
    session.clear()
    return jsonify({"status": "success"})


if __name__ == "__main__":
    os.makedirs("html", exist_ok=True)
    os.makedirs("static", exist_ok=True)
    print("Web 服务已启动:", flush=True)
    print("  - http://localhost:5000/", flush=True)
    print("  - http://127.0.0.1:5000/", flush=True)
    socketio.run(app, host="0.0.0.0", port=5000, debug=False, use_reloader=False)
