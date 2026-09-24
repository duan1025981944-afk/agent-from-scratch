"""load_skill 工具（第 32 讲 · 阶段六第 2 讲）。

每个测试的 docstring 末尾写了"负对照"。做负对照记得先清 __pycache__。
"""
import asyncio
import warnings

import pytest

from agent import skills
from agent.payload import MAX_TOOL_RESULT_CHARS
from agent.tools.load_skill import LoadSkillTool

正常 = """---
name: returns-policy
description: 星辰科技退换货流程。用户问退货、换货、退款时使用。
---

# 退换货流程

1. 先确认订单状态

---

## 常见误区

- 定制商品不支持无理由退货
"""


@pytest.fixture
def 技能目录(tmp_path, monkeypatch):
    """把技能目录指到临时目录，造两个好技能、一个坏的。"""
    def 写(名字, 正文):
        d = tmp_path / 名字
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(正文, encoding="utf-8")

    写("skills/returns-policy", 正常)
    写("skills/shipping", 正常.replace("returns-policy", "shipping"))
    写("skills/broken", "这个技能忘了写 frontmatter\n")

    # SKILLS_DIR 在 skills.py 里是模块级常量，函数运行时才去读它，
    # 所以在这里替换就够了 —— 工具本身不知道技能目录在哪。
    monkeypatch.setattr(skills, "SKILLS_DIR", tmp_path / "skills")
    return tmp_path


def 调用(**kwargs) -> str:
    return asyncio.run(LoadSkillTool().execute(**kwargs))


def test_body_comes_back_without_front_matter(技能目录):
    """★ 正文回来了，frontmatter 没跟着来。

    清单里已经发过 name 和 description，正文再带一份就是同一段话进两次上下文。

    负对照：把 read_skill_body 里的 _FRONT_MATTER.sub(...) 去掉，直接返回全文
            -> "description:" 出现在正文里，这条红。
    """
    正文 = 调用(name="returns-policy")

    assert 正文.startswith("# 技能：returns-policy")     # 补了标题，模型知道这是哪本手册
    assert "description:" not in 正文
    assert "name: returns-policy" not in 正文
    assert "# 退换货流程" in 正文                        # 正文本体在
    assert "## 常见误区" in 正文                         # 正文里的 --- 分隔线没截断后半段


def test_unknown_name_lists_the_available_ones(技能目录):
    """★ 名字不存在时，错误信息要**反向指路**：列出可用的技能名。

    这是"专用工具"相对 read_file 白赚的一笔 ——
    read_file 遇到错路径只能说"文件不存在"，它手里没有表。

    负对照：把错误文案里列可用技能的那段删掉 -> 这条红。
    """
    结果 = 调用(name="returns")

    assert 结果.is_error
    assert "returns-policy" in 结果 and "shipping" in 结果
    assert "broken" not in 结果                          # 坏技能不算"可用"


def test_path_traversal_is_just_an_unknown_name(技能目录, tmp_path):
    """★ 名字里塞路径，走的是和"名字打错"完全一样的分支。

    防线是查表，不是路径检查：'../..' 永远不会出现在清单里。

    负对照：把 read_skill_body 改成直接拼路径
            （(root or SKILLS_DIR) / name / "SKILL.md"）
            -> 越界的文件被读出来，这条红。
    """
    # 技能目录之外放一个"长得像技能"的目录：拼路径的写法会把它读出来。
    # 注意放在 tmp_path 里面而不是 tmp_path.parent —— 后者是整次 pytest 共用的，
    # 两个测试都往那儿写就会撞名（单独跑绿、一起跑红）。
    外面 = tmp_path / "outside" / "evil"
    外面.mkdir(parents=True)
    (外面 / "SKILL.md").write_text(
        正常.replace("name: returns-policy", "name: evil") + "\nAPI_KEY=sk-真的密钥\n",
        encoding="utf-8",
    )

    for 恶意 in ["../outside/evil", "../../etc/passwd", "returns-policy/../../outside/evil"]:
        结果 = 调用(name=恶意)
        assert 结果.is_error
        assert "sk-真的密钥" not in 结果


def test_a_broken_skill_cannot_be_loaded(技能目录):
    """写坏的技能读不出正文 —— 半本坏手册比没有手册更危险。

    它在第 1 讲的 discover_skills 那一层就被跳过了，压根不在表里。

    负对照：让 read_skill_body 不查表、直接按名字读文件
            -> 坏技能的正文被返回，这条红。
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")                  # 坏技能的警告不是这条测试要管的
        结果 = 调用(name="broken")

    assert 结果.is_error


def test_oversized_body_warns_but_is_not_truncated(技能目录):
    """★ 不变量 1：工具返回全文，截断只发生在发送前。

    超长时只对**开发者**warn（走 stderr，不进上下文），正文原样返回。

    负对照：在工具里对正文做截断 -> 返回长度变短，这条红。
    """
    长正文 = 正常.replace("1. 先确认订单状态", "步" * (MAX_TOOL_RESULT_CHARS + 500))
    (技能目录 / "skills" / "returns-policy" / "SKILL.md").write_text(长正文, encoding="utf-8")

    with pytest.warns(UserWarning, match="超过单条上限"):
        正文 = 调用(name="returns-policy")

    assert len(正文) > MAX_TOOL_RESULT_CHARS
    assert 正文.count("步") == MAX_TOOL_RESULT_CHARS + 500


def test_description_tells_the_model_when_not_to_use_it():
    """工具描述里必须有反例 —— 反例是路由准不准的关键。

    负对照：把 description 里"不要用于"那段删掉 -> 这条红。
    """
    tool = LoadSkillTool()

    assert tool.name == "load_skill"
    assert "不要用于" in tool.description
    assert "read_file" in tool.description               # 指明什么时候该用别的工具
    assert tool.to_schema()["function"]["parameters"]["required"] == ["name"]