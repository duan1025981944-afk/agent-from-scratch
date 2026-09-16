"""把存档 messages 加工成"这一次要发给模型的东西"。

──────────────────────────────────────────────────────────────
这个文件是什么
──────────────────────────────────────────────────────────────
一道**加工车间**。输入是完整存档，输出是"这一次请求要发出去的那份"。

为什么要加工？因为存档只增不减，而每一圈都要把它发给模型。发原样的话
有两个后果：贵（每圈都为整份历史付一次钱），以及超出模型的上下文上限。

车间里有两道工序，都只作用于输出，**绝不碰输入**：

    工序一  截断    单条工具结果超过 4000 字符 -> 剪短，末尾注明省略了多少
    工序二  清理    总量超过 CLEAR_TRIGGER     -> 旧的工具结果换成一句占位说明

──────────────────────────────────────────────────────────────
这个文件不是什么
──────────────────────────────────────────────────────────────
- 它**不**修改传进来的 messages。这是本文件唯一的铁律（见下面"铁律"一节）
- 它**不**写盘、**不**调模型。它是同步的纯函数，跑一次几毫秒
- 它**不**负责摘要压缩。那要调模型、会失败，在 agent/compaction.py
- 它**不**知道谁会发这份 payload、发给谁

──────────────────────────────────────────────────────────────
铁律：绝不修改 messages
──────────────────────────────────────────────────────────────
传进来的 messages 是**存档**——它会被写进 jsonl，是这段对话的唯一真相。
截断和清理都是**有损**的，一旦改了它，原文就永久没了。

所以代码里凡是要改一条消息，写法永远是：

    shrunk = dict(msg)          # 先复制出一个新字典
    shrunk["content"] = ...     # 改副本
    payload.append(shrunk)      # 放进输出

有三个测试守着这条铁律：
test_archive_keeps_full_text_while_model_sees_truncated、
test_archive_and_structure_untouched、test_original_list_is_untouched。

──────────────────────────────────────────────────────────────
两道防线，四个阈值
──────────────────────────────────────────────────────────────
上下文变长有两种对策，按**成本**排队：

    0 ─────── 100,000 ─────────── 190,000 ──────────> 1M（模型硬上限）
                 |                     |
         CLEAR_TRIGGER          CONTEXT_BUDGET
         本文件的工序二            agent/compaction.py
         不花钱，早点做             要多调一次模型，最后才做

这个文件只管第一道。第二道在 compaction.py，它的触发条件是
"build_payload 加工完之后还超预算吗"——也就是说，**先把免费手段用完，
不够了才动花钱的手段**。

四个阈值的出处见下面各个常量的注释。
"""
from __future__ import annotations

from typing import Any

from agent.tokens import estimate_message_tokens, estimate_tokens

# 单条工具结果的字符上限。不管总量多少，超过就截断。
# 4000 字符大约等于 1,200 token（代码）到 2,400 token（中文）。
# 防的是"一次 read_file 读了个大文件就撑爆上下文"。
MAX_TOOL_RESULT_CHARS = 4000

# ── 两个阈值，不是一个 ──────────────────────────────────────────────
# 清理工具结果不花钱，所以早一点动手；摘要压缩要多调一次模型，留到最后。
# 数值对齐 Anthropic 上下文管理 API 的默认值（clear 100k / compact 150k）。
CLEAR_TRIGGER = 100_000       # 超过这个数就开始清理旧工具结果（便宜）
CONTEXT_BUDGET = 190_000      # 超过这个数就必须摘要压缩（贵）
#
# 190,000 只占 DeepSeek 1M 窗口的 19%，看着很保守。留这么多余量**不是怕装不下**，
# 是因为装太多模型会变笨（Context Rot：有用信息的占比越来越低，被噪音淹没）。
# Claude Code 社区普遍反映"快满了才压缩"太晚，希望更早动手，就是这个道理。

