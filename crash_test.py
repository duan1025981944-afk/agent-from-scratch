"""检查点 4：故意撞三次崩溃，看清楚防御该加在哪。"""
import asyncio
import json
from pathlib import Path

from providers.base import ModelReply, Provider, ToolCall
from agent.runner import AgentRunner, AgentRunSpec
from agent.tools.read_file import ReadFileTool


class FakeProvider(Provider):
    """不联网的假 provider：按预设剧本逐条返回，不花一分钱 token。"""

    def __init__(self, script: list[ModelReply]):
        self._script = list(script)
        self.received: list[list[dict]] = []   # 记下每次收到的 messages，事后检查用

    async def chat(self, messages: list[dict], tools: list[dict] | None = None) -> ModelReply:
        self.received.append(list(messages))   # 存一份快照
        if not self._script:
            # 剧本演完了，返回一句话让循环 break
            return ModelReply(content="（剧本结束）",
                              raw={"role": "assistant", "content": "（剧本结束）"})
        return self._script.pop(0)


def tool_reply(name: str, arguments_json: str, call_id: str = "call_fake_1") -> ModelReply:
    """造一条"模型要调工具"的回复。

    参数是 arguments_json 字符串而不是 dict，因为要原样复刻
    openai_compat 里那段解析逻辑——包括它解析失败时的行为。
    """
    try:
        args, err = json.loads(arguments_json or "{}"), None
    except json.JSONDecodeError as exc:
        args, err = {}, f"参数不是合法 JSON：{exc}"

    raw = {
        "role": "assistant",
        "content": "",
        "tool_calls": [{
            "id": call_id,
            "type": "function",
            "function": {"name": name, "arguments": arguments_json},
        }],
    }
    return ModelReply(content="", tool_calls=[ToolCall(call_id, name, args, err)], raw=raw)


def text_reply(text: str) -> ModelReply:
    """造一条"模型直接给答案"的回复，用来让循环 break。"""
    return ModelReply(content=text, raw={"role": "assistant", "content": text})

TOOLS = {t.name: t for t in [ReadFileTool()]}


async def run_case(title: str, script: list[ModelReply]):
    print("=" * 60)
    print(title)
    print("=" * 60)

    provider = FakeProvider(script)
    spec = AgentRunSpec(
        provider=provider,
        initial_messages=[{"role": "user", "content": "（测试）"}],
        tools=TOOLS,
    )

    try:
        result = await AgentRunner().run(spec)
    except Exception as exc:
        print(f"❌ 崩了：{type(exc).__name__}: {exc}")
        return None

    print("✅ 没崩。final_content =", repr(result.final_content))

    # 把每条 tool 消息的长度、开头、结尾都打出来
    for m in result.messages:
        if m.get("role") != "tool":
            continue
        body = m["content"]
        head = body[:100].replace("\n", " ↵ ")
        print(f"   📨 tool 消息（{len(body):,} 字符）")
        print(f"      开头：{head}")
        if len(body) > 200:
            tail = body[-160:].replace("\n", " ↵ ")
            print(f"      结尾：…{tail}")

    return result


async def main():
    # ── 实验 A：模型编了一个不存在的工具名 ──
    await run_case(
        "A. 未知工具名",
        [tool_reply("delete_everything", '{"path": "."}'), text_reply("done")],
    )
    print()

    # ── 实验 B：参数 JSON 少了一个右括号 ──
    await run_case(
        "B. 参数 JSON 损坏",
        [tool_reply("read_file", '{"path": ".gitignore"'), text_reply("done")],
    )
    print()

    # ── 实验 C：读一个 20 万字符的文件 ──
    Path("big.txt").write_text("星辰科技" * 50000, encoding="utf-8")
    result = await run_case(
        "C. 超大文件",
        [tool_reply("read_file", '{"path": "big.txt"}'), text_reply("done")],
    )
    


asyncio.run(main())