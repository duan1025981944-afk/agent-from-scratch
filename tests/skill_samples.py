"""测试用的技能样例。两个技能测试文件共用这一份。

为什么单独开一个文件（第 34 讲）
    原本 tests/test_skills.py 和 tests/test_load_skill.py 各抄了一份样例。
    frontmatter 的规则一改，就要记得改两处 —— 必然漂移。

    这不是 fixture，是普通模块：样例要在模块级被 .replace() 派生出变体
    （改个名字、改坏 frontmatter），fixture 拿不到那么早。
"""
from pathlib import Path

# 一个写得规规矩矩的技能。
# description 用引号包起来、末尾留三个空格 —— 不加引号的话 YAML 自己就把行尾空格吃了，
# .strip() 那行等于没被测到。
# 正文里有**两条** --- 分隔线：一条都不留的话，"正文分隔线不该被吃掉"那条测试验不出东西。
正常 = """---
name: returns-policy
description: "星辰科技退换货流程。用户问退货、换货、退款时使用。   "
---

# 退换货流程

1. 先确认订单状态

---

## 常见误区

- 定制商品不支持无理由退货

---

## 升级路径

- 两次沟通无果转人工
"""


def 写技能(root: Path, 目录名: str, 正文: str) -> Path:
    """在 root 下造一个技能目录，返回这个目录。"""
    d = root / 目录名
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(正文, encoding="utf-8")
    return d