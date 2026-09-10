"""会话文件的底层读写：key ↔ 文件，一行 ↔ 一个字典。

这一层不知道"会话"是什么，只知道怎么把字典存进 jsonl、怎么读回来。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SESSIONS_DIR = Path(__file__).resolve().parent.parent / "data" / "sessions"


def _path(session_key: str) -> Path:
    return SESSIONS_DIR / f"{session_key}.jsonl"


def append_messages(session_key: str, messages: list[dict[str, Any]]) -> None:
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    with _path(session_key).open("a", encoding="utf-8") as f:
        for msg in messages:
            f.write(json.dumps(msg, ensure_ascii=False) + "\n")


def load_messages(session_key: str) -> list[dict[str, Any]]:
    path = _path(session_key)
    if not path.exists():
        return []

    messages: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if "role" not in obj:          # 不是消息（如 _meta 头）
                continue
            messages.append(obj)
    return messages


def write_meta(session_key: str, meta: dict[str, Any]) -> None:
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    with _path(session_key).open("a", encoding="utf-8") as f:
        f.write(json.dumps({"_meta": meta}, ensure_ascii=False) + "\n")


def load_meta(session_key: str) -> dict[str, Any] | None:
    path = _path(session_key)
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                return json.loads(line).get("_meta")
    return None


def count_messages(session_key: str) -> int:
    """数消息条数，不把内容读进内存。"""
    path = _path(session_key)
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8") as f:
        return max(sum(1 for line in f if line.strip()) - 1, 0)   # 减掉 _meta 行


def list_keys() -> list[str]:
    """所有会话 key，新的在前。"""
    if not SESSIONS_DIR.exists():
        return []
    return [p.stem for p in sorted(SESSIONS_DIR.glob("*.jsonl"), reverse=True)]