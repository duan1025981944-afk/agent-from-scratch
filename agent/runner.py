"""Agent 的核心循环：调模型 -> 要不要用工具 -> 执行 -> 再调，直到给出答复。

──────────────────────────────────────────────────────────────
这个文件是什么
──────────────────────────────────────────────────────────────
整个项目的**发动机**。给它一份消息和一套工具，它转完循环把结果交回去。

循环的形状只有这么点东西：

    for i in range(max_iterations):
        payload = build_payload(messages)         # 加工成这次要发的
        reply = await provider.chat(payload)      # 调模型
        messages.append(reply.raw)                # 模型说的话先入链
        if not reply.wants_tools:                 # ★ 唯一的分岔点
            break                                 #   没有工具调用 = 最终答复
        执行每个工具，把结果 append 进 messages    #   然后回到开头再转一圈

**这就是 Agent Loop 的全部秘密**：模型每次要么"要用工具"（继续转），
要么"说完了"（退出），没有第三种可能。

──────────────────────────────────────────────────────────────
这个文件不是什么（这一条比上一条重要）
──────────────────────────────────────────────────────────────
- 它**不知道 SOUL.md 存在**。系统提示是谁拼的、里面写了什么，它不关心
- 它**不知道在调哪家模型**。它只知道"provider 上有个 chat 方法"
- 它**不知道会话存在磁盘的哪里**，也不写盘
- 它**不知道消息是从 CLI 来的还是别处来的**

这叫"循环层不碰产品层"。nanobot 的 AgentRunner 类注释写的是
"Run a tool-capable LLM loop without product-layer concerns"，同一个意思。

好处：换渠道（CLI -> 微信）、换模型、换存储，这个文件一个字都不用改。
代价：它拿不到上层的信息，所以像"把当前会话名打进提示里"这种需求，
必须由上层拼好了再传进来，不能在这里临时取。

──────────────────────────────────────────────────────────────
一轮里 messages 是怎么变长的
──────────────────────────────────────────────────────────────
以「读一个文件」为例：

    进来时   [system, user("读一下 runner.py")]
    第1圈后  [..., assistant(tool_calls=[read_file])]        模型说：我要调工具
             [..., tool("文件内容……")]                      工具返回
    第2圈后  [..., assistant("它是主循环执行器。")]           模型给出答复 -> break

**assistant 和 tool 两条都要 append**，不能只塞工具结果。
因为下一圈模型看到的必须是完整因果链：
"我说要调 read_file -> 它返回了这些内容"。只塞结果的话，
模型会看到一段凭空出现的文字，不知道是谁调的、为什么调。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from agent.payload import (
    CLEAR_TRIGGER,
    CONTEXT_BUDGET,
    PLACEHOLDER,
    build_payload,
    measure_payload,
)
from agent.tools.base import Tool, ToolResult
from providers.base import Provider

# 统一的行动建议，附在所有错误文本后面。
#
# 为什么要有它：模型看到 "Error: xxx" 的默认反应是**原样再试一次**。
# 这句话在打断那个反射，让它换个思路。
#
# 这是"错误信息要带修正建议"这条 ACI 原则的最低成本实现——不给每种错误
# 单独写建议，统一附一句通用的。nanobot 里也是同样的做法
# （它的原文是 "[Analyze the error above and try a different approach.]"）。
TOOL_ERROR_HINT = "提示：检查参数是否正确，或换一种方式完成任务。不要用同样的参数重试。"


def _with_hint(text: str) -> str:
    """给错误文本统一附上行动建议。

    这是个内部函数，只给本文件用。

    传入什么
        text: 错误说明，比如 "文件不存在：a.py"。

    返回什么
        原文 + 空行 + TOOL_ERROR_HINT。

    例子
        >>> _with_hint("文件不存在").endswith("不要用同样的参数重试。")
        True
        >>> _with_hint("文件不存在").startswith("文件不存在")
        True
    """
    return f"{text}\n\n{TOOL_ERROR_HINT}"


@dataclass
class AgentRunSpec:
    """给 runner 的一张**订单**：要转什么、用什么、最多转几圈。

    谁会产生它
        main.py，每轮对话创建一个。

    谁会用它
        AgentRunner.run()。

    字段
        initial_messages: 起始消息列表（含 system）。
                          **runner 会先 list() 拷一份**，不会改你传进来的这个列表。

        provider:         模型接口，见 providers/base.py 的 Provider。

        max_iterations:   最多转几圈，默认 10。一圈 = 调一次模型。
                          转满还没结束的话，返回 stop_reason="max_iterations"。

        tools:            {工具名: 工具实例}，通常直接传 discover_tools() 的结果。
                          **不传就是空字典**，那模型只能用文字回答。

    为什么要专门定义一个类，而不是给 run() 加一堆参数
        参数只会越加越多（nanobot 的对应结构有 26 个字段）。做成一张订单之后，
        加字段不用改 run() 的签名，调用方也不用记参数顺序。

    例子
        >>> spec = AgentRunSpec(initial_messages=[], provider=None)
        >>> spec.max_iterations
        10
        >>> spec.tools
        {}
    """

    initial_messages: list[dict]
    provider: Provider
    max_iterations: int = 10
    tools: dict[str, Tool] = field(default_factory=dict)


@dataclass
class AgentRunResult:
    """runner 转完之后交回的结果。

    谁会产生它
        AgentRunner.run()。

    谁会用它
        main.py：拿 messages 更新自己的列表、拿 final_content 打印、
        拿 stop_reason 判断这轮是"答完了"还是"没答完"。

    字段
        final_content: 模型给用户的最终文字。**转满圈数时是 None**，
                       所以打印前要先判断。

        messages:      转完之后的完整消息列表。

                       **有一条重要约定**：它 = 传进去的 initial_messages
                       + 这一轮新长出来的尾巴，顺序不变、中间不插东西。
                       main.py 的 save_turn(messages[boundary:]) 就靠这条约定
                       切出"新增的部分"。改这个文件时别破坏它。

        stop_reason:   为什么停下来的。目前两个值：

                           "completed"       模型给出了纯文本答复
                           "max_iterations"  转满圈数还没结束

        error:         预留字段，目前没用上。

    为什么要有 stop_reason
        **"循环停下来"和"任务完成了"是两件不同的事。** 两种情况循环都停了，
        但含义相反：一个是做完了，一个是配额用光、任务还悬着。
        少了这个字段，上层就分不清该庆祝还是该救火。

    例子
        >>> r = AgentRunResult(final_content="好了", messages=[])
        >>> r.stop_reason
        'completed'
    """

    final_content: str | None
    messages: list[dict]
    stop_reason: str = "completed"
    error: str | None = None


class AgentRunner:
    """Agent 循环的执行者。没有状态，一个实例可以反复用。"""

    async def run(self, spec: AgentRunSpec) -> AgentRunResult:
        """转完一轮 Agent 循环。

        谁会用它
            main.py，每轮对话调一次。

        传入什么
            spec: 一张 AgentRunSpec 订单。

        返回什么
            一个 AgentRunResult。

        拿到之后怎么用
            main.py 里的标准写法：

                result = await runner.run(spec)
                messages = result.messages               # 收回完整历史
                session.save_turn(messages[boundary:])   # 只落盘新增的
                if result.stop_reason == "max_iterations":
                    print("[转满圈数仍未完成]")
                elif result.final_content:
                    print(f"AI > {result.final_content}")

        会抛什么
            正常情况下什么都不抛——工具出错会被转成文本回给模型。

            唯一会往外抛的是 asyncio.CancelledError（用户按 Ctrl+C）。
            见下面工具执行那段的注释，它必须单独放行。

        为什么用 for 而不是 while True
            for i in range(max_iterations) 让"最多转 N 圈"成为**语法保证**，
            而不是靠记得写 break。漏写 break 的话，while True 是死循环，
            for 最坏也就转满 N 圈。

        for-else 是什么
            函数末尾那个和 for 对齐的 else，是 Python 的一个冷知识：
            **只有循环"自然跑完"（没被 break 打断）时才执行**。

                模型给出答复   -> break -> 不进 else
                转满 N 圈      -> 自然结束 -> 进 else -> 标记 max_iterations

            用它来处理"转满了"这种情况，一行判断都不用写。

        每圈的两行打印是什么
            临时的观察信息，不是功能。第一行是"尺子"量出来的估算值，
            第二行是模型回来之后的真实值和缓存命中率。两者对照能看出
            尺子准不准、缓存有没有被打断。调试完可以删。
        """
        # 拷一份，不改调用方的列表。
        # 注意这是**浅拷贝**：列表是新的，但里面的消息字典还是同一批对象。
        # 所以 append 新消息安全，"原地修改某条旧消息"则会影响调用方——
        # 这也是 build_payload 里改内容前必须 dict(msg) 复制的原因。
        messages = list(spec.initial_messages)

        # 工具 schema 每圈都一样，循环外算一次就够。
        # 末尾的 or None：一个工具都没有时传 None 而不是空列表（有些接口不收空列表）。
        schemas = [t.to_schema() for t in spec.tools.values()] or None

        for i in range(spec.max_iterations):
            # ── 第一步：把存档加工成"这一次要发的东西" ──
            # 每圈都重新算。因为 messages 每圈都在变长，该截断/该清理的也在变。
            payload = build_payload(messages)

            used, over = measure_payload(payload)
            omitted = sum(1 for m in payload if m.get("content") == PLACEHOLDER)
            note = f"，已省略 {omitted} 条旧工具结果" if omitted else ""
            flag = f"，超过预算 {CONTEXT_BUDGET:,}" if over else ""
            if not over and used > CLEAR_TRIGGER:
                flag = f"，已过清理阈值 {CLEAR_TRIGGER:,}"
            print(f"  [第 {i + 1} 圈] 上下文约 {used:,} token{note}{flag}")   # 临时观察

            # ── 第二步：调模型 ──
            # 重试、超时、字段翻译全部关在 provider 内部。
            # 在 runner 眼里，这是一个"要么成功、要么彻底失败"的操作。
            reply = await spec.provider.chat(payload, tools=schemas)

            u = reply.usage                                             # 临时观察
            if u.prompt_tokens:
                gap = (used - u.prompt_tokens) / u.prompt_tokens * 100
                print(
                    f"         实际 {u.prompt_tokens:,}（尺子误差 {gap:+.0f}%）"
                    f"｜缓存命中 {u.cache_hit_tokens:,}，未命中 {u.cache_miss_tokens:,}"
                    f"，命中率 {u.hit_rate:.0%}"
                )

            # 无论要不要用工具，assistant 这条都必须先入链。
            # 不然下一圈模型会看到一段凭空出现的工具输出，不知道是谁调的。
            messages.append(reply.raw)

            # ── 第三步：唯一的分岔点 ──
            if not reply.wants_tools:
                break                                        # 没有工具调用 = 最终答复

            # ── 第四步：执行模型要求的每个工具 ──
            # 串行执行。模型可能一次要求调五个，这里一个个来。
            # （nanobot 支持按 concurrency_safe 分批并发，我们跳过了：
            #   并发是优化，正确性优先。）
            for call in reply.tool_calls:
                print(f"    → {call.name}({call.arguments})")

                tool = spec.tools.get(call.name)

                # 情况 A：模型编了一个不存在的工具名。
                # 把可用工具列表告诉它，它下一圈就能改对。
                if tool is None:
                    available = "、".join(spec.tools) or "（没有可用工具）"
                    content = _with_hint(
                        f"没有名为{call.name}的工具。当前可用工具有：{available}"
                    )
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": content,
                    })
                    continue

                # 情况 B：参数那段 JSON 是坏的（少括号、字段名拼错）。
                # 同样转成文本回给模型，让它重写一次。
                if call.parse_error:
                    content = _with_hint(
                        f"工具 {call.name} 的参数解析失败：{call.parse_error}"
                    )
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": content,
                    })
                    continue

                # 情况 C：正常执行。
                try:
                    result = await tool.execute(**call.arguments)
                except asyncio.CancelledError:
                    # ★ 必须单独放行，而且要写在 except Exception 前面。
                    #
                    # 用户按 Ctrl+C 时，Python 会往正在执行的协程里扔这个异常。
                    # 要是被下面那个 except Exception 一把捞住，它就会被当成
                    # "工具执行失败"，转成文字塞回给模型，循环继续转——
                    # **用户明明按了取消，Agent 还在跑。**
                    #
                    # （补充：CancelledError 在 Python 3.8 之后继承的是
                    #  BaseException 而不是 Exception，本来就捞不到；
                    #  但显式写出来更保险，也更能说明意图。）
                    raise
                except Exception as exc:
                    # 兜底：工具自己没接住的异常。
                    # 这是最后一道保险，不是让工具偷懒的理由——
                    # 兜底产生的错误信息远不如工具自己写的具体。
                    result = ToolResult.error(
                        f"工具 {call.name} 执行时抛出异常：{type(exc).__name__}: {exc}"
                        + "\n\n" + TOOL_ERROR_HINT
                    )

                content = str(result)

                # 工具返回的是 ToolResult 且标了错误时，补上行动建议。
                # 用 getattr 而不是直接 result.is_error，是因为工具成功时
                # 可以直接返回普通 str，那种情况下没有这个属性。
                if getattr(result, "is_error", False):
                    content = _with_hint(content)

                messages.append({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": content,
                })
            # 循环体走完 → 自动进入下一圈，把工具结果带给模型
        else:
            # for 正常跑完（没 break）才会到这里 = 转满圈数还没结束
            return AgentRunResult(None, messages, stop_reason="max_iterations")

        return AgentRunResult(final_content=reply.content, messages=messages)
