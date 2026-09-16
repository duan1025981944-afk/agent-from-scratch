"""尺子：估算一份消息发给模型时，大约会占掉多少 token。

──────────────────────────────────────────────────────────────
这个文件是什么
──────────────────────────────────────────────────────────────
一把秤。给它一段文字或一堆消息，它告诉你"大概多少 token"。

**token 是什么**：模型不是按"字"收费，是按 token。一个 token 大致相当于
半个汉字到一个英文单词。模型的上下文窗口、API 账单，用的都是这个单位。

**为什么要自己估**：真实的 token 数只有模型回复之后才知道（在 usage 里）。
但"这次要不要压缩上下文"必须在**发送之前**决定，那时候还没有真实值，
所以只能估。

**为什么不装分词器**：装一个能精确数 token 的库当然更准，但
（1）不同厂商的分词器不一样，换模型就白装；
（2）这里的用途是"超没超预算"，差个百分之十几不影响判断。
所以用最土的办法：数字符，乘一个系数。

──────────────────────────────────────────────────────────────
这个文件不是什么
──────────────────────────────────────────────────────────────
- 它**不**负责决定"超了怎么办"——那是 agent/payload.py 和 agent/compaction.py 的事
- 它**不**知道真实的 token 数——真实值见 providers/base.py 里的 Usage
- 它**不**准。误差实测在 ±25% 以内，够用来判断"要不要动手"，
  不够用来精确卡在窗口上限边缘

──────────────────────────────────────────────────────────────
系数是怎么来的
──────────────────────────────────────────────────────────────
拿 DeepSeek 的离线分词器，把两个真实文件（一份中文 Markdown、一份 Python 代码）
按字符类别拆开，逐类数了一遍：

    中文字符        实测 0.59 ~ 0.60 token/字符   ->  取 0.6
    箭头方框等符号  实测 0.58 ~ 0.73             ->  取 0.6（和中文同档）
    ASCII（英文、
    数字、空格、
    代码符号）      文档里 0.32，代码里 0.21      ->  取 0.3（一个系数盖不住两种，取中偏高）

**为什么按"是不是 ASCII"分类，而不是按"是不是中文"**：
ASCII 字符在 UTF-8 里占 1 个字节，分词器很容易把连续几个合并成一个 token
（8 个缩进空格 = 1 个 token）。中文和箭头方框这类符号都占 2~4 个字节，
合并得少，实测都在 0.6 附近。所以分界线是"字节多不多"，不是"是不是汉字"。
"""
from __future__ import annotations

import json
from typing import Any

# ── 两个系数 ──
# 出处见文件顶部说明。要调的话，先跑 scripts/check_tokens.py 看当前误差，
# 再决定调哪一个——不要凭感觉改。
NON_ASCII_RATIO = 0.6
ASCII_RATIO = 0.3

# 每条消息除了正文，还有 role 标记、分隔符之类的"包装"，按每条 4 个 token 算。
# 这个数值借鉴 nanobot 的 per_message_overhead。
PER_MESSAGE_OVERHEAD = 4


def estimate_text_tokens(text: str) -> int:
    """估算一段纯文字大约占多少 token。

    谁会用它
        - estimate_message_tokens()（本文件）——量单条消息时先量它的文字
        - scripts/check_tokens.py——拿它的结果和模型返回的真实值对账

    传入什么
        text: 任意字符串。中文、英文、代码、emoji 都行，空字符串也行。

    返回什么
        一个整数，估算出来的 token 数。**不含**每条消息 4 个的包装开销——
        那是 estimate_message_tokens() 才加的。

    拿到之后怎么用
        直接和阈值比大小，比如 if estimate_text_tokens(t) > 1000:。
        不要拿它当精确值去做减法算余量，误差会放大。

    例子
        中文大约两个字一个 token：

        >>> estimate_text_tokens("你好")
        1
        >>> estimate_text_tokens("你好，世界")
        3

        英文和代码便宜得多：

        >>> estimate_text_tokens("hello")
        2
        >>> estimate_text_tokens("def main():")
        3

        空字符串是 0，不会报错：

        >>> estimate_text_tokens("")
        0
    """
    non_ascii = sum(1 for ch in text if not ch.isascii())
    ascii_count = len(text) - non_ascii
    return round(non_ascii * NON_ASCII_RATIO + ascii_count * ASCII_RATIO)


