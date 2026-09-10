from openai import AsyncOpenAI

import json
from .base import ModelReply, Provider, ToolCall

class OpenAICompatProvider(Provider):
    """OpenAI 兼容接口的实现。DeepSeek、Kimi、通义等都走这个。"""

    def __init__(self, api_key: str, base_url: str, model: str):
        self.model = model
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> ModelReply:
        kwargs = {"model": self.model, "messages": messages}
        if tools:                      # 空列表和 None 都不传，别给模型一个空 tools 字段
            kwargs["tools"] = tools

        resp = await self._client.chat.completions.create(**kwargs)
        msg = resp.choices[0].message

        # ── 一、原件：手工拼一份干净的 assistant 消息 ──
        # 不用 msg.model_dump()，那会带出 refusal 等一堆字段，回传时可能被 API 拒绝
        raw: dict = {"role": "assistant", "content": msg.content}

        calls: list[ToolCall] = []
        if msg.tool_calls:
            raw["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,   # 原样保留字符串
                    },
                }
                for tc in msg.tool_calls
            ]

            # ── 二、解析好的视图：把 arguments 字符串变成 dict ──
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                    err = None
                except json.JSONDecodeError as exc:
                    args, err = {}, f"参数不是合法 JSON：{exc}"
                calls.append(
                    ToolCall(id=tc.id, name=tc.function.name, arguments=args, parse_error=err)
                )

        return ModelReply(content=msg.content, tool_calls=calls, raw=raw)