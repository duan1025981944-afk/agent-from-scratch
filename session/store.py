"""会话存档：把 messages 一条一行写进 jsonl 文件。

阶段三检查点 1：先写死——不管会话 key 怎么来，只管读和写。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from datetime import datetime

# 会话文件统一放在 mini_nanobot/data/sessions/ 下
# __file__ 是本文件路径 → parent 是 session/ → parent.parent 是 mini_nanobot/
SESSIONS_DIR = Path(__file__).resolve().parent.parent / "data" / "sessions"


def _session_path(session_key: str) -> Path:
    """会话 key → 具体文件路径。"""
    return SESSIONS_DIR / f"{session_key}.jsonl"


def append_messages(session_key: str, messages: list[dict[str, Any]]) -> None:
    """把若干条消息追加到会话文件末尾。一条消息 = 一行。

    模式 "a" 是 append（追加），不是 "w"（覆盖写）——写错这个字母，
    每一轮都会把前面的记录全冲掉。
    """
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)  # 目录不存在就建，已存在不报错

    with _session_path(session_key).open("a", encoding="utf-8") as f:
        for msg in messages:
            # ensure_ascii=False：中文按原样写，不写成 \u4f60\u597d
            f.write(json.dumps(msg, ensure_ascii=False) + "\n")


def load_messages(session_key: str) -> list[dict[str, Any]]:
    """从会话文件读回全部消息。文件不存在就返回空列表（第一次运行）。"""
    path = _session_path(session_key)
    if not path.exists():
        return []

    messages: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:          # 跳过空行
                continue
            obj = json.loads(line)
            if "role" not in obj:
                continue
            messages.append(obj)
    return messages

def new_session_key() -> str:
    """用当前时间生成会话 key，如 20260909-143052。"""
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def write_meta(session_key: str, meta: dict[str, Any]) -> None:
    """写会话文件头。必须在任何消息之前调用（它得是第一行）。"""
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    with _session_path(session_key).open("a", encoding="utf-8") as f:
        f.write(json.dumps({"_meta": meta}, ensure_ascii=False) + "\n")


def load_meta(session_key: str) -> dict[str, Any] | None:
    """读会话文件头。读到第一行就返回，不用把整个文件读完。"""
    path = _session_path(session_key)
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                return json.loads(line).get("_meta")
    return None


def list_sessions() -> list[tuple[str, dict[str, Any], int]]:
    """列出所有会话：[(key, meta, 消息条数)]，新的在前。"""
    if not SESSIONS_DIR.exists():
        return []

    out = []
    for path in sorted(SESSIONS_DIR.glob("*.jsonl"), reverse=True):
        key = path.stem                       # 文件名去掉 .jsonl
        meta = load_meta(key) or {}
        with path.open("r", encoding="utf-8") as f:
            line_count = sum(1 for line in f if line.strip())
        out.append((key, meta, max(line_count - 1, 0)))   # 减掉 meta 那一行
    return out