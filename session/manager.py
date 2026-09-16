"""会话生命周期：新建、恢复、列出、保存一轮。

──────────────────────────────────────────────────────────────
这个文件是什么
──────────────────────────────────────────────────────────────
**唯一懂"会话"这个概念的一层。** 它知道：

    一个会话有身份（key）、有来历（meta）、有历史（history）、可能有摘要（summary）
    会话可以被新建、被恢复、被列出、被追加一轮

**它不知道 jsonl 存在。** 所有跟磁盘打交道的活儿都转给 session/messages.py，
全程只通过 store.xxx() 调用。

检验方法：想把存储从 jsonl 换成 SQLite，**这个文件一行都不用改**。

──────────────────────────────────────────────────────────────
它上面和下面各是谁
──────────────────────────────────────────────────────────────
    main.py            知道怎么打印、命令行参数      <- 不知道会话存在哪
        |
    manager.py（本文件） 知道会话有生命周期            <- 不知道 jsonl 是什么
        |
    messages.py        知道怎么读写 jsonl            <- 不知道"会话"是什么概念

──────────────────────────────────────────────────────────────
一个边界要记清楚：摘要的"格式"不归这一层管
──────────────────────────────────────────────────────────────
这一层只知道"会话带一条摘要记录 = 一段文字 + 一个数字"。

至于那段文字**变成消息之后**长什么样（role 是 system、开头是
"[以下是更早对话的摘要]"），是 agent/compaction.py 的内部约定，
这里完全不知道。

好处：哪天想把摘要消息改成 user 角色，只动 compaction.py，
存储层和 manager 一个字都不用改。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# 别名 store：本文件全程只通过它访问磁盘，绝不自己 open 文件。
# 这条自律就是"换存储只改一个文件"的全部保障——一旦这里出现 open()，
# 分层就破了。
from session import messages as store


@dataclass
class Session:
    """一次运行绑定的那个会话。

    谁会产生它
        本文件的 create()（新建）和 resume()（恢复）。**不要自己 new。**

    谁会用它
        main.py。整轮流程里会用到它的 key、history、summary、covered，
        以及 save_turn() / save_summary() / workspace_changed()。

    字段
        key:      会话标识，也是文件名（不含扩展名），形如 "20260915-183243"。

        meta:     文件头信息，形如
                      {"created_at": "2026-09-15T18:32:43",
                       "model": "deepseek-flash",
                       "workspace": "D:\\\\VibeCoding\\\\Agent\\\\mini_nanobot"}
                  读不到时是空字典 {}，不是 None——调用方可以直接 .get()。

        history:  **存档全文**，按写入顺序。注意三点：
                      · 不含 system 消息（那只活在内存里）
                      · 不含 _meta、_summary（那两种没有 role）
                      · **不是**能直接发给模型的那份 —— 要先过
                        agent/compaction.py 的 restore_history()

        summary:  最后一条摘要便条，形如 {"text": ..., "covered": 15, "at": ...}。
                  **从没压缩过的会话是 None。**

    一个容易踩空的地方
        save_turn() 只往磁盘追加，**不会更新内存里的 self.history**。
        所以存完之后 session.history 还是刚打开时那份。

        这是有意的：main.py 自己维护着一份更全的 messages（含 system、
        含摘要消息），没必要在两个地方同步同一份数据。但如果你写新代码时
        指望 save 完 session.history 就长了，会踩空。

    例子
        >>> s = Session(key="20260915-183243")
        >>> s.meta, s.history, s.summary
        ({}, [], None)
        >>> s.covered                      # 没摘要时是 0，不用写 if
        0
    """

    key: str
    meta: dict[str, Any] = field(default_factory=dict)
    history: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, Any] | None = None

    @property
    def covered(self) -> int:
        """摘要已经代表了 history 前面多少条消息。

        返回什么
            整数。**没有摘要时返回 0**，而不是 None。

        为什么要包这一层
            调用方（main.py 和 compaction.py）关心的永远是那个数字，
            不关心"有没有摘要"。包一层之后，它们直接写

                compact_if_needed(messages, provider, covered=session.covered)

            而不用每次都写 `session.summary["covered"] if session.summary else 0`。

        例子
            >>> Session(key="k").covered
            0
            >>> Session(key="k", summary={"text": "…", "covered": 15}).covered
            15
        """
        return self.summary["covered"] if self.summary else 0

    def workspace_changed(self, current_root: str) -> str | None:
        """检查这个会话当初的工作区和现在是不是同一个。

        谁会用它
            main.py 的 open_session()，恢复会话时调一次。

        传入什么
            current_root: 现在的工作区绝对路径（str(workspace.get_root())）。

        返回什么
            变了   -> 返回**当时**那个工作区路径（一个字符串）
            没变   -> 返回 None
            meta 里根本没记 workspace -> 也返回 None（老会话没这个字段）

        拿到之后怎么用
            配合海象运算符写得很紧凑：

                if old := session.workspace_changed(str(root)):
                    print(f"[警告] 这个会话当时的工作区是：{old}")

        为什么要有这个检查
            历史里的路径都是相对的（"agent/runner.py"）。换个目录启动的话，
            模型照着历史去读文件会读不到、或者读到另一个项目的同名文件。
            提前警告一句，比让它困惑半天强。

        注意：**只返回事实，不打印。** 打印是 main.py 的事——这一层
        不该知道有没有终端、用什么语言、要不要加颜色。

        例子
            >>> s = Session(key="k", meta={"workspace": "D:/old"})
            >>> s.workspace_changed("D:/old") is None          # 没变
            True
            >>> s.workspace_changed("D:/new")                  # 变了
            'D:/old'
            >>> Session(key="k").workspace_changed("D:/any") is None   # 老会话没记
            True
        """
        old = self.meta.get("workspace")
        return old if old and old != current_root else None

    def save_turn(self, new_messages: list[dict[str, Any]]) -> None:
        """把**这一轮新长出来的**几条消息落盘。

        谁会用它
            main.py，每轮 runner 跑完之后调一次：

                session.save_turn(messages[boundary:])

        传入什么
            new_messages: 只传新增的那几条，**不是整份历史**。
                          typically 是 messages[boundary:]，其中 boundary 是
                          这一轮开始前（压缩之后）记下的列表长度。

        返回什么
            无。

        为什么是"整轮一次性写"，而不是每条都写
            这是不变量 2：**磁盘上不会出现半截轮次**。
            中途 Ctrl+C 的话，要么这一轮完整地在，要么完全不在，
            不会留下"有提问没回答"或者"有工具调用没有结果"的残缺文件。

            代价：一轮跑到一半崩溃，这一轮**整个丢掉**（包括用户输入）。
            这是有意的取舍——宁可丢一轮，不要留半轮。

        为什么系统提示和摘要消息不会被写进去
            它们都在 boundary 之前。main.py 是先压缩、再记 boundary、
            再 append 用户输入，所以切出来的那一段里天然没有它们。
        """
        store.append_messages(self.key, new_messages)

    def save_summary(self, summary: str, covered: int) -> None:
        """记下一条摘要便条。**原文不动，只追加。**

        谁会用它
            main.py，压缩发生之后调：

                if outcome.summary:                       # 压过才记
                    session.save_summary(outcome.summary, outcome.covered)

        传入什么
            summary: 摘要正文。
            covered: 这段摘要代表了存档前多少条消息。**累计值**，
                     不是这次新增的数量——算法见 agent/compaction.py。

        返回什么
            无。附带把 self.summary 也更新了，所以同一次运行里再压第二次时，
            session.covered 拿到的是最新值。

        内存和磁盘的形状差一个字段
            磁盘上那条带 at 时间戳，这里塞进内存的不带。
            目前没有任何代码读 at，所以不影响；但别依赖 session.summary["at"]
            一定存在——刚 save 完的那份没有它。
        """
        store.append_summary(self.key, summary, covered)
        self.summary = {"text": summary, "covered": covered}


def create(model: str, workspace: str) -> Session:
    """新建一个会话：生成 key，写文件头。

    谁会用它
        main.py 的 open_session()，没给 --session 时走这条路。

    传入什么
        model:     当前用的模型名，形如 "deepseek-flash"。写进文件头，
                   以后 --list 能看到这个会话当时用的是哪个模型。
        workspace: 工作区绝对路径。写进文件头，恢复时用来比对。

    返回什么
        一个新的 Session。history 是空的，summary 是 None。

    附带做了什么
        往磁盘写了一行 _meta。所以**调用它就已经产生了文件**，
        哪怕用户一句话都没说就退出，也会留下一个只有文件头的空会话。

    key 是怎么生成的
        datetime.now().strftime("%Y%m%d-%H%M%S") -> "20260915-183243"

        选这个格式有两个理由：人一眼能看出是什么时候建的；
        **字典序恰好等于时间序**，所以 list_keys() 直接按文件名倒序排
        就是"新的在前"，不用读文件的修改时间。

    已知短板：**精度只到秒**
        同一秒内建两个会话会拿到同一个 key，于是共用同一个文件，
        第二条 _meta 被追加进去、然后被 load_meta 静默忽略。
        单人手敲命令碰不到，脚本批量起就会中招。记在待办里，还没修。
    """
    key = datetime.now().strftime("%Y%m%d-%H%M%S")
    meta = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "model": model,
        "workspace": workspace,
    }
    store.write_meta(key, meta)
    return Session(key=key, meta=meta, history=[])


def resume(key: str) -> Session:
    """恢复一个已有会话：把三样东西一起捞回来。

    谁会用它
        main.py 的 open_session()，给了 --session 时走这条路。

    传入什么
        key: 会话标识。**不检查它存不存在**——不存在的话，
             三个 load 各自返回空值，你会得到一个空的 Session
             （key 是你给的，其余都是空）。

    返回什么
        一个 Session：
            meta    读不到时是 {}
            history 读不到时是 []
            summary 没压缩过是 None

    拿到之后怎么用
        history 是**存档全文**，还不能直接用。要变成能发给模型的那份：

            messages = [{"role": "system", "content": SYSTEM_PROMPT}] + \\
                       restore_history(session.history, session.summary)

        restore_history 在 agent/compaction.py：它按 summary["covered"]
        把前面那段换成一条摘要消息。

    这个函数读了三遍文件
        load_meta、load_messages、load_summary 各扫一次。三遍其实可以合成一遍，
        但**分开写换来的是分层干净**——每个底层函数只干一件事，
        manager 这里读起来就是一句话："恢复会话 = 三个 load 拼起来"。
        会话文件通常几十 KB，多扫两遍无所谓。
    """
    return Session(
        key=key,
        meta=store.load_meta(key) or {},
        history=store.load_messages(key),
        summary=store.load_summary(key),
    )


def list_all() -> list[tuple[str, dict[str, Any], int]]:
    """列出所有会话的摘要信息，新的在前。

    谁会用它
        main.py 的 print_session_list()，也就是 --list。

    传入什么
        无。

    返回什么
        一个列表，每项是三元组 (key, meta, 消息条数)：

            [("20260915-183243", {"model": "deepseek-flash", ...}, 38),
             ("20260913-104035", {"model": "deepseek-flash", ...}, 126)]

        没有任何会话时返回空列表。

    拿到之后怎么用
        三元组可以在 for 里直接拆开：

            for key, meta, count in manager.list_all():
                print(key, count, meta.get("model", "?"))

        meta 用 .get() 取值，因为老会话可能缺字段。

    为什么不返回 Session 对象
        --list 只需要三样信息，返回 Session 就意味着要把每个会话的**全部历史**
        都读进内存。几十个会话、每个上百 KB，光为了打一张表，不值当。

    成本：**会话数 × 文件大小**
        每个 key 都要 load_meta（读一行，便宜）+ count_messages（扫全文件，贵）。
        会话多了会明显变慢。记在待办里，暂时没优化——真慢了再说。
    """
    return [
        (key, store.load_meta(key) or {}, store.count_messages(key))
        for key in store.list_keys()
    ]
