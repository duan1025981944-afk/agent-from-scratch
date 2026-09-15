from openai import AsyncOpenAI

import json
from .base import ModelReply, Provider, ToolCall, Usage

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

        return ModelReply(
            content=msg.content, tool_calls=calls, raw=raw, usage=_read_usage(resp)
        )

def _read_usage(resp) -> Usage:
    """把厂商各自的用量字段翻译成统一的 Usage。
 
    缓存命中的字段有两种写法，都要认：
      DeepSeek：usage.prompt_cache_hit_tokens
      OpenAI ：usage.prompt_tokens_details.cached_tokens
    取不到就当 0——没有用量数据不该让整轮对话失败。
    """
    u = getattr(resp, "usage", None)
    if u is None:
        return Usage()
 
    prompt = getattr(u, "prompt_tokens", 0) or 0
 
    hit = getattr(u, "prompt_cache_hit_tokens", None)
    if hit is None:
        details = getattr(u, "prompt_tokens_details", None)
        hit = getattr(details, "cached_tokens", 0) if details else 0
    hit = hit or 0
 
    miss = getattr(u, "prompt_cache_miss_tokens", None)
    if miss is None:
        miss = max(0, prompt - hit)          # 没给未命中字段就自己减出来
 
    return Usage(
        prompt_tokens=prompt,
        completion_tokens=getattr(u, "completion_tokens", 0) or 0,
        cache_hit_tokens=hit,
        cache_miss_tokens=miss,
    )