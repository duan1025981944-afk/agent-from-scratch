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
- 它**不**关心记录里有什么字段，写的时候也不检查
- 它**不**在乎文件是干什么用的——会话存档、流水账，一视同仁

──────────────────────────────────────────────────────────────
为什么写也放进来了
──────────────────────────────────────────────────────────────
原本只有读。写看起来太简单（open("a") 一行一个 json.dumps），不值得共用。

后来情景模拟撞出一件事：上一次写到一半断电，文件末尾留下的半行**没有换行符**，
下一次追加就直接焊在它后面：

    {"role": "user", "content": "写到一半就断电{"role": "user", "content": "在吗"}

两条粘成一行，下次读的时候被一起当成坏行扔掉。**受损范围从"丢半行"变成了
"丢半行，外加断电后的第一条新记录"。**

这个检查每个写入点都得做，而写入点有四个（会话存档三个、流水账一个）。
到这一步，"写"就值得共用了——这是同一条老规矩：撞到第二次才抽。

──────────────────────────────────────────────────────────────
参照物
──────────────────────────────────────────────────────────────
读：nanobot 的 MemoryStore._read_entries 也是这个思路：json.JSONDecodeError
就 continue，解析出来不是字典也 continue。区别只是它每个 Store 各写一份，
我们抽成公共的。

写：nanobot 的 _append_history_record 是直接 open("a") 写一行，**没有**
"先补换行"这一步——它的读法能跳过坏行，但挡不住坏行把下一条也拖下水。
append_record() 的第 ② 条保证，是我们在它的基础上多做的一步。
"""
from __future__ import annotations

import json
import warnings
from pathlib import Path
from typing import Any, Iterator


def append_record(path: Path, record: dict[str, Any]) -> None:
    """往 jsonl 末尾追加一条记录，**追加前先保证上一行是完整的一行**。

    谁会用它
        agent/memory.py 的 append_fact，以及 session/messages.py 的几个追加函数。

    传入什么
        path:   jsonl 文件。目录不存在会自动建。
        record: 一个字典，会被写成一行 JSON。

    它保证什么
        ① 只追加，永不改写——这个函数里只有 open("a")
        ② 写之前先看文件最后一个字节，不是换行就先补一个

        第 ② 条是给"上次写到一半断电"兜底的。半行本身救不回来（内容就是缺了），
        但至少不能让它再连累下一条。

    为什么要用二进制方式去读最后一个字节
        文本模式下 seek 到末尾往回退一个字符是不可靠的（多字节字符会被切开）。
        二进制方式读一个字节，只判断它是不是 b"\n"，不解码，不会出问题。

    例子（碰文件系统，见 tests/test_broken_lines.py）
        文件末尾是完整的一行      ->  直接追加
        文件末尾是断电留下的半行   ->  先补一个换行，再追加
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    unterminated = False
    if path.exists() and path.stat().st_size:
        with path.open("rb") as f:
            f.seek(-1, 2)                     # 2 = 从文件末尾开始算
            unterminated = f.read(1) != b"\n"

    with path.open("a", encoding="utf-8") as f:      # ★ 只追加
        if unterminated:
            f.write("\n")
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


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