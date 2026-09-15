from dataclasses import dataclass, field
from providers.base import Provider
from agent.tools.base import Tool
from agent.tools.base import ToolResult
import asyncio
from agent.payload import CLEAR_TRIGGER, CONTEXT_BUDGET, PLACEHOLDER, build_payload, measure_payload


# 统一的行动建议，附在所有错误文本后面
TOOL_ERROR_HINT = "提示：检查参数是否正确，或换一种方式完成任务。不要用同样的参数重试。"


def _with_hint(text: str) -> str:
    """给错误文本统一附上行动建议。"""
    return f"{text}\n\n{TOOL_ERROR_HINT}"

@dataclass
class AgentRunSpec:
    """给 runner 的一张订单：要什么、用什么、最多转几圈。"""
    initial_messages: list[dict]
    provider: Provider
    max_iterations: int = 10
    tools: dict[str, Tool] = field(default_factory=dict)   # ← 新增：工具名 → 工具对象


@dataclass
class AgentRunResult:
    """runner 交回的结果。"""
    final_content: str | None
    messages: list[dict]
    stop_reason: str = "completed"          # ★ 为什么停下来的
    error: str | None = None


class AgentRunner:
    async def run(self, spec: AgentRunSpec) -> AgentRunResult:
        messages = list(spec.initial_messages)

        # 工具 schema 每轮都一样，循环外算一次就够
        schemas = [t.to_schema() for t in spec.tools.values()] or None

        for i in range(spec.max_iterations):
            payload = build_payload(messages)
            used, over = measure_payload(payload)
            omitted = sum(1 for m in payload if m.get("content") == PLACEHOLDER)
            note = f"，已省略 {omitted} 条旧工具结果" if omitted else ""
            flag = f"，超过预算 {CONTEXT_BUDGET:,}" if over else ""
            if not over and used > CLEAR_TRIGGER:
                flag = f"，已过清理阈值 {CLEAR_TRIGGER:,}"
            print(f"  [第 {i + 1} 圈] 上下文约 {used:,} token{note}{flag}")   # 临时观察

            reply = await spec.provider.chat(payload, tools=schemas)

            u = reply.usage                                             # 临时观察
            if u.prompt_tokens:
                gap = (used - u.prompt_tokens) / u.prompt_tokens * 100
                print(
                    f"         实际 {u.prompt_tokens:,}（尺子误差 {gap:+.0f}%）"
                    f"｜缓存命中 {u.cache_hit_tokens:,}，未命中 {u.cache_miss_tokens:,}"
                    f"，命中率 {u.hit_rate:.0%}"
                )

            # 无论要不要用工具，assistant 这条都必须先入链
            messages.append(reply.raw)

            # ── 唯一的分岔点 ──
            if not reply.wants_tools:
                break                                        # ← 原来这里是 return

            for call in reply.tool_calls:
                print(f"    → {call.name}({call.arguments})")

                tool = spec.tools.get(call.name)
                # 如果工具不存在，则提示用户当前可用工具，模型返回错误工具名后的应对策略
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
                
                # 如果工具参数解析失败，则提示用户参数解析失败，模型返回错误参数后的应对策略
                if call.parse_error:
                    content = _with_hint(f"工具 {call.name} 的参数解析失败：{call.parse_error}")
                    messages.append({
                        "role": "tool", 
                        "tool_call_id": call.id, 
                        "content": content})
                    continue

                try:
                    result = await tool.execute(**call.arguments)
                except asyncio.CancelledError:
                    raise                                    # ← 必须单独放行，且写在最前面
                except Exception as exc:
                    result = ToolResult.error(
                        f"工具 {call.name} 执行时抛出异常：{type(exc).__name__}: {exc}"
                        + "\n\n" + TOOL_ERROR_HINT
                    )
                
                content = str(result)

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