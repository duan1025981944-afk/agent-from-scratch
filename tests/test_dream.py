"""Dream 整理的测试。对应第 27 讲。

规矩：**没正常跑完，绝不推进游标**（不变量 8）。

每个测试的 docstring 末尾写了"负对照"：把代码改成那样，这个测试必须红。
"""
from __future__ import annotations

import asyncio

import pytest

from agent.memory import (
    DREAM_BATCH,
    build_dream_prompt,
    dream,
    read_dream_cursor,
    write_dream_cursor,
)
from providers.base import ModelReply, Provider


class StubProvider(Provider):
    """假模型：返回固定内容，或者抛错，并记下被调用几次。

    和 tests/test_compaction.py、tests/test_fact_extraction.py 里的是同一个套路。
    Dream 要调模型，测试里不能真调。
    """

    def __init__(self, reply: str | None = "## 用户\n- 用中文", fail: bool = False):
        self.reply, self.fail, self.calls = reply, fail, 0
        self.last_prompt = ""

    async def chat(self, messages, tools=None):
        self.calls += 1
        self.last_prompt = messages[-1]["content"]
        if self.fail:
            raise RuntimeError("模型炸了")
        return ModelReply(content=self.reply)


@pytest.fixture
def 记忆(tmp_path):
    """三个文件的位置，全在临时目录里——测试碰不到真实记忆。

    返回一个小工具对象：写流水账、读总账、读游标，都走它。
    """
    class 现场:
        memory = tmp_path / "MEMORY.md"
        history = tmp_path / "history.jsonl"
        cursor = tmp_path / ".dream_cursor"

        def 记流水(self, *facts: tuple[str, str]) -> None:
            """facts 是 (标签, 正文) 的序列，按顺序编号写进流水账。"""
            from agent.memory import append_fact

            for tag, content in facts:
                append_fact(content, tag=tag, path=self.history)

        def 跑一次(self, provider):
            return asyncio.run(
                dream(
                    provider,
                    memory_path=self.memory,
                    history_path=self.history,
                    cursor_path=self.cursor,
                )
            )

        def 总账(self) -> str:
            return self.memory.read_text(encoding="utf-8") if self.memory.exists() else ""

    return 现场()


# ── 游标：读写与容错 ─────────────────────────────────────────


def test_cursor_starts_at_zero(tmp_path):
    """从没整理过 = 游标 0 = 流水账全都要整理。

    负对照：把 read_dream_cursor 的 try/except 去掉 → FileNotFoundError，这条红。
    （最初这里还有一句 `if not path.exists(): return 0`，写负对照时发现删掉它
    测试照样绿——因为 FileNotFoundError 本来就被 except OSError 接住了。
    **那一行是重复的**，于是删掉了。负对照的另一个用处：发现多余的代码。）
    """
    assert read_dream_cursor(tmp_path / "没有这个文件") == 0


def test_broken_cursor_falls_back_to_zero(tmp_path):
    """游标文件被手改坏了，当作 0，不报错。

    最坏的后果是把整理过的再整理一遍，而 Dream 是幂等的——
    为这个崩掉整个程序不值得。

    负对照：把 read_dream_cursor 里的 try/except 去掉 → ValueError，这条红。
    """
    f = tmp_path / ".dream_cursor"
    for 坏内容 in ["坏了", "", "3.5", "-1"]:
        f.write_text(坏内容, encoding="utf-8")
        assert read_dream_cursor(f) == 0


def test_cursor_round_trip(tmp_path):
    """写进去多少，读出来就是多少。目录不存在也要能写。

    负对照：把 write_dream_cursor 里的 mkdir 去掉 → 目录不存在时报错，这条红。
    """
    f = tmp_path / "还没建的目录" / ".dream_cursor"
    write_dream_cursor(7, f)

    assert read_dream_cursor(f) == 7


# ── 拼提示词：纯函数，不用调模型 ─────────────────────────────


def test_prompt_carries_current_memory_and_new_facts():
    """模型要输出整篇新总账，就必须同时看到旧总账和新事实。

    负对照：把 build_dream_prompt 里拼 current 那段去掉 → 旧记忆进不去，这条红。
    """
    prompt = build_dream_prompt(
        "## 用户\n- 用户叫 Himeko",
        [{"tag": "durable", "content": "项目用 Chroma"}],
    )

    assert "用户叫 Himeko" in prompt                    # 旧总账
    assert "[durable] 项目用 Chroma" in prompt          # 新事实（带标签）
    # 指令本身也要在。挑一句 dream.md 里独有的，别拿"整理"这种到处都有的词
    # 当哨兵——那样的断言永远绿，守不住任何东西。
    assert "不许添加流水账和旧记忆里都没有的内容" in prompt


def test_first_ever_dream_says_so():
    """第一次整理时总账是空的，要明确告诉模型"这是第一次"。

    不说的话，提示词里会出现一段空白，模型可能以为是内容被截断了。

    负对照：把 `or "（这是第一次整理，还没有长期记忆）"` 去掉 → 这条红。

    断言写的是**整句话**，不是"第一次整理"四个字——那四个字在 dream.md 的
    正文里也出现过，拿它当哨兵的话，把代码改坏了测试照样绿。
    （这条是写负对照时发现的，原来的断言就是假的。）
    """
    prompt = build_dream_prompt("", [{"tag": "permanent", "content": "用中文"}])

    assert "（这是第一次整理，还没有长期记忆）" in prompt


