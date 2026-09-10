"""一个只用来验证自动发现的玩具工具。"""
from agent.tools.base import Tool


class EchoTool(Tool):
    name = "echo"
    description = "把传入的文本原样返回。用于测试工具链路是否连通。"
    parameters = {
        "type": "object",
        "properties": {"text": {"type": "string", "description": "要回显的文本"}},
        "required": ["text"],
    }

    async def execute(self, text: str) -> str:
        return f"echo: {text}"