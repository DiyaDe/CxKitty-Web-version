import json
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import config
from cxapi.schema import AccountInfo


@dataclass
class SessionModule:
    """会话数据模型"""
    phone: str
    puid: int
    passwd: Optional[str]
    name: str
    ck: str

def dict2ck(dict_ck: dict[str, str]) -> str:
    """序列化dict形式的ck
    Args:
        dict_ck: cookie键值对
    Returns:
    """
    return "".join(f"{k}={v};" for k, v in dict_ck.items())

def ck2dict(ck: str) -> dict[str, str]:
    """解析ck到dict
    Args:
        ck: 序列化cookie字符串
    Returns:
        dict[str, str]: cookie键值对
    """
    result = {}
    for field in ck.strip().split(";"):
        if not field:
            continue
        k, v = field.split("=")
        result[k] = v
    return result

def save_session(ck: dict, acc: AccountInfo, passwd: Optional[str] = None) -> None:
    """存档会话数据为json
    Args:
        ck: cookie
        acc: 用户信息
        passwd: 密码
    """
    return save_session_scoped(ck, acc, passwd=passwd, scope=None)


def save_session_scoped(ck: dict, acc: AccountInfo, passwd: Optional[str] = None, scope: Optional[str] = None) -> None:
    """存档会话数据为json（支持按 scope 隔离）
    Args:
        ck: cookie
        acc: 用户信息
        passwd: 密码
        scope: 隔离标识（例如 Web 端每个浏览器会话的 client_id）；None 表示使用旧的全局路径
    """
    base_dir = config.SESSIONS_PATH / scope if scope else config.SESSIONS_PATH
    if not base_dir.is_dir():
        base_dir.mkdir(parents=True)
    file_path = base_dir / f"{acc.phone}.json"
    with open(file_path, "w", encoding="utf8") as fp:
        sessdata = {
            "phone": acc.phone,
            "puid": acc.puid,
            "passwd": passwd,
            "name": acc.name,
            "ck": dict2ck(ck),
        }
        json.dump(sessdata, fp, ensure_ascii=False)

def sessions_load() -> list[SessionModule]:
    """从路径批量读档会话
    Returns:
        list[SessionModule]: 会话模型列表
    """
    return sessions_load_scoped(scope=None)


def sessions_load_scoped(scope: Optional[str] = None) -> list[SessionModule]:
    """从路径批量读档会话（支持按 scope 隔离）
    Args:
        scope: 隔离标识；None 表示使用旧的全局路径
    Returns:
        list[SessionModule]: 会话模型列表
    """
    sessions: list[SessionModule] = []
    base_dir = config.SESSIONS_PATH / scope if scope else config.SESSIONS_PATH
    if not base_dir.is_dir():
        return []
    for file in base_dir.iterdir():
        if file.suffix != ".json":
            continue
        with open(file, "r", encoding="utf8") as fp:
            sessdata = json.load(fp)
        sessions.append(
            SessionModule(
                phone=sessdata["phone"],
                puid=sessdata["puid"],
                passwd=sessdata.get("passwd"),
                name=sessdata["name"],
                ck=sessdata["ck"],
            )
        )
    return sessions

def mask_name(name: str) -> str:
    """打码姓名
    Args:
        name: 姓名 (大于2汉字)
    Returns:
        str: 打码后的姓名
    """
    return name[0] + ("*" * (len(name) - 2) + name[-1] if len(name) > 2 else "*")

def mask_phone(phone: str) -> str:
    """打码手机号
    Args:
        phone: 手机号 (必须为11位)
    Returns:
        str: 打码后的手机号
    """
    return phone[:3] + "****" + phone[-4:]

def get_face_path_by_puid(puid: int) -> Path | None:
    """获取并随机选择该 puid 所属的人脸图片路径
    Args:
        puid: 用户 puid
    Returns:
        Path: 选定的人脸图片路径, 不存在时为 None
    """
    matched_image = []
    for f in config.FACE_PATH.glob(f'{puid}*.jpg'):
        if re.match(r'\d+(_\d+)?', f.stem):
            matched_image.append(f)
    if matched_image:
        return random.choice(matched_image)
    return None

__all__ = [
    "save_session",
    "save_session_scoped",
    "SessionModule",
    "ck2dict",
    "sessions_load",
    "sessions_load_scoped",
    "mask_name",
    "mask_phone",
    "get_face_path_by_puid",
]