def _message_text(msg: dict[str, Any]) -> str:
    """把一条消息里**所有会被发出去的文字**拼成一个字符串。

    这是个内部函数（名字带下划线 = 别的文件不该直接调它）。

    传入什么
        msg: 一条消息字典，就是会放进 messages 列表里的那种。
             常见的三种形状：

                 {"role": "user", "content": "帮我读一下 main.py"}

                 {"role": "assistant", "content": None,
                  "tool_calls": [{"id": "call_1", "type": "function",
                                  "function": {"name": "read_file",
                                               "arguments": '{"path": "main.py"}'}}]}

                 {"role": "tool", "tool_call_id": "call_1", "content": "文件内容……"}

    返回什么
        拼接好的字符串。

    为什么不能只看 content
        看上面第二种形状：模型说"我要调工具"时，content 往往是 None，
        真正的内容全在 tool_calls 里那段 JSON。只数 content 的话，
        这条消息会被算成 0 token，而它实际可能有好几十。
    """
    parts = []
    if isinstance(msg.get("content"), str):
        parts.append(msg["content"])
    if msg.get("tool_calls"):
        # ensure_ascii=False：中文按原样算长度，不要变成 \uXXXX（那样会虚高三倍）
        parts.append(json.dumps(msg["tool_calls"], ensure_ascii=False))
    if isinstance(msg.get("tool_call_id"), str):
        parts.append(msg["tool_call_id"])
    return "".join(parts)


def estimate_message_tokens(msg: dict[str, Any]) -> int:
    """估算**单条**消息占多少 token（含每条 4 个的包装开销）。

    谁会用它
        agent/payload.py 清理旧工具结果时，要知道"换掉这一条能省多少"，
        所以必须能单独量一条。

    传入什么
        msg: 一条消息字典。形状见 _message_text 的说明。

    返回什么
        一个整数。**最小值是 4**（包装开销），哪怕消息内容是空的。

    拿到之后怎么用
        典型用法是算差值——"把这条换成占位说明能省多少 token"：

            saved = estimate_message_tokens(原消息) - estimate_message_tokens(占位版)

    例子
        一条普通的用户消息：

        >>> estimate_message_tokens({"role": "user", "content": "你好"})
        5

        模型要求调工具的那条，content 是空的，但一样要花钱：

        >>> msg = {"role": "assistant", "content": None,
        ...        "tool_calls": [{"id": "c1", "type": "function",
        ...                        "function": {"name": "read_file",
        ...                                     "arguments": '{"path": "a.py"}'}}]}
        >>> estimate_message_tokens(msg)
        36
    """
    return estimate_text_tokens(_message_text(msg)) + PER_MESSAGE_OVERHEAD


def estimate_tokens(messages: list[dict[str, Any]]) -> int:
    """估算**一整份**消息列表占多少 token。

    谁会用它
        - agent/payload.py 的 measure_payload()——判断超没超预算
        - agent/compaction.py——判断该不该压缩、切点该切在哪
        - agent/runner.py——每圈打印"上下文约多少 token"

    传入什么
        messages: 消息字典组成的列表。可以是内存里的 messages，
                  也可以是 build_payload() 加工过的 payload。
                  空列表也行，返回 0。

        量哪一份很重要：内存里那份是全文，payload 那份是截断、清理之后的。
        要判断"这次发出去要花多少钱"，**必须量 payload**，不能量内存那份。
        两者可能差好几倍。

    返回什么
        一个整数，所有消息的估算之和。

    漏掉了什么（重要）
        它只数 messages。一次真实请求里还有两样东西要花 token，它数不到：

            工具定义（tools 参数）   4 个工具约 603 token
            对话模板（角色标记等）   约 190 token

        合计约 790，是个**固定值**。所以消息很少时误差看起来很吓人
        （比如尺子报 104、实际 898），消息一多就收敛到 ±25% 以内。
        以 190,000 的预算论，这 790 只占 0.4%，可以忽略；
        等以后接入 MCP、工具变多，就必须把工具定义计入。

    例子
        >>> estimate_tokens([{"role": "user", "content": "你好"},
        ...                  {"role": "assistant", "content": "你好呀"}])
        11

        >>> estimate_tokens([])
        0
    """
    return sum(estimate_message_tokens(m) for m in messages)