# ── 整理：成功路径 ───────────────────────────────────────────


def test_dream_writes_memory_and_advances_cursor(记忆):
    """★ 本讲的核心：整理成功 = 总账被覆盖 + 游标推进。

    负对照：把 dream 里 write_dream_cursor 那行去掉 → 游标还是 0，这条红。
    """
    记忆.记流水(("permanent", "用户用中文"), ("durable", "项目用 Chroma"))

    result = 记忆.跑一次(StubProvider("## 用户\n- 用中文\n\n## 项目\n- 用 Chroma"))

    assert result.ok and result.processed == 2
    assert "Chroma" in 记忆.总账()
    assert read_dream_cursor(记忆.cursor) == 2          # 推进到最后一条的编号


def test_nothing_new_is_not_a_failure(记忆):
    """流水账里没有新事实 —— 这是常态（刚整理完又敲了一次 /dream），不算失败。

    而且**不该调模型**：没东西可整理还花钱，纯浪费。

    负对照：把 `if not facts` 那个提前返回去掉 → provider.calls 变成 1，这条红。
    """
    provider = StubProvider()
    result = 记忆.跑一次(provider)

    assert result.ok and result.processed == 0
    assert provider.calls == 0                          # 一次模型都没调


def test_only_facts_after_the_cursor_are_processed(记忆):
    """游标之后的才整理，之前的不重复整理。

    负对照：把 dream 里 read_facts(since=cursor) 的 since 去掉 → 前两条也进提示词，这条红。
    """
    记忆.记流水(("durable", "第一条"), ("durable", "第二条"), ("durable", "第三条"))
    write_dream_cursor(2, 记忆.cursor)                  # 假装前两条整理过了

    provider = StubProvider()
    result = 记忆.跑一次(provider)

    assert result.processed == 1
    assert "第三条" in provider.last_prompt
    assert "第一条" not in provider.last_prompt


def test_big_batch_is_split_and_cursor_stops_midway(记忆):
    """流水账攒太多时一次只整理一批，游标停在这批的最后一条。

    剩下的下次接着来——不会漏，也不会重。

    负对照：把 `facts[:DREAM_BATCH]` 改成 `facts` → 一次全整理完，这条红。
    """
    记忆.记流水(*[("durable", f"第{i}条") for i in range(DREAM_BATCH + 5)])

    result = 记忆.跑一次(StubProvider())

    assert result.processed == DREAM_BATCH
    assert read_dream_cursor(记忆.cursor) == DREAM_BATCH
    assert "还剩 5 条" in result.note


# ── 整理：失败路径（不变量 8） ───────────────────────────────


def test_model_failure_touches_nothing(记忆):
    """★ 不变量 8：调模型失败时，总账和游标都不许动。

    推进了游标就等于宣布"这批已经整理进总账了"。要是其实没有，
    这批事实就永远不会再被整理——还在流水账里，但再也没人看它们一眼。

    负对照：把 dream 里的 try/except 去掉 → 异常冒到上层，这条红
    （或者把 write_dream_cursor 挪到调模型之前 → 游标变成 1，这条也红）。
    """
    记忆.记流水(("permanent", "用户用中文"))

    result = 记忆.跑一次(StubProvider(fail=True))

    assert not result.ok
    assert not 记忆.memory.exists()                     # 总账连建都没建
    assert read_dream_cursor(记忆.cursor) == 0          # 游标没动


def test_empty_reply_does_not_wipe_memory(记忆):
    """★ 模型返回空时，绝不能拿空字符串去覆盖总账——那等于把记忆全删了。

    模型返回空的原因很多：被安全策略拦了、超时截断了、网络吐了半截。
    不管哪种，"没返回内容"都不等于"记忆该清空"。

    负对照：把 `if not 新总账` 那段去掉 → 总账被覆盖成空，这条红。
    """
    记忆.memory.write_text("## 用户\n- 这是之前整理好的记忆\n", encoding="utf-8")
    记忆.记流水(("permanent", "用户用中文"))

    result = 记忆.跑一次(StubProvider(reply=""))

    assert not result.ok
    assert "之前整理好的记忆" in 记忆.总账()             # 老内容一个字没少
    assert read_dream_cursor(记忆.cursor) == 0


def test_failed_dream_can_be_retried(记忆):
    """失败之后再跑一次要能成功——因为游标没动，那批事实还在待整理区。

    这条测的是"失败不是死局"，是不变量 8 真正的价值所在。

    负对照：让失败路径也推进游标 → 第二次 processed 变成 0，这条红。
    """
    记忆.记流水(("permanent", "用户用中文"))

    assert not 记忆.跑一次(StubProvider(fail=True)).ok      # 第一次：炸了
    result = 记忆.跑一次(StubProvider("## 用户\n- 用中文"))  # 第二次：好了

    assert result.ok and result.processed == 1
    assert "用中文" in 记忆.总账()
