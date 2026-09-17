"""jsonl 文件的公共读法：逐行产出字典，跳过读不懂的行。

──────────────────────────────────────────────────────────────
为什么会有这个文件
──────────────────────────────────────────────────────────────
"先写死再抽象，撞到第二次才抽" ——这里就是第二次。

    第 25 讲之前：只有 session/messages.py 要逐行读 jsonl
    第 25 讲之后：agent/memory.py 的流水账 history.jsonl 也要读，读法一模一样

两处都要处理同一件麻烦事：**追加写 + 断电，文件末尾会留下半行**。

    {"cursor": 7, "content": "用户的项目代号是 蓝     <- 写到一半断了

本来的写法是 json.loads(line)，一行坏掉就抛异常、整个文件读不出来。
几百条记录全没了，只因为最后半行。

──────────────────────────────────────────────────────────────
这个文件不是什么
──────────────────────────────────────────────────────────────
- 它**不**知道记录里有什么字段（role？cursor？都不关它的事）
- 它**不**负责写。写太简单了（open("a") 一行一个 json.dumps），
  而且各处写的内容不同，没什么可共用的
- 它**不**在乎文件是干什么用的——会话存档、流水账，一视同仁

──────────────────────────────────────────────────────────────
参照物
──────────────────────────────────────────────────────────────
nanobot 的 MemoryStore._read_entries 也是这个思路：json.JSONDecodeError
就 continue，解析出来不是字典也 continue。区别只是它每个 Store 各写一份，
我们抽成公共的。
"""
from __future__ import annotations

import json
import warnings
from pathlib import Path
from typing import Any, Iterator


def iter_records(path: Path) -> Iterator[dict[str, Any]]:
    """逐行读一个 jsonl 文件，**跳过读不懂的行**。

    谁会用它
        session/messages.py 的四个读取函数（load_messages / load_meta /
        load_summary / count_messages），以及 agent/memory.py 的流水账读取。

    传入什么
        path: jsonl 文件路径。**不存在就什么都不产出**，这不是错误——
              "还没有这个文件"是正常状态（新装的项目、还没记过东西）。

    产出什么
        一个个字典，按文件里的顺序。以下三种行会被跳过：

            空行                          分隔用的，本来就没内容
            解析不了的 JSON                写到一半断电留下的半行
            解析出来不是字典（比如 [1,2]）   形状不对，下游全是 obj["role"] 会炸

    警告
        一个文件里第一次遇到坏行时，用 warnings.warn 提醒一次，
        之后同一次调用里不再重复——坏行常常连着一片，刷屏没有意义。

        为什么用 warnings 不用 print：print 会混进 Agent 的对话输出里；
        warnings 走 stderr，而且测试里能用 pytest.warns 断言它真的报了。

    代价
        坏行里的内容确实丢了，救不回来。但"丢半句"远好过"丢全部"。

    拿到之后怎么用
        调用方自己判断这条记录是不是自己要的：

            for obj in iter_records(path):
                if "role" not in obj:      # 不是消息，跳过
                    continue
                messages.append(obj)

    例子（碰文件系统，所以不写成 doctest，见 tests/test_broken_lines.py）
        文件里 3 行，中间一行是半截 JSON  ->  产出 2 个字典 + 1 次警告
    """
    if not path.exists():
        return

    warned = False
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                obj = None
            if not isinstance(obj, dict):
                if not warned:
                    warned = True
                    warnings.warn(
                        f"{path.name} 里有读不懂的行，已跳过"
                        f"（通常是写到一半断电留下的）。后续坏行不再提示。",
                        stacklevel=2,
                    )
                continue
            yield obj