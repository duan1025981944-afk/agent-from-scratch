"""build_payload 周边的测试。

第 12 讲：预算判断（measure_payload）
第 13 讲：工具结果换成占位说明
第 20 讲：两个阈值、keep、clear_at_least
"""
import copy

from agent.payload import PLACEHOLDER, build_payload, measure_payload
from agent.tokens import estimate_tokens

SYSTEM = {"role": "system", "content": "你是助手"}

# 小样本测试用的参数：真实默认值（20,000 / 3）对这些几百 token 的样本太大
SMALL = dict(clear_at_least=1, keep=1)


def turn(n: int, content: str) -> list[dict]:
    """造一轮对话：用户提问 → 模型调 read_file → 工具返回 content。"""
    cid = f"call_{n}"
    return [
        {"role": "user", "content": f"第 {n} 个问题"},
        {"role": "assistant", "content": None, "tool_calls": [{
            "id": cid, "type": "function",
            "function": {"name": "read_file", "arguments": "{}"},
        }]},
        {"role": "tool", "tool_call_id": cid, "content": content},
    ]


def round_with_tool(n: int, content: str) -> list[dict]:
    """同一轮里的一圈：模型要求调工具 + 工具返回。没有新的 user 消息。"""
    cid = f"call_{n}"
    return [
        {"role": "assistant", "content": None, "tool_calls": [{
            "id": cid, "type": "function",
            "function": {"name": "read_file", "arguments": "{}"},
        }]},
        {"role": "tool", "tool_call_id": cid, "content": content},
    ]


def tool_contents(payload: list[dict]) -> list[str]:
    return [m["content"] for m in payload if m["role"] == "tool"]


# ─────────────────────────── 第 12 讲：预算判断 ───────────────────────────

def test_small_payload_is_within_budget():
    _, over = measure_payload([{"role": "user", "content": "好"}], budget=100)
    assert not over


def test_big_payload_is_over_budget():
    payload = [{"role": "tool", "tool_call_id": "call_1", "content": "a" * 4000}]
    used, over = measure_payload(payload, budget=100)
    assert used > 100 and over


# ─────────────────────── 第 13 讲：旧工具结果替换 ───────────────────────

def test_oldest_tool_result_is_replaced_first():
    """从最旧的开始换。"""
    messages = [SYSTEM] + turn(1, "a" * 3000) + turn(2, "b" * 3000) + turn(3, "c" * 3000)
    total = estimate_tokens(build_payload(messages))

    payload = build_payload(messages, clear_trigger=total - 500, **SMALL)
    assert tool_contents(payload)[0] == PLACEHOLDER
    assert tool_contents(payload)[-1] == "c" * 3000          # 最新的没动


def test_archive_and_structure_untouched():
    """不变量 1：存档不动；不变量 3：条数、顺序、tool_call_id 配对都不变。"""
    messages = [SYSTEM] + turn(1, "a" * 3000) + turn(2, "b" * 3000) + turn(3, "c" * 3000)
    snapshot = copy.deepcopy(messages)

    payload = build_payload(messages, clear_trigger=100, **SMALL)

    assert messages == snapshot
    assert [m["role"] for m in payload] == [m["role"] for m in messages]
    assert [m.get("tool_call_id") for m in payload] == [m.get("tool_call_id") for m in messages]


def test_replaced_result_stays_replaced_when_history_grows():
    """历史变长以后，之前换掉的不会被换回来。"""
    before = [SYSTEM] + turn(1, "a" * 3000) + turn(2, "b" * 2000) + turn(3, "d" * 4000)
    trigger = estimate_tokens(build_payload(before)) - 500
    assert tool_contents(build_payload(before, clear_trigger=trigger, **SMALL))[0] == PLACEHOLDER

    after = before + turn(4, "ok")
    assert tool_contents(build_payload(after, clear_trigger=trigger, **SMALL))[0] == PLACEHOLDER


# ─────────────── 第 20 讲：keep / 同一轮多圈 / clear_at_least ───────────────

def test_earlier_rounds_in_the_same_turn_can_be_replaced():
    """一轮里连转多圈时，早几圈的工具结果也要能换掉。

    旧规则护住"当前这一轮"，于是一条都换不掉——长任务里最容易撑爆的就是这种。
    """
    messages = [SYSTEM, {"role": "user", "content": "读一堆文件"}]
    for i in range(6):
        messages += round_with_tool(i, "x" * 3000)

    payload = build_payload(messages, clear_trigger=500, clear_at_least=1, keep=3)
    assert tool_contents(payload).count(PLACEHOLDER) == 3      # 保留最近 3 条


def test_the_freshest_batch_is_never_replaced():
    """模型刚拿到的那一批，就算超阈值也不能换——哪怕这批比 keep 还多。"""
    messages = [SYSTEM, {"role": "user", "content": "读一堆文件"}]
    for i in range(3):
        messages += round_with_tool(i, "x" * 3000)
    # 最后一圈并行调 5 个工具
    calls = [{"id": f"p{j}", "type": "function",
              "function": {"name": "read_file", "arguments": "{}"}} for j in range(5)]
    messages.append({"role": "assistant", "content": None, "tool_calls": calls})
    messages += [{"role": "tool", "tool_call_id": f"p{j}", "content": "y" * 3000} for j in range(5)]

    payload = build_payload(messages, clear_trigger=10, clear_at_least=1, keep=3)
    assert tool_contents(payload)[-5:] == ["y" * 3000] * 5     # 这一批一条都没动


def test_clearing_is_skipped_when_it_would_not_save_enough():
    """清不够 clear_at_least 就一条都不清——不值得为一点点收益打断缓存。"""
    messages = [SYSTEM] + turn(1, "a" * 3000) + turn(2, "b" * 3000) + turn(3, "c" * 3000)
    payload = build_payload(messages, clear_trigger=100, clear_at_least=1_000_000, keep=1)
    assert PLACEHOLDER not in tool_contents(payload)