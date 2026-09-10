from __future__ import annotations
from abc import ABC, abstractmethod

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCall:
    """模型的一次工具调用意图（已经解析好，与厂商无关）。"""

    id: str                       # 调用编号，回传结果时要用它对上号
    name: str                     # 工具名
    arguments: dict[str, Any]     # 参数，已从 JSON 字符串解析成 dict
    parse_error: str | None = None  # 参数 JSON 解析失败时记在这，检查点 3 用


@dataclass
class ModelReply:
    """模型的一次回复。"""

    content: str | None                          # 文本内容，调工具时可能为 None
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)  # 原始 assistant 消息

    @property
    def wants_tools(self) -> bool:
        """模型这轮要不要用工具——runner 唯一的分岔判断。"""
        return bool(self.tool_calls)

class Provider(ABC):
    """所有模型接入的统一接口。

    主循环只认这个类，不认具体是 DeepSeek 还是别的。
    """

    @abstractmethod
    async def chat(
        self, 
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> ModelReply:
        """调用模型。

            tools 是一组 to_schema() 的输出；不传表示这轮不给工具。
        """