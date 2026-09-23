"""Skills：把 skills/ 目录读成一份"名字 + 描述"的清单数据。

──────────────────────────────────────────────────────────────
这个文件是什么
──────────────────────────────────────────────────────────────
一个**目录读取器**。它把磁盘上的

    skills/returns-policy/SKILL.md

读成程序能用的数据（名字、描述、文件路径），交给上层去拼系统提示。

一个技能 = 一个目录 + 里面一个 SKILL.md。SKILL.md 分两段：

    ---                              <- frontmatter，给**程序**看
    name: returns-policy                这一段决定它叫什么、清单上怎么介绍
    description: 星辰科技退换货流程……
    ---

    # 退换货流程                      <- 正文，给**模型**看
    ……                                 模型决定用这个技能时才读

**本文件只读 frontmatter，不读正文。** 正文是按需加载的，那是另一件事。

──────────────────────────────────────────────────────────────
这个文件不是什么
──────────────────────────────────────────────────────────────
- 它**不**知道技能目录在哪（路径由调用方传进来）
- 它**不**拼系统提示，也不知道清单长什么样
- 它**不**读技能正文，更不把正文塞给模型

──────────────────────────────────────────────────────────────
坏掉的技能怎么办：跳过，但要出声
──────────────────────────────────────────────────────────────
`templates/` 缺文件直接炸，因为模板是**必需品**，缺了 Agent 没法工作。
技能是**可选扩展**，坏一个不该让整个程序起不来 —— 所以跳过它、其余照常，
这和 storage/jsonl.py 处理坏行是同一个待遇：跳过、警告一次、不影响别的。

**但"悄悄跳过"最糟**：你以为技能装好了，模型却永远找不到它。
所以每跳过一个，打印一行说清是哪个目录、为什么。
"""
from __future__ import annotations

import re
import warnings
from dataclasses import dataclass
from pathlib import Path

import yaml

# frontmatter 的形状：开头一行 ---，中间是 YAML（第 1 组），再一行 --- 收尾。
#
# 这个正则直接学 nanobot 的 _STRIP_SKILL_FRONTMATTER，它的注释里特意写了
# "supports CRLF" —— \r?\n 就是为 Windows 准备的，少了它在 Windows 上一个都匹配不上。
_FRONT_MATTER = re.compile(r"^---\s*\r?\n(.*?)\r?\n---\s*\r?\n?", re.DOTALL)

@dataclass(frozen=True)
class Skill:
    """一个技能的元数据。**不含正文。**

    谁会产生它
        本文件的 load_skill()。

    三个字段
        name         技能名，**等于目录名**（frontmatter 里的 name 只用来校验）
        description  frontmatter 里那句话，清单上就显示它
        path         SKILL.md 的路径，第 2 讲模型要按它去读正文

    为什么 frozen=True
        清单数据是只读的：拼完系统提示就不该再改。frozen 让手滑改它直接报错，
        顺带还能放进 set / 当字典的键。

    为什么不存正文
        正文是按需加载的那一层。存进来就等于每次启动都把所有技能全文读进内存，
        渐进式加载的意义就没了。
    """

    name: str
    description: str
    path: Path

def parse_front_matter(text: str) -> dict | None:
    """切出 SKILL.md 开头的 frontmatter，解析成字典。

    谁会用它
        本文件的 load_skill()。

    传入什么
        text: SKILL.md 的全文。

    返回什么
        解析出来的字典，形如 {"name": "returns-policy", "description": "……"}。
        **没有 frontmatter、YAML 坏了、或者解析出来不是字典，一律返回 None。**

        为什么三种情况共用一个 None：对调用方来说它们是同一件事 ——
        "这个文件没给出能用的元数据"。至于是哪一种，由 load_skill() 报给用户。

    会抛什么
        什么都不抛。坏文件是常态，不是异常。

    正文里也有 --- 怎么办（Markdown 的分隔线）
        伤不到。靠两件事：

            ^---  + match()   只从文件第 0 个字符试，正文里的轮不上
            (.*?)             非贪婪，停在**第一个**收尾 ---

    和 nanobot 的差异
        nanobot 会把键统一转成 str（YAML 里 `on:` 这种键会被解析成布尔 True）。
        我们只用 .get("name") 和 .get("description") 两个固定的键，
        非字符串的键拿不到也不影响，所以不做这层转换 —— 先写死，撞到再说。

    例子
        >>> parse_front_matter("---\\nname: a\\ndescription: b\\n---\\n\\n正文")
        {'name': 'a', 'description': 'b'}

        三种"没有能用的元数据"，返回的都是 None：

        >>> parse_front_matter("") is None
        True
        >>> parse_front_matter("直接就是正文\\n\\n---\\n\\n后面的分隔线") is None
        True
        >>> parse_front_matter("---\\n就一句话，不是键值对\\n---\\n") is None
        True
    """
    matched = _FRONT_MATTER.match(text)
    if not matched:
        return None

    try:
        data = yaml.safe_load(matched.group(1))
    except yaml.YAMLError:
        return None

    return data if isinstance(data, dict) else None

