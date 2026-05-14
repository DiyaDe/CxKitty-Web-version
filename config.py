import copy
import json
import threading
import warnings
from pathlib import Path

import yaml

DEFAULT_CONFIG = {
    "mask_acc": True,
    "tui_max_height": 25,
    "fetch_uploaded_face": True,
    "session_path": "session/",
    "log_path": "logs/",
    "export_path": "export/",
    "face_image_path": "faces/",
    "video": {
        "enable": True,
        "wait": 15,
        "speed": 1.0,
        "report_rate": 58,
    },
    "work": {
        "enable": True,
        "export": False,
        "wait": 15,
        "fallback_fuzzer": False,
        "fallback_save": True,
    },
    "document": {
        "enable": True,
        "wait": 15,
    },
    "exam": {
        "fallback_fuzzer": False,
        "persubmit_delay": 15,
        "confirm_submit": True,
    },
    "searchers": [],
}

_runtime_local = threading.local()


def _deep_merge(base: dict, override: dict | None) -> dict:
    result = copy.deepcopy(base)
    if not override:
        return result
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def normalize_conf(data: dict | None) -> dict:
    return _deep_merge(DEFAULT_CONFIG, data or {})


PERSISTED_USER_CONFIG_KEYS = ("video", "work", "document", "exam", "searchers")


def db_conf_snapshot(data: dict | None) -> dict:
    normalized = normalize_conf(data or {})
    out = {}
    for key in PERSISTED_USER_CONFIG_KEYS:
        if key in normalized:
            out[key] = copy.deepcopy(normalized[key])
    return out


def _ensure_dirs(data: dict) -> None:
    for key in ("session_path", "log_path", "export_path", "face_image_path"):
        Path(data[key]).mkdir(parents=True, exist_ok=True)


try:
    with open("config.yml", "r", encoding="utf8") as fp:
        conf: dict = normalize_conf(yaml.load(fp, yaml.FullLoader) or {})
except FileNotFoundError:
    conf = normalize_conf({})
    warnings.warn("Config file not found", RuntimeWarning)

_ensure_dirs(conf)


def get_default_conf() -> dict:
    return copy.deepcopy(conf)


def get_effective_conf() -> dict:
    return copy.deepcopy(getattr(_runtime_local, "conf", conf))


def set_runtime_conf(runtime_conf: dict | None) -> dict:
    normalized = normalize_conf(runtime_conf)
    _ensure_dirs(normalized)
    _runtime_local.conf = normalized
    return normalized


def clear_runtime_conf() -> None:
    if hasattr(_runtime_local, "conf"):
        delattr(_runtime_local, "conf")


def get_effective_conf_signature() -> str:
    return json.dumps(get_effective_conf(), ensure_ascii=False, sort_keys=True)


def _lookup(path: tuple[str, ...], default=None):
    current = getattr(_runtime_local, "conf", conf)
    for key in path:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
        if current is None:
            return default
    return current


class PathProxy:
    def __init__(self, path: tuple[str, ...], default: str):
        self.path = path
        self.default = default

    def _value(self) -> Path:
        return Path(str(_lookup(self.path, self.default)))

    def __fspath__(self):
        return str(self._value())

    def __str__(self):
        return str(self._value())

    def __repr__(self):
        return repr(self._value())

    def __truediv__(self, other):
        return self._value() / other

    def __getattr__(self, item):
        return getattr(self._value(), item)


class ListProxy:
    def __init__(self, path: tuple[str, ...], default=None):
        self.path = path
        self.default = default or []

    def _value(self) -> list:
        value = _lookup(self.path, self.default)
        return copy.deepcopy(value if isinstance(value, list) else self.default)

    def __iter__(self):
        return iter(self._value())

    def __len__(self):
        return len(self._value())

    def __getitem__(self, index):
        return self._value()[index]

    def __bool__(self):
        return bool(self._value())

    def __repr__(self):
        return repr(self._value())


# 支持 Web 端按线程/用户隔离的动态路径和搜索器
SESSIONS_PATH = PathProxy(("session_path",), "session/")
LOGS_PATH = PathProxy(("log_path",), "logs/")
EXPORT_PATH = PathProxy(("export_path",), "export/")
FACE_PATH = PathProxy(("face_image_path",), "faces/")
SEARCHERS = ListProxy(("searchers",), [])
