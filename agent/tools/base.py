"""工具基类：定义所有工具的共同形状。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ToolResult(str):
    """工具的返回值。

    本质就是一个字符串（能直接塞进 messages），额外挂一个 is_error 标志
    供代码判断成功失败。模型看到的只有文本，看不到这个标志。
    """

    is_error: bool

    def __new__(cls, content: str, *, is_error: bool = False) -> "ToolResult":
        # str 是不可变类型，要在 __new__ 里构造，不能在 __init__ 里赋值
        obj = str.__new__(cls, content)
        obj.is_error = is_error
        return obj

    @classmethod
    def error(cls, content: str) -> "ToolResult":
        """构造一个错误结果。工具里统一用 ToolResult.error(...) 返回失败。"""
        return cls(content, is_error=True)


class Tool(ABC):
    """一个工具 = 给模型的说明书 + 给代码的实现。

    子类需要提供四样东西：
      name / description / parameters  → 说明书，会被翻译成 JSON 发给模型
      execute                          → 实现，真正干活的那段代码
    """

    # ── 说明书部分：子类用类属性覆盖 ──
    name: str = ""
    description: str = ""
    parameters: dict[str, Any] = {}

    # ── 实现部分：子类必须实现 ──
    @abstractmethod
    async def execute(self, **kwargs: Any) -> str:
        """执行工具。

        成功返回内容字符串；失败返回 ToolResult.error(...)，
        不要抛异常——错误要作为文本回给模型，让它自己想办法。
        """
        ...

    # ── 翻译层：写一次，所有工具共享 ──
    def to_schema(self) -> dict[str, Any]:
        """转成 OpenAI Function Calling 要求的格式。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }
    