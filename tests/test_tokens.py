from agent.tokens import estimate_tokens, estimate_text_tokens


def test_same_count_different_size():
    """条数一样的两份 payload，尺子必须量出它们差很多。"""
    light = [
        {"role": "user", "content": "好"},
        {"role": "assistant", "content": "嗯"},
    ]
    heavy = [
        {"role": "user", "content": "好"},
        # 模拟 read_file 读回一个 4000 字符的文件
        {"role": "tool", "tool_call_id": "call_1", "content": "a" * 4000},
    ]
    assert len(light) == len(heavy)                               # 数条数：一样多
    assert estimate_tokens(heavy) > estimate_tokens(light) * 50   # 量 token：差几十倍


def test_tool_call_is_not_zero():
    """模型说"我要调工具"的那条消息 content 是空的，但不能被量成 0。"""
    msg = {
        "role": "assistant",
        "content": None,
        "tool_calls": [{"id": "call_1", "type": "function",
                        "function": {"name": "read_file",
                                     "arguments": "{\"path\": \"agent/runner.py\"}"}}],
    }
    only_overhead = 4
    assert estimate_tokens([msg]) > only_overhead


def test_symbols_cost_like_chinese():
    """─ → ✅ 这类符号不是 ASCII，要和中文按同一档算，不能按英文算。"""
    assert estimate_text_tokens("─" * 100) == estimate_text_tokens("中" * 100)