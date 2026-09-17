"""会话文件的底层读写：key <-> 文件，一行 <-> 一个字典。

──────────────────────────────────────────────────────────────
这个文件是什么
──────────────────────────────────────────────────────────────
最底下那一层。它只懂两件事：

    一个 session_key  对应  data/sessions/{key}.jsonl 这个文件
    文件里的一行      对应  一个 JSON 字典

**它不知道"会话"是什么概念**，不知道什么叫"一轮对话"、什么叫"压缩"。
上面那些是 session/manager.py 的事。

检验方法：想把存储从 jsonl 换成 SQLite，**只需要重写这一个文件**。

──────────────────────────────────────────────────────────────
jsonl 是什么
──────────────────────────────────────────────────────────────
JSON Lines：一行一个完整的 JSON 对象，行与行之间没有逗号、没有外层的 []。

    {"_meta": {"created_at": "2026-09-15T18:32:43", "model": "deepseek-flash"}}
    {"role": "user", "content": "帮我读一下 main.py"}
    {"role": "assistant", "content": null, "tool_calls": [...]}
    {"role": "tool", "tool_call_id": "call_1", "content": "文件内容……"}
    {"role": "assistant", "content": "它是命令行入口。"}
    {"_summary": {"text": "……", "covered": 15, "at": "2026-09-15T16:22:10"}}

比起一个大 JSON 数组，它有两个好处：

    **能只追加**  写新内容 = 往文件末尾加几行，不用把整个文件读出来再写回去
    **能只读一点** 想知道文件头，读第一行就够，不用解析整个文件

代价是**没有"改"这个操作**。想改一行就得重写整个文件——而我们恰恰不想改，
所以这个代价对我们是零。

──────────────────────────────────────────────────────────────
三种行，靠有没有 role 键区分
──────────────────────────────────────────────────────────────
    形态                        谁认它            是消息吗
    {"role": ...}               load_messages     ✅
    {"_meta": {...}}            load_meta         ❌ 文件头
    {"_summary": {...}}         load_summary      ❌ 摘要便条

判别方式就一句 `if "role" not in obj: continue`。这个约定很好用：
**以后想往文件里加新类型的记录，只要不叫 role，就不会污染消息列表**，
load_messages 一个字都不用改。`_summary` 就是这么加进来的。

──────────────────────────────────────────────────────────────
不变量：存档只追加，永不改写
──────────────────────────────────────────────────────────────
本文件所有的写操作都是 open("a")（追加模式），**没有一处是 open("w")**。

为什么：磁盘上这份是这段对话的唯一真相。压缩是有损的，截断是有损的，
一旦改了它，原文就永久没了。有了全文，随时能翻回去、随时能换压缩策略。

摘要也遵守这条——它不改原文，只在末尾追加一张便条说明
"前 covered 条已经被这段摘要代表了"。

──────────────────────────────────────────────────────────────
坏行怎么办：跳过它，别连累整个文件（第 25 讲）
──────────────────────────────────────────────────────────────
追加写 + 断电/强杀，文件末尾很容易留下半行：

    {"role": "user", "content": "帮我读一下 mai      <- 写到一半断了

本来的写法是直接 json.loads(line)，**一行坏掉，整个会话读不出来**——
几百轮对话全没了，只因为最后半行。

现在所有读取都走 storage/jsonl.py 的 iter_records()，它遇到解析不了的行
就跳过，并且只警告一次。那个函数是公共的——流水账 history.jsonl 也用它，
细节写在那个文件的说明里。

代价：坏行里的内容确实丢了，没法救。但"丢半句"远好过"丢全部"。

──────────────────────────────────────────────────────────────
还剩的两个短板（记在待办里）
──────────────────────────────────────────────────────────────
1. **没有并发保护**：两个进程同时写同一个 key 会交错。单人 CLI 用不到。
2. **load_meta 比 load_summary 脆**：前者只读第一行，meta 不在第一行就静默返回 None。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from storage.jsonl import iter_records as _iter_records
from typing import Any

# 会话文件都放在这里。
#
# 锚在 __file__（代码位置）而不是 cwd（启动目录）——不管你在哪个目录敲
# python main.py，会话都存在同一个地方，不会到处散落。
#
# 注意这和 agent/tools/workspace.py 的选择相反：那边锚在 cwd，因为"工作区"
# 本来就该跟着你在哪儿启动走。两套基准并存，是有意的，但容易记混。
SESSIONS_DIR = Path(__file__).resolve().parent.parent / "data" / "sessions"


def _path(session_key: str) -> Path:
    """key -> 文件路径。内部函数。

    传入什么
        session_key: 会话标识，形如 "20260915-183243"。

    返回什么
        Path 对象，形如 .../data/sessions/20260915-183243.jsonl。
        **不保证这个文件存在。**
    """
    return SESSIONS_DIR / f"{session_key}.jsonl"


def append_messages(session_key: str, messages: list[dict[str, Any]]) -> None:
    """把几条消息追加到会话文件末尾。

    谁会用它
        session/manager.py 的 Session.save_turn()。

    传入什么
        session_key: 会话标识。
        messages:    要写的消息列表。**只写这几条**，不是整份历史——
                     调用方应该只传"这一轮新长出来的"。

    返回什么
        无。

    附带做了什么
        目录不存在会自动创建（mkdir parents=True）。所以第一次跑程序
        不用手动建 data/sessions/。

    两个细节
        - open("a")：**追加模式**。文件已有内容不会被动，新内容加在末尾。
        - ensure_ascii=False：中文原样写进文件。默认的 True 会把"你好"
          变成 "\\u4f60\\u597d"——能用，但打开文件就没法读了。

    例子（这里不写成可执行的 doctest，因为它会真的往磁盘上写文件）

        append_messages("20260915-183243", [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好呀"},
        ])

        文件末尾就多了两行。再调一次，又多两行，前面的不受影响。
        隔离好的可执行版本见 tests/test_session_summary.py。
    """
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    with _path(session_key).open("a", encoding="utf-8") as f:
        for msg in messages:
            f.write(json.dumps(msg, ensure_ascii=False) + "\n")


def load_messages(session_key: str) -> list[dict[str, Any]]:
    """读回一个会话的**全部消息**。

    谁会用它
        session/manager.py 的 resume()。

    传入什么
        session_key: 会话标识。

    返回什么
        消息字典组成的列表，按写入顺序。

        **文件不存在时返回空列表，不报错**——"这个会话还没有内容"和
        "读取失败"是两回事，前者是正常状态。

        返回的是**存档全文**，不含 _meta、不含 _summary。要变成能直接发给
        模型的那份，还得过一道 agent/compaction.py 的 restore_history()。

    坏行怎么办
        **跳过，其余照常返回**，并警告一次。见文件顶部"坏行怎么办"。
        一行坏掉不该让几百轮对话全部读不出来。

    例子
        不存在的会话返回空列表：

        >>> load_messages("这个会话不存在")
        []
    """
    messages: list[dict[str, Any]] = []
    for obj in _iter_records(_path(session_key)):
        if "role" not in obj:              # 不是消息（_meta 头、_summary 便条）
            continue
        messages.append(obj)
    return messages


def write_meta(session_key: str, meta: dict[str, Any]) -> None:
    """写会话的文件头。**新建会话时调一次，之后不该再调。**

    谁会用它
        session/manager.py 的 create()。

    传入什么
        session_key: 会话标识。
        meta:        要记的信息，形如

                         {"created_at": "2026-09-15T18:32:43",
                          "model": "deepseek-flash",
                          "workspace": "D:\\\\VibeCoding\\\\Agent\\\\mini_nanobot"}

    返回什么
        无。

    为什么要记这三样
        created_at   --list 里显示"什么时候建的"
        model        --list 里显示"当时用的哪个模型"
        workspace    恢复会话时对比，工作区变了就警告——因为历史里的
                     相对路径（agent/runner.py）可能已经指不到东西了

    注意：重复调用会留下多条 _meta
        它用的也是追加模式，而 load_meta 只认**第一行**。所以第二次写的
        meta 会被静默忽略。这是已知短板，正常流程里 create() 只调一次。
    """
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    with _path(session_key).open("a", encoding="utf-8") as f:
        f.write(json.dumps({"_meta": meta}, ensure_ascii=False) + "\n")


def load_meta(session_key: str) -> dict[str, Any] | None:
    """读会话的文件头。

    谁会用它
        session/manager.py 的 resume() 和 list_all()。

    传入什么
        session_key: 会话标识。

    返回什么
        meta 字典，或者 None（文件不存在、或者第一行不是 _meta）。

    为什么只读第一行
        因为 --list 要对**每一个**会话都调一次。会话文件可能有几十 KB，
        全读一遍太浪费——文件头本来就在第一行，读到就返回。

        代价：meta 必须严格是第一行。正常流程里 create() 第一件事就是写它，
        所以成立；但这个前提没有任何东西强制保证。

    例子
        >>> load_meta("这个会话不存在") is None
        True
    """
    for obj in _iter_records(_path(session_key)):
        return obj.get("_meta")            # 只看第一条读得懂的记录
    return None


def append_summary(session_key: str, summary: str, covered: int) -> None:
    """追加一条摘要便条。**不动任何原文。**

    谁会用它
        session/manager.py 的 Session.save_summary()。

    传入什么
        session_key: 会话标识。
        summary:     摘要正文（模型写出来的那段话）。
        covered:     这段摘要代表了存档里**前多少条**消息。
                     注意是**累计值**，不是这次新增的数量。

    返回什么
        无。

    写进去长什么样
        {"_summary": {"text": "……", "covered": 15, "at": "2026-09-15T16:22:10"}}

        它**不带 role**，所以 load_messages 会自动跳过——
        存档里的原文一条都不动，只是多了一张便条说明
        "前 covered 条已经被这段摘要代表了"。

    为什么用追加而不是改写文件头
        改写就要把整个文件读出来再写回去，"只追加"这条不变量就废了。
        追加的话，写到一半断电最多留下半行垃圾，前面的内容全都安全。

        代价是文件里会有多条 _summary（压几次就有几条），
        所以 load_summary 取**最后一条**。

    摘要消息本身会被写进存档吗
        **不会。** 压缩后内存里那条摘要消息在写盘边界之前，走不到 save_turn。
        磁盘上只有这张便条，下次启动时由 restore_history() 现场重造出消息。

    例子（同样不写成可执行的 doctest，它会真的写盘）

        append_messages(key, [{"role": "user", "content": "问题1"}])
        append_summary(key, "聊过问题1", covered=1)

        之后：
            load_messages(key)  ->  [{'role': 'user', 'content': '问题1'}]
                                    原文一条不少，便条被跳过
            load_summary(key)   ->  {'text': '聊过问题1', 'covered': 1, 'at': ...}

        可执行版本见 tests/test_session_summary.py 的
        test_summary_record_does_not_touch_the_transcript。
    """
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    record = {"_summary": {
        "text": summary,
        "covered": covered,
        "at": datetime.now().isoformat(timespec="seconds"),
    }}
    with _path(session_key).open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def load_summary(session_key: str) -> dict[str, Any] | None:
    """读**最后一条**摘要便条。

    谁会用它
        session/manager.py 的 resume()。

    传入什么
        session_key: 会话标识。

    返回什么
        便条字典，形如 {"text": ..., "covered": 15, "at": ...}；
        **从没压缩过的会话返回 None。**

    为什么取最后一条
        压缩可能发生多次，每次追加一条便条。后写的摘要是在前一条的基础上
        又压了一次，信息更全、covered 更大，所以**后写的覆盖先写的**。

        取最后一条只能从头扫到尾（jsonl 没法从后往前读），
        所以这个函数的成本是 O(文件大小)。会话很长时会慢，已知短板。

    at 字段有人用吗
        没有。写进去是为了以后排查问题时能看时间，目前没有任何代码读它。
        顺带一提：manager.save_summary() 塞进内存的那份**不带 at**，
        和磁盘上的形状不一致——不是 bug，但别依赖 summary["at"] 一定存在。

    例子
        >>> load_summary("这个会话不存在") is None
        True
    """
    latest: dict[str, Any] | None = None
    for obj in _iter_records(_path(session_key)):
        if "_summary" in obj:
            latest = obj["_summary"]
    return latest


def count_messages(session_key: str) -> int:
    """数一个会话有多少条消息。

    谁会用它
        session/manager.py 的 list_all()，也就是 --list 显示的那个"条数"。

    传入什么
        session_key: 会话标识。

    返回什么
        整数。文件不存在返回 0。

        **只数带 role 的行** —— _meta 头和 _summary 便条都不算消息。
        早先的写法是"总行数减 1"（减掉 _meta），加了摘要便条之后就虚高了，
        test_count_messages_ignores_summary_records 守着这一点。

    它比 load_messages 省在哪
        load_messages 会把每条消息都攒进一个列表返回；这里只累加计数，
        不留对象。不过每行还是 json.loads 解析了一遍，所以省的是内存占用，
        不是解析开销。

    例子
        >>> count_messages("这个会话不存在")
        0
    """
    return sum(1 for obj in _iter_records(_path(session_key)) if "role" in obj)


def list_keys() -> list[str]:
    """列出所有会话的 key，新的在前。

    谁会用它
        session/manager.py 的 list_all()。

    传入什么
        无。

    返回什么
        key 字符串列表，形如 ["20260915-183243", "20260913-104035"]。
        目录不存在时返回空列表。

    "新的在前"有个前提
        它是按**文件名字符串**倒序排的，不是按修改时间。

        之所以成立，是因为 key 的格式是 %Y%m%d-%H%M%S——
        这种格式的**字典序恰好等于时间序**（"20260915" > "20260913"）。
        哪天 key 换成别的格式（比如 UUID），这个排序就不再是"新的在前"了。

    例子

        目录里有 20260101-000000.jsonl 和 20260915-000000.jsonl 两个文件时：

            list_keys()  ->  ['20260915-000000', '20260101-000000']
    """
    if not SESSIONS_DIR.exists():
        return []
    return [p.stem for p in sorted(SESSIONS_DIR.glob("*.jsonl"), reverse=True)]