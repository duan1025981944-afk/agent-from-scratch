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

import difflib
import json
import re
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from agent.prompts import read_template
from providers.base import Provider
from storage.jsonl import iter_records

# 记忆文件的唯一位置。测试里用 tmp_path 代替，不会碰到真文件。
MEMORY_FILE = Path(__file__).resolve().parent.parent / "data" / "memory" / "MEMORY.md"


def read_memory(path: Path | None = None) -> str:
    """读出长期记忆的正文；没有记忆时返回空字符串。

    谁会用它
        main.py，启动时读一次，结果传给 build_system_prompt()。

    传入什么
        path: 记忆文件路径。平时不用传，用默认的 MEMORY_FILE；
              测试里传 tmp_path 下的文件。

              **默认值写成 None、在函数里才取 MEMORY_FILE**，和 append_fact
              一样。写成 `path: Path = MEMORY_FILE` 的话，默认值在 def 那一刻
              就定死了，之后再换掉 MEMORY_FILE 也没用——第 28 讲搭投毒现场时
              就被这一条坑过：换了 MEMORY_FILE，read_memory() 照样读旧地址，
              读出来是空的。

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
    path = path or MEMORY_FILE
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
        diff:      这次整理**实际上**改了哪些行（第 28 讲）。由程序比对前后两版
                   算出来，不是模型自己汇报的——模型漏抄一条时它自己不知道。
                   没改动、或者没整理时是空字符串。

    例子
        >>> r = DreamResult(ok=True, processed=0, cursor=5, note="没有新事实")
        >>> r.ok and r.processed == 0          # 成功，但没什么可做
        True
        >>> r.diff                             # 没整理，自然没有改动
        ''
    """

    ok: bool
    processed: int
    cursor: int
    note: str
    diff: str = ""


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
    snapshot_dir: Path | None = None,
) -> DreamResult:
    """睡前整理：把流水账上的新事实并进长期记忆。

    谁会用它
        main.py：你敲 /dream 时，以及退出程序时。

    传入什么
        provider:     调模型用。和压缩用的是同一个。
        memory_path / history_path / cursor_path / snapshot_dir:
                      四个位置。平时都不传，测试里传 tmp_path 下的。

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

    # ★ 覆盖之前先拍快照（第 28 讲）。放在这里而不是函数开头：
    # 前面任何一步失败都不会走到这儿，那些情况本来就没改文件，不用存。
    旧总账 = read_memory(memory_path)
    snapshot_memory(memory_path, snapshot_dir)

    # ★ 这两行必须紧挨着：写总账 + 推进游标，要么都做、要么都不做（不变量 8）
    memory_path.parent.mkdir(parents=True, exist_ok=True)
    memory_path.write_text(新总账 + "\n", encoding="utf-8")
    write_dream_cursor(batch[-1]["cursor"], cursor_path)

    剩下 = len(facts) - len(batch)
    note = f"整理了 {len(batch)} 条事实。"
    if 剩下:
        note += f"还剩 {剩下} 条，下次接着整理。"
    return DreamResult(
        True, len(batch), batch[-1]["cursor"], note,
        diff=diff_memory(旧总账, 新总账),        # 程序算的，不是模型自己说的
    )

# ══════════════════════════════════════════════════════════════
# 快照与回滚（第 28 讲）
# ══════════════════════════════════════════════════════════════
#
# 第 27 讲的 Dream 是"整篇覆盖"——模型输出什么，MEMORY.md 就变成什么。
# 第 28 讲搭了个投毒现场，一跑就撞出两个问题：
#
#     ① 错的事实被并进去了           流水账里混进一条"向量库用 Pinecone"
#     ② 一条旧记忆被**悄悄漏抄**了    "提示词外置在 templates/" 没了
#
# 第 ② 个更阴险：屏幕上只说"整理了 1 条事实"，完全看不出丢了东西。
# 而且旧内容已经被 write_text 覆盖，**磁盘上没有任何地方存着它**。
#
# 所以加两样东西，都不指望模型配合：
#
#     快照   覆盖之前先存一份带时间戳的副本   -> 退得回去
#     diff   程序自己比对前后两版，逐行报告   -> 看得见改了什么
#
# **diff 必须由程序算，不能让模型自己汇报改了什么。** nanobot 在
# GitStore.summarize_working_tree 的注释里写明了这一点：
# "Pure filesystem/git ground truth — never LLM narrative"。
# 道理很简单：模型漏抄了一条，它自己是不知道的——它以为自己抄全了。
# 问它"你改了什么"，它只会复述它**打算**做的事。

SNAPSHOT_DIR = MEMORY_FILE.parent / "snapshots"

# 留最近多少份快照。更早的自动删掉。
#
# 为什么要有上限：每次整理都存一份，一天几十次就攒一堆。
# 为什么是 10：够你回溯最近几次整理了。nanobot 靠 git 存全部历史，
# 我们没引入 git（多一个 dulwich 依赖），先写死一个数——
# 真需要翻很久以前的版本时再说。
KEEP_SNAPSHOTS = 10


def _free_snapshot_path(snapshot_dir: Path) -> Path:
    """挑一个还没被占用的快照文件名。

    只给 snapshot_memory() 用。

    为什么时间戳要精确到微秒
        原本只精确到秒，写测试时立刻撞车了：restore_snapshot() 会先给
        "当前这版"拍一张，而它和 dream 刚拍的那张常常落在同一秒里——
        后者把前者覆盖掉，于是"退回上一版"退了个寂寞。

        改成毫秒还是撞。实测（写 200 个小文件量的）相邻两次写盘最短只隔
        **19 微秒**，一毫秒内轻松塞进好几次。所以取微秒。

    为什么有了微秒还要再查一遍存不存在
        微秒够用，但"够快所以不会撞"是个假设，而这个假设一旦破了，
        后果是**静默覆盖掉一份快照**——最不该静默失败的地方。
        多三行循环，把假设变成保证。

    往后加而不是往前减
        撞名时把微秒数 +1 再试。加不会越过上一份快照，减会——
        而 list_snapshots 靠文件名排序分新旧，顺序乱了整个回滚就废了。

    例子（碰文件系统，见 tests/test_snapshot.py）
        目录空的        ->  snapshots/MEMORY-20260918-164700-842317.md
        那个名字已存在   ->  ...-842318.md
    """
    now = datetime.now()
    秒 = f"{now:%Y%m%d-%H%M%S}"
    微秒 = now.microsecond

    while True:
        target = snapshot_dir / f"MEMORY-{秒}-{微秒:06d}.md"
        if not target.exists():
            return target
        微秒 += 1


def snapshot_memory(
    memory_path: Path | None = None,
    snapshot_dir: Path | None = None,
) -> Path | None:
    """把当前的 MEMORY.md 存一份副本，返回副本的路径。

    谁会用它
        dream()，**在覆盖 MEMORY.md 之前**；以及 restore_snapshot()，
        在回滚之前（撤销也要能被撤销）。

    传入什么
        memory_path:  要拍快照的文件。
        snapshot_dir: 快照存哪。默认 data/memory/snapshots/。

    返回什么
        副本的路径。**记忆文件还不存在时返回 None**（第一次整理，没什么可存的）。

    副本叫什么
        MEMORY-20260918-164700-842317.md ——「文件名带时间戳」是最笨也最好用的
        版本管理：按名字排序就是按时间排序，不用额外的索引文件。

        时间戳精确到**微秒**，理由见 _free_snapshot_path()。

    顺便做的事
        超过 KEEP_SNAPSHOTS 份时，把最旧的删掉。

    例子（碰文件系统，见 tests/test_snapshot.py）
        记忆文件不存在  ->  None，什么都不做
        记忆文件有内容  ->  snapshots/MEMORY-20260918-164700.md
    """
    memory_path = memory_path or MEMORY_FILE
    snapshot_dir = snapshot_dir or SNAPSHOT_DIR

    if not memory_path.exists():
        return None                       # 还没有记忆，没什么可存的

    snapshot_dir.mkdir(parents=True, exist_ok=True)
    target = _free_snapshot_path(snapshot_dir)
    target.write_text(memory_path.read_text(encoding="utf-8-sig"), encoding="utf-8")

    # 只留最近 KEEP_SNAPSHOTS 份。名字带时间戳，所以按名字排序 = 按时间排序。
    老的 = list_snapshots(snapshot_dir)[KEEP_SNAPSHOTS:]
    for path in 老的:
        with suppress(OSError):
            path.unlink()

    return target


def list_snapshots(snapshot_dir: Path | None = None) -> list[Path]:
    """列出所有快照，**最新的排最前面**。

    谁会用它
        snapshot_memory()（清理旧的）、restore_snapshot()（找上一版）、
        main.py 的 /memory-log。

    返回什么
        Path 列表，从新到旧。目录不存在就是空列表（不是错误）。

    例子（见 tests/test_snapshot.py）
        [snapshots/MEMORY-20260918-164700.md, snapshots/MEMORY-20260918-101200.md]
    """
    snapshot_dir = snapshot_dir or SNAPSHOT_DIR
    if not snapshot_dir.exists():
        return []
    return sorted(snapshot_dir.glob("MEMORY-*.md"), reverse=True)


def diff_memory(old: str, new: str) -> str:
    """比对前后两版记忆，逐行报告增删。**由程序算，不问模型。**

    谁会用它
        dream()（整理完报告改了什么）、main.py 的 /memory-log。

    传入什么
        old / new: 两版记忆的正文。

    返回什么
        一段人看的文本，形如：

            + 向量库用 Pinecone
            - 提示词全部外置在 templates/，改文件即生效

        没有任何变化时返回 ""（调用方据此决定要不要打印）。

    为什么不用 difflib 的 unified_diff
        那个格式带 @@ 行号和上下文行，适合给 patch 程序吃。
        这里是给人扫一眼的，只关心"多了什么、少了什么"，
        所以用 Differ 挑出 + 和 - 开头的行就够了。

    为什么这件事一定要程序做
        模型漏抄一条时，它自己是不知道的——它以为自己抄全了。
        问它"你改了什么"，它只会复述它**打算**做的事，不是实际发生的事。
        nanobot 的注释把这点说得很直白：
        "Pure filesystem/git ground truth — never LLM narrative"。

    例子
        >>> 旧 = "- 从零复刻 nanobot\\n- 提示词外置在 templates/"
        >>> 新 = "- 从零复刻 nanobot\\n- 向量库用 Pinecone"
        >>> print(diff_memory(旧, 新))
        - - 提示词外置在 templates/
        + - 向量库用 Pinecone
        >>> diff_memory("一样的", "一样的")
        ''
    """
    差异 = difflib.Differ().compare(old.splitlines(), new.splitlines())
    行 = [d for d in 差异 if d.startswith(("+ ", "- ")) and d[2:].strip()]
    return "\n".join(行)


def restore_snapshot(
    which: int = 0,
    memory_path: Path | None = None,
    snapshot_dir: Path | None = None,
) -> tuple[bool, str]:
    """把 MEMORY.md 回滚到某一份快照。

    谁会用它
        main.py 的 /memory-undo。

    传入什么
        which:        用第几份快照。0 = 最新的一份（也就是"上一版"）。
        memory_path / snapshot_dir: 两个位置，测试里会换掉。

    返回什么
        (成功了吗, 给人看的一句话)。

    为什么回滚之前还要再拍一张快照
        **撤销也要能被撤销。** 你回滚之后可能发现"其实刚才那版是对的"，
        这时候得能再回来。不先拍一张的话，当前这版就被覆盖没了——
        我们又回到了第 28 讲开头那个"退不回去"的处境，只是方向反过来。

    为什么用序号而不用文件名
        敲 /memory-undo 时最常见的需求是"退回上一版"，序号 0 最省事。
        真要指定某一份时，/memory-log 会把序号列出来。

    例子（见 tests/test_snapshot.py）
        没有快照        ->  (False, "还没有任何快照……")
        which=0        ->  (True, "已回滚到 MEMORY-20260918-164700.md")
    """
    memory_path = memory_path or MEMORY_FILE
    snapshots = list_snapshots(snapshot_dir)

    if not snapshots:
        return False, "还没有任何快照，没法回滚。（快照是整理记忆时自动存的）"
    if which >= len(snapshots):
        return False, f"只有 {len(snapshots)} 份快照，没有第 {which} 份。"

    # ★ 先把当前这版存下来，否则回滚本身就变成了不可逆操作
    snapshot_memory(memory_path, snapshot_dir)

    源 = snapshots[which]
    memory_path.parent.mkdir(parents=True, exist_ok=True)
    memory_path.write_text(源.read_text(encoding="utf-8-sig"), encoding="utf-8")
    return True, f"已回滚到 {源.name}（回滚前那版也存成快照了，可以再退回来）"

# ══════════════════════════════════════════════════════════════
# 纠正与溯源（第 29 讲）
# ══════════════════════════════════════════════════════════════
#
# 第 28 讲加的快照是**事后补救**：记错了 -> 你发现 -> 整个退回上一版。
# 但"退回去"是个粗暴的动作：这一版里可能有九条对的、一条错的，
# 退回去等于把九条对的也一起扔了。
#
# 这一讲加两个更细的动作：
#
#     纠正   你说一句"不对，是 Chroma"，只推翻错的那一条    -> correct_fact
#     溯源   记忆里某句话可疑，查它是从哪次对话来的           -> trace_fact
#
# 两个动作都不需要新机制，用的全是前面几讲已经有的零件：
#
#     纠正 = append_fact(tag="correction") + dream()
#            [correction] 这个标签第 26 讲就定下了，dream.md 里也早写了
#            "删掉被它推翻的那条"——一直没人真正用过它而已
#     溯源 = 流水账每条都带 session 字段（第 25 讲埋的伏笔）


def correct_fact(text: str, session: str = "", path: Path | None = None) -> int:
    """记一条纠正。返回它在流水账里的编号。

    谁会用它
        main.py 的 /correct 命令。

    传入什么
        text:    正确的说法。**最好把错的也一起说出来**，例如
                 "项目的向量库是 Chroma，不是 Pinecone"——
                 这样模型整理时才知道该删掉哪一条。
        session: 当前会话 key，记成出处。
        path:    流水账文件。

    返回什么
        编号（int）。和 append_fact 一样。

    会抛什么
        ValueError —— text 去掉空白后是空的（由 append_fact 抛）。

    它和 append_fact 的唯一区别
        标签写死成 "correction"。就这一点。

        **那为什么还要单独开一个函数**：因为"纠正"是一个有名字的动作，
        调用方写 correct_fact(...) 比写 append_fact(..., tag="correction")
        更容易看懂，也不会把标签拼错。这是一层很薄的门面，不是抽象。

    为什么纠正不直接去改 MEMORY.md
        因为**流水账是账本，只能追加、不能涂改**（不变量 7）。
        错了就再记一条冲正，让下一次整理去合并——这是会计的做法，
        也是我们从第 25 讲就定下的规矩。

        直接改总账还有一个坏处：改完之后没人知道为什么改，
        而记成一条 correction 之后，它和别的事实一样带着时间和出处。

    例子（碰文件系统，见 tests/test_correction.py）
        correct_fact("向量库是 Chroma，不是 Pinecone")  ->  7
    """
    return append_fact(text, session=session, tag="correction", path=path)


def trace_fact(keyword: str, path: Path | None = None) -> list[dict[str, Any]]:
    """在流水账里找出含某个关键词的事实，用来查"这句话是哪来的"。

    谁会用它
        main.py 的 /why 命令。

    传入什么
        keyword: 要找的词，比如 "Pinecone"。**不区分大小写**，
                 因为你多半是从记忆里随手复制一个词过来的。
        path:    流水账文件。

    返回什么
        匹配的记录列表，**从新到旧**（最近记的最可能是你要查的那条）。
        一条都没有就是空列表。

        每条记录带着 cursor / at / tag / content / session 五样，
        其中 session 就是出处——拿它去 `python main.py --session <key>`
        就能翻回当初那次对话的原文。

    为什么这件事做得到（第 25 讲埋的伏笔）
        流水账每条都记了 session。当时写的理由是"第 29 讲要用"，
        就是现在：**总账上的一句结论，能一路查回产生它的那次对话**。

        nanobot 做不到这一点——它的 Dream 看到的是已经压缩过的摘要，
        指不回原始会话。我们能做到是因为不变量 1："存档存全文"。

    为什么不去 MEMORY.md 里找
        总账是**合并过**的，一句话可能来自好几条流水。
        要查出处只能回到流水账——那里才是一条一条、带着时间和来源的原始记录。

    例子（见 tests/test_correction.py）
        trace_fact("Pinecone")  ->  [{"cursor": 7, "tag": "correction", ...},
                                     {"cursor": 5, "tag": "durable", ...}]
    """
    词 = keyword.strip().lower()
    if not 词:
        return []

    命中 = [f for f in read_facts(path=path) if 词 in f.get("content", "").lower()]
    return sorted(命中, key=lambda f: f["cursor"], reverse=True)