# 一旦开始清理，至少要清掉这么多，否则干脆一条都不清。
#
# 为什么：清理会改动 payload 中段，而模型的提示缓存是**从第 0 个 token 起
# 逐个匹配**的——中间改一个字，从那里往后全部失效，要按未命中价重算，
# 还要再付一次缓存写入的钱。每轮只清一点点，等于每轮都付一次这笔钱。
# 一次清够，这笔钱只付一次。
#
# 对应 Anthropic 的 clear_at_least 参数，文档原话是"这有助于判断清理上下文
# 是否值得打断你的提示缓存"。
CLEAR_AT_LEAST = 20_000

# 最近几条工具结果不清——模型可能还在用。对应 Anthropic 的 keep（默认 3）。
KEEP_TOOL_RESULTS = 3

# 旧工具结果被清掉时，模型看到的替代文字。
# 要讲清两件事：**发生了什么**、**需要的话怎么找回来**。
# 实测有效：模型看到这句之后会自己重新调一次工具，而不是凭印象瞎答。
PLACEHOLDER = "[这条工具结果较早，为节省上下文已省略。原文仍在会话存档里；如果还需要，请重新调用工具。]"


def _truncate(content: str) -> str:
    """把超长的工具结果剪短，并在末尾告诉模型丢了多少。

    这是个内部函数（名字带下划线 = 别的文件不该直接调它）。

    传入什么
        content: 工具返回的原文，**调用方应保证它确实超过了
                 MAX_TOOL_RESULT_CHARS**（本函数不自己判断）。

    返回什么
        剪短后的字符串：前 4000 个字符 + 一行说明。

    为什么要加那行说明
        直接截断的话，模型会以为文件就到这儿为止，然后基于残缺内容下结论。
        加了说明，它知道后面还有，需要的话可以换个方式再读。

    例子
        >>> out = _truncate("x" * 4500)
        >>> out[:10]
        'xxxxxxxxxx'
        >>> out.endswith("（内容过长，已截断，省略 500 个字符）")
        True

    已知问题
        这里报的是**字符数**，而 list_dir 工具报的是**字节数**。
        中文一个字符是 3 个字节，两处单位不一致，模型可能算错。
        待办里记着，暂时没改。
    """
    dropped = len(content) - MAX_TOOL_RESULT_CHARS
    return (
        content[:MAX_TOOL_RESULT_CHARS]
        + f"\n\n…（内容过长，已截断，省略 {dropped:,} 个字符）"
    )


def _protected_from(payload: list[dict[str, Any]], keep: int) -> int:
    """算出"从哪个下标开始的消息不许清理"。

    这是个内部函数，只给 _replace_old_tool_results 用。

    传入什么
        payload: 正在加工的消息列表。
        keep:    最少保留几条工具结果（默认来自 KEEP_TOOL_RESULTS = 3）。

    返回什么
        一个下标。**这个下标本身、以及它之后的所有消息，都不许动**；
        只有 range(返回值) 范围内的才可以清。

        三种特殊返回值：
            len(payload)  完全没有工具结果 -> 全都不用动
            0             工具结果总共不超过 keep 条 -> 一条都不许清

    两条保护，取更宽的那条
        1. **按条数**：最近 keep 条工具结果不许清。
           这是 Anthropic context editing 的 keep 参数，默认 3。

        2. **按批次**：最后一次工具调用的**整批**结果不许清。
           为什么需要这一条：模型可能一次并行调 5 个工具，只按条数留 3 条
           的话，会把它正要用来回答的那批削掉 2 条。

        取 min() 就是"哪条保护得多就听哪条"。

    例子
        没有工具结果时，返回列表长度（等于"全都保护"）：

        >>> _protected_from([{"role": "user", "content": "你好"}], keep=3)
        1

        工具结果只有 2 条、不到 keep=3，返回 0（一条都不许清）：

        >>> msgs = [{"role": "tool", "content": "a"}, {"role": "tool", "content": "b"}]
        >>> _protected_from(msgs, keep=3)
        0

        5 条工具结果、keep=1 时，只保护最后 1 条（下标 4）：

        >>> msgs = [{"role": "tool", "content": str(i)} for i in range(5)]
        >>> _protected_from(msgs, keep=1)
        4
    """
    tool_indexes = [i for i, m in enumerate(payload) if m.get("role") == "tool"]
    if not tool_indexes:
        return len(payload)                               # 没有工具结果，无事可做
    if len(tool_indexes) <= keep:
        return 0                                          # 总共就这么几条，全保留

    by_count = tool_indexes[-keep]

    # 往回找最后一条"模型要求调工具"的消息。它和它之后的结果都要护住。
    by_batch = len(payload)
    for i in range(len(payload) - 1, -1, -1):
        if payload[i].get("tool_calls"):
            by_batch = i
            break

    return min(by_count, by_batch)


