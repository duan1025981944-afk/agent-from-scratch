"""长期记忆：MEMORY.md 在哪、怎么读。对应第 24 讲。

──────────────────────────────────────────────────────────────
这个文件是什么
──────────────────────────────────────────────────────────────
**语义记忆**的存取入口。MEMORY.md 里放的是"以后换个会话还用得上的事实"，
启动时由 main.py 读出来，交给 context.py 拼进系统提示。

阶段五分两半：

    上半（第 24 讲）  MEMORY.md ——"总账"。只读，写归以后的 Dream 管
    下半（第 25 讲）  history.jsonl ——"流水账"。只能往后加，永不改写

两者的关系，就是会计的日记账和总账：

    流水账  一条一条往后记，记下来就不动    <- 第 25、26 讲做的
    总账    定期把流水账整理成结论           <- 第 27 讲做的 Dream
    开场白  只贴总账，不贴流水账             <- 第 24 讲做的注入

三个动作的分工，一句话说清：

    append_fact   记一笔流水    只追加，不判断对错
    dream         月底结账      读流水账 -> 整理 -> **覆盖**总账
    read_memory   看总账        启动时读一次，拼进开场白

流水账多了个"编号"（cursor），作用是记住"上次整理到第几条了"，
下次只整理新的那些。和 _summary.covered 是同一个思路——**指针外化**。

──────────────────────────────────────────────────────────────
这个文件不是什么
──────────────────────────────────────────────────────────────
- 它**不**拼系统提示——那是 context.py 的事，它只拿到一段文本
- 它**不**写 MEMORY.md——写入只归 Dream（第 27 讲），主 Agent 碰不到（第 24 讲下半）
- 它**不**判断"什么事值得记"——那是第 26 讲提炼环节的事，这里只管存
- 它**不**决定什么时候整理——那是 main.py 的事（退出时、或者你敲 /dream）
- 它**不**是 templates/。见下面

──────────────────────────────────────────────────────────────
为什么 MEMORY.md 不放在 templates/（本文件最重要的一点）
──────────────────────────────────────────────────────────────
templates/ 和 MEMORY.md 看起来都是"拼进系统提示的 .md"，但本质完全不同：

    ┌──────────────┬──────────────────────┬──────────────────────────┐
    │              │ templates/SOUL.md 等  │ data/memory/MEMORY.md    │
    ├──────────────┼──────────────────────┼──────────────────────────┤
    │ 谁写         │ 作者（人）            │ Agent 自己（经 Dream）   │
    │ 进不进 git   │ 进，推到 GitHub       │ 不进，含真实对话里的事实 │
    │ 不存在时     │ 是 bug，应该报错      │ 是常态（刚装好还没记过） │
    └──────────────┴──────────────────────┴──────────────────────────┘

nanobot 也是这么分的：包里的 templates/memory/MEMORY.md 只是个空模板，
真正被读写的是工作区里的 memory/MEMORY.md（见 nanobot/utils/helpers.py
的 sync_workspace_templates，和 agent/memory.py 的 MemoryStore.__init__）。

路径锚在代码位置、放在 data/ 下，和 session/messages.py 的 SESSIONS_DIR 同一个
基准。data/ 已经在 .gitignore 里。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from agent.prompts import read_template
from providers.base import Provider
from storage.jsonl import iter_records

# 记忆文件的唯一位置。测试里用 tmp_path 代替，不会碰到真文件。
MEMORY_FILE = Path(__file__).resolve().parent.parent / "data" / "memory" / "MEMORY.md"


def read_memory(path: Path = MEMORY_FILE) -> str:
    """读出长期记忆的正文；没有记忆时返回空字符串。

    谁会用它
        main.py，启动时读一次，结果传给 build_system_prompt()。

    传入什么
        path: 记忆文件路径。平时不用传，用默认的 MEMORY_FILE；
              测试里传 tmp_path 下的文件。

    返回什么
        str。去掉首尾空白后的正文。以下情况都返回 ""：
            - 文件不存在（刚装好、还没 Dream 过——这是常态，不是错误）
            - 文件存在但是空的，或者只有空白

    为什么用 utf-8-sig 而不是 utf-8
        Windows PowerShell 5.1 的 `Set-Content -Encoding utf8` 会在文件开头
        写一个 BOM（\\ufeff）。用 utf-8 读，这个字符会原样留在开头，而且
        str.strip() **去不掉它**（它不算空白）。utf-8-sig 读的时候会自动吃掉 BOM，
        没有 BOM 的文件也照常读。

    例子（碰文件系统，所以不写成 doctest，见 tests/test_memory_injection.py）
        文件不存在           -> ""
        文件内容 "\\n  \\n"   -> ""
        文件内容 "- 用户叫 Himeko\\n" -> "- 用户叫 Himeko"
    """
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8-sig").strip()


# ══════════════════════════════════════════════════════════════
# 流水账 history.jsonl（第 25 讲）
# ══════════════════════════════════════════════════════════════
#
# 一行一条，形如：
#
#     {"cursor": 1, "at": "2026-09-17 16:20", "content": "用户的项目代号是 蓝鲸-7",
#      "session": "20260917-161500"}
#
# cursor   自增编号，从 1 开始。Dream 靠它知道"上次整理到哪了"
# at       写入时间，只为排查问题用，没有代码读它
# content  一条事实的正文
# session  这条事实是从哪次会话来的。**第 29 讲"从结论追回原始对话"全靠它**

HISTORY_FILE = MEMORY_FILE.parent / "history.jsonl"

# 每条事实的"保质期标签"。第 27 讲整理进 MEMORY.md 时，Dream 靠它决定
# 谁该留、谁该换掉、谁过期了可以删。
#
#     permanent   长期不变：用户是谁、用什么系统、一贯的偏好
#     durable     几个月：项目用了什么技术、架构决策、环境配置
#     ephemeral   几周：当前在做哪个阶段、眼下的待办
#     correction  纠正：推翻之前记错的说法
#
# 标签取自 nanobot 的 dream.md / consolidator_archive.md，它那里还有一个
# [skip]（审计用、不进记忆）。我们在解析阶段就把 skip 丢掉了，所以存进
# 流水账的记录里不会出现它。
VALID_TAGS = ("permanent", "durable", "ephemeral", "correction")

# 从摘要正文里认出事实行的规则。宽容一点：
#
#     - [permanent] 用户用 Windows        标准写法
#     * [durable] 项目用 Chroma           换个符号
#     [ephemeral] 正在做阶段五             没有前缀符号
#     1. [durable] xxx                    带编号
#
# 认不出的行一律忽略——摘要正文的其他部分本来就不该进流水账。
_FACT_LINE = re.compile(
    r"^\s*(?:[-*•]|\d+[.、)])?\s*[\[【]\s*(\w+)\s*[\]】]\s*(.+?)\s*$"
)

# 单条正文的上限。超过就截断——这是防意外的保险丝，不是正常路径：
# 正常的一条事实就一句话。nanobot 也有同样的兜底（_HISTORY_ENTRY_HARD_CAP），
# 它防的是"模型把整段输入当摘要原样吐回来"这种事故。
MAX_CONTENT_CHARS = 4_000


def append_fact(
    content: str,
    session: str = "",
    tag: str = "durable",
    path: Path | None = None,
) -> int:
    """往流水账末尾记一条，返回它的编号。

    谁会用它
        第 26 讲的提炼环节：压缩历史时顺便挑出值得长期记住的事实，一条条记进来。
        现在还没有调用方，可以先手动调着玩。

    传入什么
        content: 一条事实的正文。建议一句话，例如 "用户的项目代号是 蓝鲸-7"。
                 超过 MAX_CONTENT_CHARS 会被截断并在末尾加省略号。
        session: 这条事实来自哪次会话（会话 key）。不传就是空字符串。
        tag:     保质期标签，取值见 VALID_TAGS。不传按 "durable" 算。
                 不认识的标签会被换成 "durable" —— 模型偶尔会自创标签，
                 为这个丢掉一条好事实不划算。
        path:    流水账文件。平时不传，用 HISTORY_FILE；测试里传 tmp_path 下的文件。
                 **默认值写成 None、在函数里才取 HISTORY_FILE**，不写成
                 `path: Path = HISTORY_FILE`——后者在 def 那一刻就把值定死了，
                 测试想整体换掉 HISTORY_FILE 就换不动，真实记忆会被测试污染。

    返回什么
        这条记录的编号（int）。第一条是 1，之后依次加一。

    会抛什么
        ValueError —— content 去掉首尾空白后是空的。
        空记录没有任何意义，还会浪费 Dream 的一次整理名额，所以当场拒绝。

    它保证什么（不变量）
        **只追加，永不改写。** 和会话存档是同一条规矩：
        记错了就再记一条纠正（第 29 讲的 [correction]），不回头涂改。
        所以这个函数里只有 open("a")，没有 open("w")。

    例子（碰文件系统，所以不写成 doctest，见 tests/test_history.py）
        append_fact("用户的项目代号是 蓝鲸-7")  ->  1
        append_fact("用户在 USM 读研")          ->  2
    """
    path = path or HISTORY_FILE       # 在调用时才取，测试才能换掉 HISTORY_FILE
    text = content.strip()
    if not text:
        raise ValueError("流水账不收空记录：content 去掉空白之后不能是空的。")

    if len(text) > MAX_CONTENT_CHARS:
        text = text[: MAX_CONTENT_CHARS - 1] + "…"

    record = {
        "cursor": _next_cursor(path),
        "at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "tag": tag if tag in VALID_TAGS else "durable",
        "content": text,
        "session": session,
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:       # ★ 只追加
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    return record["cursor"]


def read_facts(since: int = 0, path: Path | None = None) -> list[dict[str, Any]]:
    """读出编号大于 since 的所有记录。

    谁会用它
        第 27 讲的 Dream：它拿着"上次整理到第几条"去要还没整理过的部分。

    传入什么
        since: 从哪个编号**之后**开始要。0（默认）= 全要。
        path:  流水账文件。

    返回什么
        记录字典组成的列表，按编号从小到大。没有符合的就是空列表。

        坏行会被跳过（见 storage/jsonl.py）。编号不是正整数的记录也跳过——
        外部工具手改过文件时可能出现，让它进来会把 Dream 的游标算歪。

    例子（见 tests/test_history.py）
        read_facts()        ->  全部
        read_facts(since=2) ->  只有 3、4、5……
    """
    path = path or HISTORY_FILE
    facts = [r for r in iter_records(path) if _valid_cursor(r) is not None]
    return [r for r in facts if r["cursor"] > since]


def _valid_cursor(record: dict[str, Any]) -> int | None:
    """从一条记录里取出合法编号；不合法返回 None。

    合法 = 正整数。这里特意排除了 bool：Python 里 isinstance(True, int) 是 True，
    不挡的话 {"cursor": true} 会被当成编号 1。nanobot 的 _valid_cursor 也专门
    挡了这一条，是个容易漏的坑。
    """
    value = record.get("cursor")
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value


def _next_cursor(path: Path) -> int:
    """算出下一条该用几号：现有记录里最大的编号 + 1。

    为什么每次都扫全文，不单独存一个计数器文件
        nanobot 存了（memory/.cursor），因为它有多个进程、多个渠道同时写。
        我们是单进程的命令行程序，扫一遍几百行的成本可以忽略。
        **先写死，等真的慢了再优化**——这是第二次用同一条规矩了。

    为什么取"最大值 + 1"而不是"条数 + 1"
        坏行会被跳过，条数就对不上了。取最大值则不受影响：
        哪怕中间丢了几条，新编号也永远比所有旧编号大，不会撞号。
    """
    cursors = [c for r in iter_records(path) if (c := _valid_cursor(r)) is not None]
    return max(cursors, default=0) + 1

def parse_facts(text: str) -> list[tuple[str, str]]:
    """从模型写的摘要里，把"值得长期记住的事实"那些行挑出来。

    谁会用它
        agent/compaction.py：压缩成功后，把挑出来的事实逐条写进流水账。

    传入什么
        text: 模型返回的摘要全文。里面既有五项散文，也有末尾的事实清单。

    返回什么
        [(标签, 正文), ...]，按出现顺序。挑不出来就是空列表（**不是错误**：
        有的对话就是没有值得长期记住的东西，模型写"（无）"是正确行为）。

        以下行会被丢掉：
            认不出格式的行          摘要的散文部分，本来就不该进流水账
            [skip] 标签的行         nanobot 用它表示"只供审计，别存"
            正文是"（无）""无""none" 模型表示"没有值得记的"
            和前面完全重复的行      同一条事实模型有时会写两遍

    为什么解析要宽容
        模型不会每次都严格照格式写。为了一个方括号写成中文【】就丢掉一条
        真事实，不划算。认不出的行忽略就好，代价只是少记一条。

    为什么不在这里判断"这条事实对不对"
        判断不了。正确性交给后面两道：第 27 讲整理时模型会合并冲突，
        第 29 讲用户说"不对"时用 [correction] 推翻。这里只管**认字**。

    例子
        >>> parse_facts("1. 任务目标：随便什么")          # 散文行认不出，忽略
        []
        >>> parse_facts("- [permanent] 用户用 Windows")
        [('permanent', '用户用 Windows')]
        >>> parse_facts("* 【durable】项目用 Chroma")      # 中文方括号也认
        [('durable', '项目用 Chroma')]
        >>> parse_facts("- [skip] 这条只供审计")           # skip 丢掉
        []
        >>> parse_facts("- [ephemeral] （无）")            # 模型说"没有"
        []
    """
    facts: list[tuple[str, str]] = []
    seen: set[str] = set()

    for line in text.splitlines():
        m = _FACT_LINE.match(line)
        if not m:
            continue

        tag, content = m.group(1).lower(), m.group(2).strip()
        if tag == "skip" or tag not in VALID_TAGS:
            continue
        if content.strip("（）() 　") in ("", "无", "none", "None"):
            continue
        if content in seen:
            continue

        seen.add(content)
        facts.append((tag, content))

    return facts


def record_facts(
    summary_text: str, session: str = "", path: Path | None = None
) -> int:
    """解析摘要、把里面的事实一条条记进流水账，返回记了几条。

    谁会用它
        agent/compaction.py，压缩成功之后。

    传入什么
        summary_text: 模型写的摘要全文。
        session:      这次会话的 key，记在每条事实上，用于以后追溯出处。
        path:         流水账文件。

    返回什么
        写进去的条数。没有可记的就是 0（正常情况，不是错误）。

    它**不会**抛异常
        记忆是锦上添花，压缩才是正事。磁盘满了、文件被占用……这些都不该
        让一次成功的压缩变成失败。所以这里把异常吃掉，只在屏幕上提一句。

    例子（碰文件系统，见 tests/test_fact_extraction.py）
        record_facts("- [permanent] 用户用 Windows", session="2026...")  ->  1
    """
    path = path or HISTORY_FILE
    written = 0
    for tag, content in parse_facts(summary_text):
        try:
            append_fact(content, session=session, tag=tag, path=path)
            written += 1
        except (OSError, ValueError) as exc:
            print(f"  [记忆写入失败，已跳过这条：{exc}]")
    return written

# ══════════════════════════════════════════════════════════════
# Dream 整理：流水账 -> 总账（第 27 讲）
# ══════════════════════════════════════════════════════════════
#
# 一次整理干三件事：
#
#     ① 读游标之后的新事实       read_facts(since=游标)
#     ② 交给模型，连同当前总账     一次调用
#     ③ 覆盖 MEMORY.md、推进游标   两件事要么都做、要么都不做
#
# 游标存在一个单独的小文件里（指针外化），和 _summary.covered 是同一个思路：
# **进度是个数字，单独存着，别每次从内容里猜。**
# nanobot 叫 memory/.dream_cursor，我们叫 .dream_cursor，放在同一个目录。

DREAM_CURSOR_FILE = MEMORY_FILE.parent / ".dream_cursor"

# 一次最多整理多少条。和 nanobot 的 build_dream_prompt(max_entries=20) 同一个值。
#
# 为什么要有上限：流水账可能攒了几百条，一次全塞进去既贵又容易让模型顾此失彼。
# 剩下的下次再整理——游标只推进到这一批的最后一条，下次从那儿接着来。
DREAM_BATCH = 20

# 整理用的提示词。正文在 templates/prompts/dream.md，改文件即生效。
# 在 import 时就读：文件缺了要在启动时立刻报错，而不是等你敲 /dream 时才炸。
DREAM_INSTRUCTION = read_template("prompts", "dream.md")


@dataclass
class DreamResult:
    """一次 Dream 的结果。

    谁会产生它
        只有 dream()。

    谁会用它
        main.py：拿 ok 判断要不要打印成功信息，拿 processed 告诉用户整理了几条。

    字段
        ok:        整理成功了吗。**失败时 MEMORY.md 和游标都没动。**
        processed: 这次处理了几条流水账。0 表示没有新的可整理（ok 为 True）。
        cursor:    整理之后的游标值。失败时是原值。
        note:      给人看的一句话说明，失败时说明原因。

    例子
        >>> r = DreamResult(ok=True, processed=0, cursor=5, note="没有新事实")
        >>> r.ok and r.processed == 0          # 成功，但没什么可做
        True
    """

    ok: bool
    processed: int
    cursor: int
    note: str


def read_dream_cursor(path: Path | None = None) -> int:
    """读"上次整理到第几条"。没整理过就是 0。

    谁会用它
        dream()，以及想看进度的人。

    传入什么
        path: 游标文件。平时不传，测试里传 tmp_path 下的文件。

    返回什么
        int。以下情况一律返回 0（当作"从没整理过"）：
            - 文件不存在
            - 文件内容不是整数（被手改坏了）
            - 是负数

        **读不懂就当 0，不报错。** 最坏的后果是把已经整理过的事实再整理一遍，
        而 Dream 本来就是幂等的（同样的事实合并两次，结果一样）。
        为这个崩掉整个程序不值得。

    例子（碰文件系统，见 tests/test_dream.py）
        文件不存在   -> 0
        文件内容 "7" -> 7
        文件内容 "坏了" -> 0
    """
    path = path or DREAM_CURSOR_FILE
    try:
        value = int(path.read_text(encoding="utf-8-sig").strip())
    except (ValueError, OSError):      # 文件不存在也走这里：FileNotFoundError 是 OSError
        return 0
    return value if value >= 0 else 0


def write_dream_cursor(cursor: int, path: Path | None = None) -> None:
    """记下"整理到第几条了"。

    谁会用它
        dream()，**只在整理成功之后调**。这是不变量 8 的一半。

    传入什么
        cursor: 这次整理到的最后一条的编号。
        path:   游标文件。
    """
    path = path or DREAM_CURSOR_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(cursor), encoding="utf-8")


def build_dream_prompt(memory: str, facts: list[dict[str, Any]]) -> str:
    """拼出整理用的提示词：指令 + 当前总账 + 新流水账。

    谁会用它
        dream()。单独抽出来是为了能不调模型就测它（拼词是纯函数）。

    传入什么
        memory: 当前 MEMORY.md 的正文。第一次整理时是空字符串。
        facts:  read_facts() 返回的那些记录。

    返回什么
        一整段提示词（str）。

    为什么当前总账要放进提示词
        模型要输出**整篇新的**总账，就必须先看到旧的长什么样——
        否则它只能凭新事实重写，旧内容全丢。

        （nanobot 不用这么做：它的 Dream 是个能读文件的受限 Agent，
        当前记忆通过系统提示进去，它自己用 edit_file 改。我们这里是
        一次性调用，所以要手动喂进去。取舍见 dream() 的说明。）

    例子
        >>> p = build_dream_prompt("", [{"tag": "permanent", "content": "用户用中文"}])
        >>> "[permanent] 用户用中文" in p
        True
        >>> "（这是第一次整理，还没有长期记忆）" in p
        True
    """
    lines = [f"[{f.get('tag', 'durable')}] {f.get('content', '')}" for f in facts]
    current = memory.strip() or "（这是第一次整理，还没有长期记忆）"

    return (
        f"{DREAM_INSTRUCTION}\n\n"
        f"---\n\n"
        f"## 当前的长期记忆\n\n{current}\n\n"
        f"---\n\n"
        f"## 新的流水账（{len(facts)} 条）\n\n" + "\n".join(lines)
    )


async def dream(
    provider: Provider,
    memory_path: Path | None = None,
    history_path: Path | None = None,
    cursor_path: Path | None = None,
) -> DreamResult:
    """睡前整理：把流水账上的新事实并进长期记忆。

    谁会用它
        main.py：你敲 /dream 时，以及退出程序时。

    传入什么
        provider:     调模型用。和压缩用的是同一个。
        memory_path / history_path / cursor_path:
                      三个文件的位置。平时都不传，测试里传 tmp_path 下的。

    返回什么
        DreamResult。四种情形：

            没有新事实        ok=True,  processed=0   什么都没做，也不算失败
            调模型失败        ok=False, processed=0   MEMORY.md 和游标都没动
            模型返回空/太短    ok=False, processed=0   同上
            整理成功          ok=True,  processed=N   MEMORY.md 被覆盖，游标推进

    它保证什么（不变量 8）
        **没正常跑完，绝不推进游标。**

        推进了游标就等于宣布"这批事实已经整理进总账了"。要是模型其实失败了，
        这批事实就永远不会再被整理——它们还在流水账里，但再也没人看它们一眼。
        所以写 MEMORY.md 和推进游标这两件事必须**同时发生**，
        代码里它们紧挨着，中间不能有任何可能失败的东西。

    为什么是"一次调用、整篇覆盖"，而不是像 nanobot 那样起个受限 Agent
        nanobot 的 Dream 是一个只有文件工具的小 Agent，它自己 read_file 看当前
        记忆、再用 edit_file 精确改几行（见 MemoryStore.build_dream_tools）。

        我们选了更笨的办法：一次调用，让模型输出整篇新的，直接覆盖。

            受限 Agent      改得精确，不容易误删；但要先有 edit_file，
                            还要给 runner 传一套不同的工具，工程量大
            整篇覆盖        实现只有十几行；代价是模型可能**悄悄漏抄**一条旧记忆

        选后者的理由是"先写死再抽象"：我们的总账很小（几十行），整篇重写的
        风险可控。而"悄悄漏抄"这个风险，正好是第 28 讲要用快照和 diff 解决的——
        **先撞坏再加固**。等真撞上了，再考虑要不要换成受限 Agent。

    为什么写 MEMORY.md 不走 write_file 工具
        第 24 讲下半定的禁区：`data/` 里的文件，工具一律不许写。
        Dream 不是工具，它是程序自己的一段代码，直接 write_text 就行——
        **围栏拦的是模型，不是我们自己。**

        这样禁区那条不变量不用开任何口子，主 Agent 依然碰不到记忆。

    例子（要调模型，见 tests/test_dream.py）
        没有新事实       -> DreamResult(ok=True, processed=0, ...)
        模型返回空       -> DreamResult(ok=False, ...)，文件没动
    """
    memory_path = memory_path or MEMORY_FILE
    cursor = read_dream_cursor(cursor_path)
    facts = read_facts(since=cursor, path=history_path)

    if not facts:
        return DreamResult(True, 0, cursor, "流水账里没有新事实，不用整理。")

    batch = facts[:DREAM_BATCH]
    prompt = build_dream_prompt(read_memory(memory_path), batch)

    try:
        reply = await provider.chat([{"role": "user", "content": prompt}])
        新总账 = (reply.content or "").strip()
    except Exception as exc:                      # noqa: BLE001 —— 整理失败不该炸掉程序
        return DreamResult(False, 0, cursor, f"整理时调模型失败：{exc}")

    if not 新总账:
        # 模型返回空。可能是被安全策略拦了、可能是超时截断了。
        # 不管哪种，都不能拿一个空字符串去覆盖总账——那等于把记忆全删了。
        return DreamResult(False, 0, cursor, "模型没有返回内容，记忆未改动。")

    # ★ 这两行必须紧挨着：写总账 + 推进游标，要么都做、要么都不做（不变量 8）
    memory_path.parent.mkdir(parents=True, exist_ok=True)
    memory_path.write_text(新总账 + "\n", encoding="utf-8")
    write_dream_cursor(batch[-1]["cursor"], cursor_path)

    剩下 = len(facts) - len(batch)
    note = f"整理了 {len(batch)} 条事实。"
    if 剩下:
        note += f"还剩 {剩下} 条，下次接着整理。"
    return DreamResult(True, len(batch), batch[-1]["cursor"], note)