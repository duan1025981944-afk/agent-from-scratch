"""load_mcp_servers 的测试。对应第 40 讲：MCP server 写进 config.json。

每个测试的 docstring 末尾写了"负对照"：把代码改成那样，这个测试必须红。
"""
import json
import sys

import pytest

from config.loader import load_mcp_servers


def write_config(tmp_path, raw: dict) -> str:
    """在临时目录里写一份 config.json，返回它的路径。"""
    path = tmp_path / "config.json"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    return str(path)


def test_reads_servers_and_replaces_python(tmp_path):
    """一项变成一个 MCPServerConfig；command 写 "python" 的换成 sys.executable。

    负对照：把 load_mcp_servers 里换 sys.executable 那一行删掉 → command 还是 "python"，本测试红。
    """
    path = write_config(tmp_path, {"tools": {"mcpServers": {
        "xingchen_db": {"command": "python", "args": ["mcp_servers/xingchen_db/server.py"]},
    }}})
    [server] = load_mcp_servers(path)
    assert server.name == "xingchen_db"
    assert server.command == sys.executable
    assert server.args == ["mcp_servers/xingchen_db/server.py"]
    assert server.env == {}


def test_full_path_command_is_kept(tmp_path):
    """写了完整路径的 command 原样保留：想让某个 server 用别的 Python，就写完整路径。

    负对照：把判断改成"只要 command 里有 python 就换掉" → 本测试红。
    """
    path = write_config(tmp_path, {"tools": {"mcpServers": {
        "other": {"command": "C:/other/python.exe", "args": ["s.py"]},
    }}})
    [server] = load_mcp_servers(path)
    assert server.command == "C:/other/python.exe"


def test_no_section_means_no_servers(tmp_path):
    """没写 tools.mcpServers：返回空列表，不接 MCP 也能跑。

    负对照：把 raw.get("tools", {}) 改成 raw["tools"] → KeyError，本测试红。
    """
    path = write_config(tmp_path, {"agents": {}})
    assert load_mcp_servers(path) == []


def test_missing_command_names_the_server(tmp_path):
    """某一项没写 command：当场报错，而且报错里有 server 的名字和正确写法。

    负对照：把 ValueError 那段删掉 → 变成 KeyError: 'command'，看不出是哪个 server，本测试红。
    """
    path = write_config(tmp_path, {"tools": {"mcpServers": {"broken": {"args": []}}}})
    with pytest.raises(ValueError, match="broken"):
        load_mcp_servers(path)