def _replace_old_tool_results(
    payload: list[dict[str, Any]],
    trigger: int,
    clear_at_least: int = CLEAR_AT_LEAST,
    keep: int = KEEP_TOOL_RESULTS,
) -> list[dict[str, Any]]:
    """工序二：总量超过 trigger 时，把旧的工具结果换成 PLACEHOLDER。

    这是个内部函数，只给 build_payload 用。

    传入什么
        payload:        正在加工的列表。**本函数会原地修改它**——但那是
                        build_payload 自己新建的列表，存档不受影响。
        trigger:        超过多少 token 才开始清理。
        clear_at_least: 一旦开始清，至少要清掉多少，否则一条都不清。
        keep:           最少保留几条工具结果。

    返回什么
        同一个 payload 列表（改过或没改）。返回它只是为了写起来顺手，
        调用方拿到的和传进去的是同一个对象。

    三条规则
        1. **从最旧的开始换。** 这样"已经换掉的，以后历史再变长也不会被换回来"。
           如果改成"从最大的开始换"，历史一变长，最大的那条可能换了位置，
           之前换掉的就会变回原文——模型会看到历史内容忽有忽无，
           而且每次变动都要重建一次缓存。

        2. **最近几条 / 最新那一批不换**（见 _protected_from）。

        3. **要么清够 clear_at_least，要么一条都不清。**
           代码上的体现是先把要换的记进 cleared 列表，最后统一判断够不够，
           不够就直接 return，一个字都不改。

    拿到之后怎么用
        不用做别的，它返回的就是能直接发出去的那份。
        想知道清掉了几条，数一下 PLACEHOLDER：

            omitted = sum(1 for m in payload if m.get("content") == PLACEHOLDER)

        agent/runner.py 每圈打印的"已省略 N 条旧工具结果"就是这么来的。
    """
    used = estimate_tokens(payload)
    if used <= trigger:
        return payload                                    # 没到阈值，什么都不做

    protected_from = _protected_from(payload, keep)

    # 目标：既要降到阈值以下，又至少要清掉 clear_at_least，取更严的那个
    target = min(trigger, used - clear_at_least)

    cleared: list[tuple[int, dict[str, Any]]] = []        # 先记下来，够不够再说
    saved_total = 0

    for i in range(protected_from):                       # 只看保护线之前的消息
        if used - saved_total <= target:
            break                                         # 清够了就停
        msg = payload[i]
        if msg.get("role") != "tool" or msg.get("content") == PLACEHOLDER:
            continue                                      # 不是工具结果，或已经清过了

        replaced = dict(msg)                              # 复制，不碰原件
        replaced["content"] = PLACEHOLDER
        saved = estimate_message_tokens(msg) - estimate_message_tokens(replaced)
        if saved <= 0:
            continue                                      # 原本就比占位说明短，换了反而更长

        cleared.append((i, replaced))
        saved_total += saved

    # ★ 规则 3：清不够就一条都不清。只超了一点点时，"清够这一点点"就算达标
    if saved_total < min(clear_at_least, used - trigger):
        return payload                                    # 不如不清——留给摘要压缩去处理

    for i, replaced in cleared:
        payload[i] = replaced                             # 换的是 payload 里的一格，不是 messages
    return payload


