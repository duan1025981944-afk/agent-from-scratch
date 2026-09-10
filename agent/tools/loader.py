"""工具自动发现：扫描 tools/ 目录，找出所有可用的 Tool 子类。"""
import importlib
import pkgutil

import agent.tools as tools_pkg
from agent.tools.base import Tool

# 这些模块里没有工具，跳过（省一次导入，也避免误扫）
_SKIP = {"base", "loader"}


def discover_tools() -> dict[str, Tool]:
    """扫描 agent/tools/ 下的所有模块，返回 {工具名: 工具实例}。"""
    found: dict[str, Tool] = {}

    # ① 列出 tools 包下的所有模块名
    for _finder, module_name, _ispkg in pkgutil.iter_modules(tools_pkg.__path__):
        if module_name.startswith("_") or module_name in _SKIP:
            continue

        # ② 运行时导入——模块名是变量，不是写死的
        module = importlib.import_module(f"agent.tools.{module_name}")

        # ③ 遍历模块命名空间，挑出 Tool 子类
        for attr_name in dir(module):
            attr = getattr(module, attr_name)

            if not isinstance(attr, type):
                continue                        # 不是类：常量、函数、模块
            if not issubclass(attr, Tool):
                continue                        # 不是工具
            if attr.__module__ != module.__name__:
                continue                        # 是 import 进来的（含 Tool 基类本身）
            if getattr(attr, "__abstractmethods__", None):
                continue                        # 抽象类，实例化会 TypeError

            tool = attr()

            # 类属性写法下 ABC 不再强制，这里补上检查（兑现待办）
            if not tool.name:
                raise ValueError(f"{attr.__name__} 没有设置 name")
            if not tool.description or not tool.parameters:
                raise ValueError(f"{tool.name} 缺少 description 或 parameters")
            if tool.name in found:
                raise ValueError(
                    f"工具名冲突：{tool.name} 同时被 "
                    f"{type(found[tool.name]).__name__} 和 {attr.__name__} 使用"
                )

            found[tool.name] = tool

    return dict(sorted(found.items()))          # 排序保证顺序稳定