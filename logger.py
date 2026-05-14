import logging
import threading

import config

_runtime_log = threading.local()
_log_emitter = None


def set_log_emitter(emitter):
    global _log_emitter
    _log_emitter = emitter


def set_log_session_id(session_id: str):
    _runtime_log.session_id = session_id


def get_log_session_id() -> str:
    return getattr(_runtime_log, "session_id", "")


def clear_log_session_id():
    try:
        delattr(_runtime_log, "session_id")
    except Exception:
        pass

def set_log_filename(phone: str):
    """设置日志文件名
    Args:
        phone: 当前会话的手机号
    """
    _runtime_log.log_file_name = phone


def get_log_filename() -> str:
    return getattr(_runtime_log, "log_file_name", "")


class EmitHandler(logging.Handler):
    def emit(self, record):
        if _log_emitter is None:
            return
        session_id = get_log_session_id()
        if not session_id:
            return
        levelno = int(getattr(record, "levelno", logging.INFO))
        if levelno >= logging.ERROR:
            level = "error"
        elif levelno >= logging.WARNING:
            level = "warning"
        else:
            level = "info"
        try:
            _log_emitter(session_id, record.name, level, record.getMessage())
        except Exception:
            pass

class Logger:
    """日志记录类
    """
    def __init__(self, name: str, level=logging.DEBUG, fmt=None) -> None:
        """constructor
        Args:
            name: 模块名
            level: 日志级别
            fmt: 日志格式
        """
        self.level = level
        if fmt is None:
            self.fmt = "%(asctime)s [%(name)s] %(levelname)s -> %(message)s"
        else:
            self.fmt = fmt
        
        if not config.LOGS_PATH.is_dir():
            config.LOGS_PATH.mkdir(parents=True)
        self.logger = logging.Logger(name)
        self.logger.setLevel(self.level)
        self.logger.propagate = False
        
        self.load_handler()
    
    def load_handler(self):
        """重载日志记录器实现
        """
        log_file_name = get_log_filename()
        if log_file_name and (not any(isinstance(h, logging.FileHandler) for h in self.logger.handlers)):
            fh = logging.FileHandler(config.LOGS_PATH / f"xuexitong_{log_file_name}.log", encoding="utf8")
            fh.setLevel(self.level)
            fh.setFormatter(logging.Formatter(self.fmt))
            self.logger.addHandler(fh)

        if _log_emitter is not None and (not any(isinstance(h, EmitHandler) for h in self.logger.handlers)):
            eh = EmitHandler()
            eh.setLevel(self.level)
            self.logger.addHandler(eh)

    def debug(self, msg) -> None:
        """输出 debug 级别日志
        Args:
            msg: 日志信息
        """
        self.load_handler()
        self.logger.debug(msg)

    def info(self, msg) -> None:
        """输出 info 级别日志
        Args:
            msg: 日志信息
        """
        self.load_handler()
        self.logger.info(msg)

    def warning(self, msg) -> None:
        """输出 warning 级别日志
        Args:
            msg: 日志信息
        """
        self.load_handler()
        self.logger.warning(msg)

    def error(self, msg, exc_info=False) -> None:
        """输出 error 级别日志
        Args:
            msg: 日志信息
            exc_info: 是否输出异常调用栈
        """
        self.load_handler()
        self.logger.error(msg, exc_info=exc_info)

__all__ = [
    "set_log_filename",
    "set_log_emitter",
    "set_log_session_id",
    "clear_log_session_id",
    "Logger",
]
