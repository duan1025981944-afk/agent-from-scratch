"""会话生命周期：新建、恢复、列出、保存一轮。

这一层不知道 jsonl 存在——换成数据库，这个文件一行都不用改。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from session import messages as store


@dataclass
class Session:
    """一次运行绑定的那个会话。"""
    key: str
    meta: dict[str, Any] = field(default_factory=dict)
    history: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, Any] | None = None                         # 最后一条摘要记录
 
    @property
    def covered(self) -> int:
        """摘要已经代表了 history 前面多少条。"""
        return self.summary["covered"] if self.summary else 0

    def workspace_changed(self, current_root: str) -> str | None:
        """工作区变了就返回"当时的工作区"，没变返回 None。

        注意：只返回事实，不打印。打印是 main 的事。
        """
        old = self.meta.get("workspace")
        return old if old and old != current_root else None

    def save_turn(self, new_messages: list[dict[str, Any]]) -> None:
        store.append_messages(self.key, new_messages)

    def save_summary(self, summary: str, covered: int) -> None:
        """记下一条摘要。原文不动，只追加一张便条。"""
        store.append_summary(self.key, summary, covered)
        self.summary = {"text": summary, "covered": covered}


def create(model: str, workspace: str) -> Session:
    """新建会话：生成 key + 写文件头。"""
    key = datetime.now().strftime("%Y%m%d-%H%M%S")
    meta = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "model": model,
        "workspace": workspace,
    }
    store.write_meta(key, meta)
    return Session(key=key, meta=meta, history=[])


def resume(key: str) -> Session:
    """恢复已有会话。"""
    return Session(
        key=key,
        meta=store.load_meta(key) or {},
        history=store.load_messages(key),
        summary=store.load_summary(key),
    )


def list_all() -> list[tuple[str, dict[str, Any], int]]:
    """[(key, meta, 消息条数)]，新的在前。"""
    return [
        (key, store.load_meta(key) or {}, store.count_messages(key))
        for key in store.list_keys()
    ]