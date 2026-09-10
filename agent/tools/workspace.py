"""工作区边界：所有文件类工具共用的路径解析与越界检查。"""
from pathlib import Path

# 启动时由 main.py 设置一次
_root: Path | None = None


class OutsideWorkspace(Exception):
    """路径越界。工具捕获它并转成 ToolResult.error。"""


def set_root(path: str) -> Path:
    """设置工作区根目录，返回解析后的绝对路径。"""
    global _root
    _root = Path(path).expanduser().resolve()
    if not _root.is_dir():
        raise ValueError(f"工作区根目录不存在或不是目录：{_root}")
    return _root


def get_root() -> Path:
    if _root is None:
        raise RuntimeError("工作区未初始化，请先调用 set_root()")
    return _root


def resolve(path: str) -> Path:
    """把工具收到的路径解析成绝对路径，并检查是否越界。

    越界抛 OutsideWorkspace；正常返回解析后的 Path。
    注意：不检查文件是否存在——那是工具自己的事。
    """
    root = get_root()
    p = (root / path).expanduser()      # 相对路径接在 root 后面；绝对路径会覆盖 root
    p = p.resolve()                     # ★ 展开 ..、转绝对、跟随符号链接

    if not p.is_relative_to(root):      # ★ 解析之后再判断
        raise OutsideWorkspace(
            f"路径 {path} 超出了工作区范围。"
            f"只能访问 {root} 及其子目录下的文件。"
        )
    return p