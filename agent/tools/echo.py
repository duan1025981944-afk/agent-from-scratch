"""echo 工具：把传入的文本原样返回。

──────────────────────────────────────────────────────────────
这个文件是什么
──────────────────────────────────────────────────────────────
一个玩具工具，也是**最小的工具范例**。

它存在的两个理由：

    1. 验证工具链路通不通 —— 从"模型决定调工具"到"结果回到模型手里"
       这条链路上有十几个环节，出问题时用 echo 试一下，能立刻分清
       是链路坏了还是某个具体工具坏了
    2. 当模板 —— 想写新工具时照着它抄最省事，四样东西一目了然

──────────────────────────────────────────────────────────────
一个工具需要的四样东西
──────────────────────────────────────────────────────────────
    name         工具名，模型调用时用的标识
    description  给模型看的说明
    parameters   参数定义（JSON Schema）
    execute      实现

前三样会被 to_schema() 打包成 JSON 随请求发给模型；execute 是真正干活的。

**没有任何注册步骤。** 把这个文件放进 agent/tools/ 目录，
agent/tools/loader.py 启动时自动扫到它。
"""
from agent.tools.base import Tool


class EchoTool(Tool):
    """把传入的文本原样返回，前面加个 "echo: "。

    谁会产生它
        agent/tools/loader.py 的 discover_tools()，启动时自动实例化。
        **不需要手动 new，也不需要在任何地方注册。**

    谁会调它
        模型。当它想确认工具链路是否正常时。实际使用中很少被调到——
        系统提示里没提它，模型一般不会主动用。

    类属性
        name         "echo"
        description  一句话说明 + 用途。这是四个工具里最简单的一份，
                     因为 echo 没有任何"用错"的可能，不需要写反例。
        parameters   一个必填的 text 参数。

    例子
        >>> tool = EchoTool()
        >>> tool.to_schema()["function"]["name"]
        'echo'
        >>> tool.to_schema()["function"]["parameters"]["required"]
        ['text']
    """

    name = "echo"
    description = "把传入的文本原样返回。用于测试工具链路是否连通。"
    parameters = {
        "type": "object",
        "properties": {"text": {"type": "string", "description": "要回显的文本"}},
        "required": ["text"],
    }

    async def execute(self, text: str) -> str:
        """原样返回传入的文本。

        传入什么
            text: 任意字符串。由模型提供。

                  注意参数名必须和 parameters 里声明的一致——runner 是用
                  tool.execute(**call.arguments) 调用的，名字对不上会报 TypeError。

        返回什么
            "echo: " + 原文。加前缀是为了让人一眼看出这是 echo 的输出，
            不是别的地方冒出来的文字。

        会失败吗
            不会。这是唯一一个不可能出错的工具——所以它才适合用来
            验证链路：**如果 echo 都不通，问题一定不在工具本身。**

        例子
            >>> import asyncio
            >>> asyncio.run(EchoTool().execute(text="你好"))
            'echo: 你好'
        """
        return f"echo: {text}"