def build_payload(
    messages: list[dict[str, Any]],
    clear_trigger: int = CLEAR_TRIGGER,
    clear_at_least: int = CLEAR_AT_LEAST,
    keep: int = KEEP_TOOL_RESULTS,
) -> list[dict[str, Any]]:
    """加工车间的总入口：存档进，"这一次要发的东西"出。

    谁会用它
        - agent/runner.py：每转一圈都调一次，把结果发给模型
        - agent/compaction.py：判断"免费手段用完还超不超预算"，以及写摘要时

    传入什么
        messages:       完整的内存 messages（含 system 消息）。
                        **本函数保证不修改它。**
        clear_trigger:  清理阈值。默认 CLEAR_TRIGGER。
                        测试里会传很小的值（比如 500）来逼出清理行为。
        clear_at_least: 一次至少清多少。默认 CLEAR_AT_LEAST。
        keep:           最少保留几条工具结果。默认 KEEP_TOOL_RESULTS。

    返回什么
        一个**新的**列表，可以直接交给 provider.chat()。

        它和输入的关系：
            条数        完全一样，一条不多一条不少
            顺序        完全一样
            tool_call_id 配对  完全一样（不变量 3）
            内容        工具结果可能被截断或换成占位说明，其他角色原样

        条数和配对不变很重要——清理只改内容、不删消息，所以永远不会
        出现"有工具调用却没有对应结果"的孤儿，那会让 API 直接返回 400。

    拿到之后怎么用
        直接发：

            payload = build_payload(messages)
            reply = await provider.chat(payload, tools=schemas)

        想知道它多大、超没超预算，配合 measure_payload()：

            used, over = measure_payload(payload)

    例子
        不超阈值时原样返回内容：

        >>> msgs = [{"role": "user", "content": "你好"}]
        >>> build_payload(msgs) == msgs
        True

        超长的工具结果会被截断，但**存档一个字没动**：

        >>> msgs = [{"role": "tool", "tool_call_id": "c1", "content": "x" * 5000}]
        >>> payload = build_payload(msgs)
        >>> len(payload[0]["content"]) < 5000
        True
        >>> len(msgs[0]["content"])
        5000
    """
    payload: list[dict[str, Any]] = []

    # ── 工序一：截断单条过长的工具结果 ──
    for msg in messages:
        # 只有工具结果需要截断，其他角色原样放行
        if msg.get("role") != "tool":
            payload.append(msg)
            continue

        content = msg.get("content") or ""
        if len(content) <= MAX_TOOL_RESULT_CHARS:
            payload.append(msg)
            continue

        # ★ 关键三行：先复制，再改副本，原件一个字都不碰
        shrunk = dict(msg)                       # 复制出一个新字典
        shrunk["content"] = _truncate(content)
        payload.append(shrunk)

    # ── 工序二：总量过了清理阈值，把旧的工具结果换成占位说明 ──
    return _replace_old_tool_results(payload, clear_trigger, clear_at_least, keep)


def measure_payload(
    payload: list[dict[str, Any]], budget: int = CONTEXT_BUDGET
) -> tuple[int, bool]:
    """量一下这份 payload：大约多少 token、超没超预算。**只量，不改。**

    谁会用它
        agent/runner.py，每圈打印一行观察信息。

    传入什么
        payload: 一份消息列表。通常是 build_payload() 的输出——
                 量**加工后**的那份才有意义，量内存里的全文会偏大很多。
        budget:  预算。默认 CONTEXT_BUDGET（190,000）。

    返回什么
        一个二元组 (used, over)：

            used: int   估算的 token 数
            over: bool  是否超过 budget

    拿到之后怎么用
        Python 的元组可以直接拆开接：

            used, over = measure_payload(payload)
            if over:
                print(f"超了！当前 {used:,}，预算 {budget:,}")

    为什么返回两个值而不是只返回 used
        让调用方自己写 `used > CONTEXT_BUDGET` 也行，但那样"预算是哪个常量"
        这件事就散在各个调用点。返回 over 等于把判断收在一处。

    例子
        >>> measure_payload([{"role": "user", "content": "你好"}], budget=100)
        (5, False)
        >>> used, over = measure_payload([{"role": "tool", "content": "x" * 4000}], budget=100)
        >>> over
        True
    """
    used = estimate_tokens(payload)
    return used, used > budget