def _skip(skill_dir: Path, reason: str) -> None:
    """报告跳过了哪个技能、为什么，然后返回 None。

    只给本文件的 load_skill() 用。它每次跳过都要"出声 + 返回 None"两件事，
    写成一个函数，调用处就能一行写完：return _skip(...)。

    为什么用 warnings 不用 print
        和 storage/jsonl.py 处理坏行同一个理由：print 会混进 Agent 的对话输出里；
        warnings 走 stderr，而且测试里能用 pytest.warns 断言它真的报了。

    stacklevel=3 是因为中间隔了两层
        用户想知道的是"谁调的 load_skill"，不是"_skip 这一行"。
        1 = _skip 自己，2 = load_skill，3 = 真正的调用方。
    """
    warnings.warn(f"跳过技能 {skill_dir.name}/：{reason}", stacklevel=3)
    return None


def load_skill(skill_dir: Path) -> Skill | None:
    """读一个技能目录，校验通过返回 Skill，否则返回 None。

    谁会用它
        本文件的 discover_skills()：每个子目录都问它一次。

    传入什么
        skill_dir: 一个技能目录，比如 skills/returns-policy。

    返回什么
        Skill —— 五道检查全过。
        None —— 任何一道没过。

    两种 None，区别很重要
        **静默的 None**：目录里根本没有 SKILL.md。它没打算当技能 ——
        skills/ 下面还会有 README.md、.git、__pycache__ 这些东西，
        给它们每个都警告一次，警告就成了噪音，真正的问题反而被淹没。

        **带警告的 None**：有 SKILL.md 但写坏了。这是"想当技能却失败了"，
        必须出声 —— 悄悄跳过最糟，你以为装好了，模型却永远找不到它。

    五道检查（顺序不能换，每道都假设前一道过了）
        1. SKILL.md 在不在        不在 -> 静默 None
        2. 读得出来吗             读不出 -> 警告（Windows 上常见：文件存成了 GBK）
        3. 有没有 frontmatter     没有 -> 警告
        4. name 对不对得上目录名   对不上 -> 警告
        5. description 是不是空的  空 -> 警告

    为什么名字以目录名为准
        目录名就是路径的一部分，模型要靠它拼出路径去读正文。
        用 frontmatter 的 name 的话，会出现"清单上叫 A、文件在目录 B 里"，
        得多维护一张映射表。Agent Skills 规范本来也要求两者一致，
        所以 frontmatter 的 name 在这里只有一个用处：**对不上就说明写错了**。

    例子（碰文件系统，不写成 doctest，见 tests/test_skills.py）
        skills/returns-policy/  ->  Skill(name="returns-policy", ...)
        skills/broken/          ->  None + 一条警告
    """
    md = skill_dir / "SKILL.md"
    if not md.is_file():
        return None                                  # 静默：它没打算当技能

    try:
        text = md.read_text(encoding="utf-8-sig")     # sig：吃掉记事本可能留下的 BOM
    except (OSError, UnicodeDecodeError) as exc:
        return _skip(skill_dir, f"SKILL.md 读不出来（{type(exc).__name__}），可能不是 UTF-8")

    meta = parse_front_matter(text)
    if meta is None:
        return _skip(skill_dir, "开头没有 --- 包起来的 frontmatter，或者里面的 YAML 写坏了")

    name = meta.get("name")
    if name != skill_dir.name:
        return _skip(skill_dir, f"frontmatter 里 name 是 {name!r}，和目录名对不上")

    description = meta.get("description")
    if not isinstance(description, str) or not description.strip():
        return _skip(skill_dir, "description 缺失或为空，模型全靠它决定要不要用这个技能")

    return Skill(name=name, description=description.strip(), path=md)

def discover_skills(root: Path) -> list[Skill]:
    """扫 root 下一层子目录，返回按名字排好序的技能列表。

    谁会用它
        第 3 讲的 context.py（拼清单时），以及测试。

    传入什么
        root: 技能目录，比如 skills/。
              **由调用方传进来，不写死** —— 技能目录锚在 cwd 还是锚在代码位置，
              是第 2 讲要拍的板。这一讲不碰，测试也就能用 tmp_path 随便造。

    返回什么
        list[Skill]，**按名字排序**。坏技能不在里面（跳过时各自警告过了）。

    为什么必须排序
        和 loader.py 的 discover_tools 同一个理由：发给模型的内容顺序稳定，
        才能命中提示缓存。iterdir() 给的顺序由文件系统决定，不保证有序，
        换台机器、删个文件都可能变 —— 顺序一变，清单那一段之后的缓存全作废。

        nanobot 这里没有排序（它直接用 iterdir 的顺序），这是我们和它的一处差异。

    root 不存在怎么办
        返回空列表，不报错。没写过任何技能是常态 ——
        和 MEMORY.md 缺文件同一个待遇，对比 templates/ 缺文件是直接抛异常。

    只扫一层
        技能目录里面的 references/、scripts/ 不会被误当成技能，
        因为它们在**技能目录里**，不在 root 下面。

    例子（碰文件系统，不写成 doctest，见 tests/test_skills.py）
        skills/ 里有 2 个好的、3 个坏的  ->  返回 2 个 Skill + 3 条警告
    """
    if not root.is_dir():
        return []

    found: list[Skill] = []
    for entry in sorted(root.iterdir()):     # 排序在这里，不在返回前
        if not entry.is_dir():               # README.md 之类，直接跳过
            continue
        skill = load_skill(entry)
        if skill is not None:
            found.append(skill)
    return found