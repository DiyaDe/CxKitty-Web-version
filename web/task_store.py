import json
import sqlite3
import threading
import time
from pathlib import Path
from datetime import datetime


_lock = threading.Lock()
_db_path: Path | None = None


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _get_db_path(project_root: Path) -> Path:
    global _db_path
    if _db_path is not None:
        return _db_path
    with _lock:
        if _db_path is not None:
            return _db_path
        data_dir = project_root / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        _db_path = data_dir / "scheduler.db"
        return _db_path


def _connect(project_root: Path) -> sqlite3.Connection:
    db_path = _get_db_path(project_root)
    conn = sqlite3.connect(str(db_path), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_db(project_root: Path) -> None:
    conn = _connect(project_root)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_config (
              owner_id TEXT PRIMARY KEY,
              config_json TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_profile (
              owner_id TEXT PRIMARY KEY,
              config_id TEXT,
              last_client_id TEXT,
              selected_task_json TEXT,
              requeue_after_current INTEGER NOT NULL DEFAULT 0,
              updated_at TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_tasks (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              owner_id TEXT NOT NULL,
              task_key TEXT NOT NULL,
              task_json TEXT NOT NULL,
              status TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              started_at TEXT,
              finished_at TEXT,
              last_error TEXT
            );
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_user_tasks_owner_status ON user_tasks(owner_id, status, created_at);")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_user_tasks_owner_key ON user_tasks(owner_id, task_key);")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS global_user_queue (
              owner_id TEXT PRIMARY KEY,
              enqueued_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_global_user_queue_enqueued ON global_user_queue(enqueued_at);")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_task_logs (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              owner_id TEXT NOT NULL,
              level TEXT NOT NULL,
              message TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_user_task_logs_owner_id ON user_task_logs(owner_id, id);")
        _migrate_timestamp_columns(conn)
    finally:
        conn.close()


def _table_has_real_timestamps(conn: sqlite3.Connection, table: str) -> bool:
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    except Exception:
        return False
    for row in rows:
        name = str(row["name"] or "")
        col_type = str(row["type"] or "").upper()
        if name.endswith("_at") and col_type == "REAL":
            return True
    return False


def _migrate_timestamp_columns(conn: sqlite3.Connection) -> None:
    need = False
    for table in ("user_profile", "user_tasks", "global_user_queue"):
        if _table_has_real_timestamps(conn, table):
            need = True
            break
    if not need:
        return

    conn.execute("BEGIN;")
    try:
        conn.execute("ALTER TABLE user_profile RENAME TO user_profile_old;")
        conn.execute("ALTER TABLE user_tasks RENAME TO user_tasks_old;")
        conn.execute("ALTER TABLE global_user_queue RENAME TO global_user_queue_old;")

        conn.execute(
            """
            CREATE TABLE user_profile (
              owner_id TEXT PRIMARY KEY,
              config_id TEXT,
              last_client_id TEXT,
              selected_task_json TEXT,
              requeue_after_current INTEGER NOT NULL DEFAULT 0,
              updated_at TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE user_tasks (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              owner_id TEXT NOT NULL,
              task_key TEXT NOT NULL,
              task_json TEXT NOT NULL,
              status TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              started_at TEXT,
              finished_at TEXT,
              last_error TEXT
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE global_user_queue (
              owner_id TEXT PRIMARY KEY,
              enqueued_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            """
        )

        conn.execute(
            """
            INSERT INTO user_profile(owner_id, config_id, last_client_id, selected_task_json, requeue_after_current, updated_at)
            SELECT
              owner_id,
              config_id,
              last_client_id,
              selected_task_json,
              requeue_after_current,
              CASE
                WHEN typeof(updated_at) IN ('real','integer') THEN datetime(updated_at, 'unixepoch', 'localtime')
                ELSE CAST(updated_at AS TEXT)
              END
            FROM user_profile_old;
            """
        )
        conn.execute(
            """
            INSERT INTO user_tasks(id, owner_id, task_key, task_json, status, created_at, updated_at, started_at, finished_at, last_error)
            SELECT
              id,
              owner_id,
              task_key,
              task_json,
              status,
              CASE
                WHEN typeof(created_at) IN ('real','integer') THEN datetime(created_at, 'unixepoch', 'localtime')
                ELSE CAST(created_at AS TEXT)
              END,
              CASE
                WHEN typeof(updated_at) IN ('real','integer') THEN datetime(updated_at, 'unixepoch', 'localtime')
                ELSE CAST(updated_at AS TEXT)
              END,
              CASE
                WHEN started_at IS NULL THEN NULL
                WHEN typeof(started_at) IN ('real','integer') THEN datetime(started_at, 'unixepoch', 'localtime')
                ELSE CAST(started_at AS TEXT)
              END,
              CASE
                WHEN finished_at IS NULL THEN NULL
                WHEN typeof(finished_at) IN ('real','integer') THEN datetime(finished_at, 'unixepoch', 'localtime')
                ELSE CAST(finished_at AS TEXT)
              END,
              last_error
            FROM user_tasks_old;
            """
        )
        conn.execute(
            """
            INSERT INTO global_user_queue(owner_id, enqueued_at, updated_at)
            SELECT
              owner_id,
              CASE
                WHEN typeof(enqueued_at) IN ('real','integer') THEN datetime(enqueued_at, 'unixepoch', 'localtime')
                ELSE CAST(enqueued_at AS TEXT)
              END,
              CASE
                WHEN typeof(updated_at) IN ('real','integer') THEN datetime(updated_at, 'unixepoch', 'localtime')
                ELSE CAST(updated_at AS TEXT)
              END
            FROM global_user_queue_old;
            """
        )

        conn.execute("DROP TABLE user_profile_old;")
        conn.execute("DROP TABLE user_tasks_old;")
        conn.execute("DROP TABLE global_user_queue_old;")

        conn.execute("CREATE INDEX IF NOT EXISTS idx_user_tasks_owner_status ON user_tasks(owner_id, status, created_at);")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_user_tasks_owner_key ON user_tasks(owner_id, task_key);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_global_user_queue_enqueued ON global_user_queue(enqueued_at);")

        conn.execute("COMMIT;")
    except Exception:
        try:
            conn.execute("ROLLBACK;")
        except Exception:
            pass
        raise


def _task_key(task: dict) -> str:
    t = str(task.get("type") or "chapter")
    if t == "chapter":
        return f"chapter:{str(task.get('course_id') or '')}"
    if t == "exam":
        exam = task.get("exam") if isinstance(task.get("exam"), dict) else {}
        return f"exam:{str(exam.get('exam_id') or '')}"
    return f"{t}:{json.dumps(task, ensure_ascii=False, sort_keys=True)}"


def upsert_user_profile(project_root: Path, owner_id: str, config_id: str | None = None, client_id: str | None = None, selected_task: dict | None = None) -> None:
    now = now_str()
    conn = _connect(project_root)
    try:
        row = conn.execute("SELECT owner_id FROM user_profile WHERE owner_id=?", (owner_id,)).fetchone()
        selected_json = json.dumps(selected_task, ensure_ascii=False) if isinstance(selected_task, dict) else None
        if row:
            updates = []
            args = []
            if config_id is not None:
                updates.append("config_id=?")
                args.append(str(config_id))
            if client_id is not None:
                updates.append("last_client_id=?")
                args.append(str(client_id))
            if selected_json is not None:
                updates.append("selected_task_json=?")
                args.append(selected_json)
            updates.append("updated_at=?")
            args.append(now)
            args.append(owner_id)
            conn.execute(f"UPDATE user_profile SET {', '.join(updates)} WHERE owner_id=?", args)
        else:
            conn.execute(
                "INSERT INTO user_profile(owner_id, config_id, last_client_id, selected_task_json, requeue_after_current, updated_at) VALUES(?,?,?,?,?,?)",
                (owner_id, str(config_id) if config_id is not None else None, str(client_id) if client_id is not None else None, selected_json, 0, now),
            )
    finally:
        conn.close()


def set_requeue_after_current(project_root: Path, owner_id: str, value: bool) -> None:
    now = now_str()
    conn = _connect(project_root)
    try:
        conn.execute(
            "INSERT INTO user_profile(owner_id, updated_at) VALUES(?,?) ON CONFLICT(owner_id) DO UPDATE SET requeue_after_current=?, updated_at=?",
            (owner_id, now, 1 if value else 0, now),
        )
    finally:
        conn.close()


def pop_requeue_after_current(project_root: Path, owner_id: str) -> bool:
    conn = _connect(project_root)
    try:
        row = conn.execute("SELECT requeue_after_current FROM user_profile WHERE owner_id=?", (owner_id,)).fetchone()
        flag = bool(row and int(row["requeue_after_current"] or 0) == 1)
        if flag:
            conn.execute("UPDATE user_profile SET requeue_after_current=0, updated_at=? WHERE owner_id=?", (now_str(), owner_id))
        return flag
    finally:
        conn.close()


def enqueue_user(project_root: Path, owner_id: str, enqueued_at: float | str | None = None) -> None:
    now = now_str()
    if enqueued_at is None:
        ts = now
    elif isinstance(enqueued_at, (int, float)):
        ts = datetime.fromtimestamp(float(enqueued_at)).strftime("%Y-%m-%d %H:%M:%S")
    else:
        ts = str(enqueued_at)
    conn = _connect(project_root)
    try:
        conn.execute(
            "INSERT INTO global_user_queue(owner_id, enqueued_at, updated_at) VALUES(?,?,?) ON CONFLICT(owner_id) DO UPDATE SET enqueued_at=?, updated_at=?",
            (owner_id, ts, now, ts, now),
        )
    finally:
        conn.close()


def dequeue_user(project_root: Path, owner_id: str) -> None:
    conn = _connect(project_root)
    try:
        conn.execute("DELETE FROM global_user_queue WHERE owner_id=?", (owner_id,))
    finally:
        conn.close()


def get_user_queue_position(project_root: Path, owner_id: str) -> int | None:
    conn = _connect(project_root)
    try:
        exists = conn.execute("SELECT 1 FROM global_user_queue WHERE owner_id=?", (owner_id,)).fetchone()
        if not exists:
            return None
        rows = conn.execute("SELECT owner_id FROM global_user_queue ORDER BY enqueued_at ASC").fetchall()
        for idx, row in enumerate(rows):
            if row["owner_id"] == owner_id:
                return idx + 1
        return None
    finally:
        conn.close()


def list_next_users(project_root: Path, limit: int = 50) -> list[str]:
    conn = _connect(project_root)
    try:
        rows = conn.execute("SELECT owner_id FROM global_user_queue ORDER BY enqueued_at ASC LIMIT ?", (int(limit),)).fetchall()
        return [str(r["owner_id"]) for r in rows]
    finally:
        conn.close()


def add_tasks(project_root: Path, owner_id: str, tasks: list[dict]) -> int:
    now = now_str()
    conn = _connect(project_root)
    added = 0
    try:
        for task in tasks:
            if not isinstance(task, dict):
                continue
            key = _task_key(task)
            raw = json.dumps(task, ensure_ascii=False)
            cur = conn.execute("SELECT id, status FROM user_tasks WHERE owner_id=? AND task_key=?", (owner_id, key)).fetchone()
            if cur:
                if str(cur["status"]) in {"completed", "failed", "stopped"}:
                    conn.execute(
                        "UPDATE user_tasks SET task_json=?, status='pending', updated_at=?, started_at=NULL, finished_at=NULL, last_error=NULL WHERE id=?",
                        (raw, now, int(cur["id"])),
                    )
                    added += 1
                continue
            conn.execute(
                "INSERT INTO user_tasks(owner_id, task_key, task_json, status, created_at, updated_at) VALUES(?,?,?,?,?,?)",
                (owner_id, key, raw, "pending", now, now),
            )
            added += 1
        return added
    finally:
        conn.close()


def remove_task(project_root: Path, owner_id: str, task_id: int) -> bool:
    conn = _connect(project_root)
    try:
        row = conn.execute("SELECT id FROM user_tasks WHERE id=? AND owner_id=? AND status='pending'", (int(task_id), owner_id)).fetchone()
        if not row:
            return False
        conn.execute("DELETE FROM user_tasks WHERE id=? AND owner_id=? AND status='pending'", (int(task_id), owner_id))
        return True
    finally:
        conn.close()


def clear_pending_tasks(project_root: Path, owner_id: str) -> int:
    conn = _connect(project_root)
    try:
        cur = conn.execute("DELETE FROM user_tasks WHERE owner_id=? AND status='pending'", (owner_id,))
        return int(cur.rowcount or 0)
    finally:
        conn.close()


def get_next_pending_task(project_root: Path, owner_id: str) -> dict | None:
    conn = _connect(project_root)
    try:
        row = conn.execute(
            "SELECT id, task_json FROM user_tasks WHERE owner_id=? AND status='pending' ORDER BY created_at ASC, id ASC LIMIT 1",
            (owner_id,),
        ).fetchone()
        if not row:
            return None
        return {"id": int(row["id"]), "task": json.loads(row["task_json"])}
    finally:
        conn.close()


def mark_task_running(project_root: Path, owner_id: str, task_id: int) -> None:
    now = now_str()
    conn = _connect(project_root)
    try:
        conn.execute(
            "UPDATE user_tasks SET status='running', updated_at=?, started_at=? WHERE id=? AND owner_id=?",
            (now, now, int(task_id), owner_id),
        )
    finally:
        conn.close()


def mark_task_finished(project_root: Path, owner_id: str, task_id: int, status: str, message: str = "") -> None:
    now = now_str()
    status = str(status or "completed")
    if status not in {"completed", "failed", "stopped", "error"}:
        status = "completed"
    conn = _connect(project_root)
    try:
        conn.execute(
            "UPDATE user_tasks SET status=?, updated_at=?, finished_at=?, last_error=? WHERE id=? AND owner_id=?",
            (status, now, now, (message or "")[:8000], int(task_id), owner_id),
        )
    finally:
        conn.close()


def get_queue_snapshot(project_root: Path, owner_id: str, limit: int = 30) -> dict:
    conn = _connect(project_root)
    try:
        counts = conn.execute(
            """
            SELECT
              SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) AS pending,
              SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) AS completed,
              SUM(CASE WHEN status IN ('failed','error') THEN 1 ELSE 0 END) AS failed
            FROM user_tasks
            WHERE owner_id=?
            """,
            (owner_id,),
        ).fetchone()
        pending = int((counts["pending"] or 0) if counts else 0)
        completed = int((counts["completed"] or 0) if counts else 0)
        failed = int((counts["failed"] or 0) if counts else 0)
        items_rows = conn.execute(
            """
            SELECT
              id,
              task_json,
              created_at,
              CASE
                WHEN typeof(created_at) IN ('real','integer') THEN created_at
                ELSE CAST(strftime('%s', created_at) AS REAL)
              END AS created_epoch
            FROM user_tasks
            WHERE owner_id=? AND status='pending'
            ORDER BY created_at ASC, id ASC
            LIMIT ?
            """,
            (owner_id, int(limit)),
        ).fetchall()
        items = []
        for row in items_rows:
            try:
                task = json.loads(row["task_json"])
            except Exception:
                task = {}
            items.append(
                {
                    "queue_id": str(int(row["id"])),
                    "type": task.get("type") or "chapter",
                    "name": task.get("name") or f"课程 {task.get('course_id') or ''}".strip(),
                    "course_id": task.get("course_id"),
                    "class_id": task.get("class_id"),
                    "enqueued_at": float(row["created_epoch"] or 0),
                }
            )
        return {
            "total": pending + completed + failed,
            "completed": completed,
            "failed": failed,
            "pending": pending,
            "items": items,
        }
    finally:
        conn.close()


def append_user_log(
    project_root: Path,
    owner_id: str,
    message: str,
    level: str = "info",
    keep_limit: int = 200,
    created_at: str | None = None,
) -> None:
    owner_id = str(owner_id or "").strip()
    if not owner_id:
        return
    now = str(created_at or now_str())
    conn = _connect(project_root)
    try:
        conn.execute(
            "INSERT INTO user_task_logs(owner_id, level, message, created_at) VALUES(?,?,?,?)",
            (owner_id, str(level or "info"), str(message or ""), now),
        )
        conn.execute(
            """
            DELETE FROM user_task_logs
            WHERE owner_id=?
              AND id NOT IN (
                SELECT id
                FROM user_task_logs
                WHERE owner_id=?
                ORDER BY id DESC
                LIMIT ?
              )
            """,
            (owner_id, owner_id, max(1, int(keep_limit))),
        )
    finally:
        conn.close()


def get_recent_user_logs(project_root: Path, owner_id: str, limit: int = 80) -> list[dict]:
    owner_id = str(owner_id or "").strip()
    if not owner_id:
        return []
    conn = _connect(project_root)
    try:
        rows = conn.execute(
            """
            SELECT
              level,
              message,
              created_at,
              CAST(strftime('%s', created_at) AS REAL) AS created_epoch
            FROM user_task_logs
            WHERE owner_id=?
            ORDER BY id DESC
            LIMIT ?
            """,
            (owner_id, max(1, int(limit))),
        ).fetchall()
        items = []
        for row in reversed(rows):
            items.append(
                {
                    "session_id": owner_id,
                    "level": str(row["level"] or "info"),
                    "message": str(row["message"] or ""),
                    "timestamp": float(row["created_epoch"] or 0),
                    "created_at": str(row["created_at"] or ""),
                }
            )
        return items
    finally:
        conn.close()


def get_selected_task(project_root: Path, owner_id: str) -> dict | None:
    conn = _connect(project_root)
    try:
        row = conn.execute("SELECT selected_task_json FROM user_profile WHERE owner_id=?", (owner_id,)).fetchone()
        if not row:
            return None
        raw = row["selected_task_json"]
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return None
    finally:
        conn.close()


def get_user_profile(project_root: Path, owner_id: str) -> dict:
    conn = _connect(project_root)
    try:
        row = conn.execute(
            "SELECT owner_id, config_id, last_client_id, selected_task_json, requeue_after_current, updated_at FROM user_profile WHERE owner_id=?",
            (owner_id,),
        ).fetchone()
        if not row:
            return {"owner_id": owner_id, "config_id": None, "last_client_id": None, "selected_task": None}
        selected = None
        raw = row["selected_task_json"]
        if raw:
            try:
                selected = json.loads(raw)
            except Exception:
                selected = None
        return {
            "owner_id": str(row["owner_id"]),
            "config_id": row["config_id"],
            "last_client_id": row["last_client_id"],
            "selected_task": selected,
            "requeue_after_current": int(row["requeue_after_current"] or 0),
            "updated_at": row["updated_at"],
        }
    finally:
        conn.close()


def clear_selected_task(project_root: Path, owner_id: str) -> None:
    now = now_str()
    conn = _connect(project_root)
    try:
        conn.execute(
            "INSERT INTO user_profile(owner_id, updated_at) VALUES(?,?) ON CONFLICT(owner_id) DO UPDATE SET selected_task_json=NULL, updated_at=?",
            (owner_id, now, now),
        )
    finally:
        conn.close()


def has_user_config(project_root: Path, owner_id: str) -> bool:
    conn = _connect(project_root)
    try:
        row = conn.execute("SELECT 1 FROM user_config WHERE owner_id=?", (owner_id,)).fetchone()
        return bool(row)
    finally:
        conn.close()


def get_user_config(project_root: Path, owner_id: str) -> dict | None:
    conn = _connect(project_root)
    try:
        row = conn.execute("SELECT config_json FROM user_config WHERE owner_id=?", (owner_id,)).fetchone()
        if not row:
            return None
        raw = row["config_json"]
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return None
    finally:
        conn.close()


def save_user_config(project_root: Path, owner_id: str, config_dict: dict) -> None:
    raw = json.dumps(config_dict, ensure_ascii=False)
    now = now_str()
    conn = _connect(project_root)
    try:
        conn.execute(
            "INSERT INTO user_config(owner_id, config_json, updated_at) VALUES(?,?,?) ON CONFLICT(owner_id) DO UPDATE SET config_json=?, updated_at=?",
            (owner_id, raw, now, raw, now),
        )
    finally:
        conn.close()


def migrate_owner(project_root: Path, old_owner_id: str, new_owner_id: str) -> None:
    old_owner_id = str(old_owner_id or "").strip()
    new_owner_id = str(new_owner_id or "").strip()
    if not old_owner_id or not new_owner_id or old_owner_id == new_owner_id:
        return
    conn = _connect(project_root)
    try:
        conn.execute("BEGIN;")
        try:
            old_profile = conn.execute("SELECT * FROM user_profile WHERE owner_id=?", (old_owner_id,)).fetchone()
            new_profile = conn.execute("SELECT * FROM user_profile WHERE owner_id=?", (new_owner_id,)).fetchone()
            if old_profile and not new_profile:
                conn.execute("UPDATE user_profile SET owner_id=?, updated_at=? WHERE owner_id=?", (new_owner_id, now_str(), old_owner_id))
            elif old_profile and new_profile:
                if (not new_profile["selected_task_json"]) and old_profile["selected_task_json"]:
                    conn.execute(
                        "UPDATE user_profile SET selected_task_json=?, updated_at=? WHERE owner_id=?",
                        (old_profile["selected_task_json"], now_str(), new_owner_id),
                    )
                conn.execute("DELETE FROM user_profile WHERE owner_id=?", (old_owner_id,))

            old_cfg = conn.execute("SELECT config_json FROM user_config WHERE owner_id=?", (old_owner_id,)).fetchone()
            new_cfg = conn.execute("SELECT config_json FROM user_config WHERE owner_id=?", (new_owner_id,)).fetchone()
            if old_cfg and not new_cfg:
                conn.execute("UPDATE user_config SET owner_id=?, updated_at=? WHERE owner_id=?", (new_owner_id, now_str(), old_owner_id))
            elif old_cfg and new_cfg:
                conn.execute("DELETE FROM user_config WHERE owner_id=?", (old_owner_id,))

            old_queue = conn.execute("SELECT enqueued_at FROM global_user_queue WHERE owner_id=?", (old_owner_id,)).fetchone()
            if old_queue:
                conn.execute("DELETE FROM global_user_queue WHERE owner_id=?", (old_owner_id,))
                conn.execute(
                    "INSERT INTO global_user_queue(owner_id, enqueued_at, updated_at) VALUES(?,?,?) ON CONFLICT(owner_id) DO UPDATE SET enqueued_at=excluded.enqueued_at, updated_at=excluded.updated_at",
                    (new_owner_id, old_queue["enqueued_at"], now_str()),
                )

            old_tasks = conn.execute(
                "SELECT task_key, task_json, status, created_at, updated_at, started_at, finished_at, last_error FROM user_tasks WHERE owner_id=? ORDER BY id ASC",
                (old_owner_id,),
            ).fetchall()
            for row in old_tasks:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO user_tasks(owner_id, task_key, task_json, status, created_at, updated_at, started_at, finished_at, last_error)
                    VALUES(?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        new_owner_id,
                        row["task_key"],
                        row["task_json"],
                        row["status"],
                        row["created_at"],
                        row["updated_at"],
                        row["started_at"],
                        row["finished_at"],
                        row["last_error"],
                    ),
                )
            if old_tasks:
                conn.execute("DELETE FROM user_tasks WHERE owner_id=?", (old_owner_id,))

            conn.execute("COMMIT;")
        except Exception:
            try:
                conn.execute("ROLLBACK;")
            except Exception:
                pass
            raise
    finally:
        conn.close()
