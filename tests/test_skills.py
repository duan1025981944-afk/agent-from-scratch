"""技能的格式与发现、按名字读正文（第 31–32 讲）。

每个测试的 docstring 末尾写了"负对照"：把代码改成那样，这个测试必须红。
做负对照记得先清 __pycache__。
"""
from pathlib import Path

import pytest

from agent.skills import (
    Skill,
    discover_skills,
    load_skill,
    parse_front_matter,
    read_skill_body,
    skill_names,
)

# 一个写得规规矩矩的技能。description 用引号包起来、末尾留三个空格 ——
# 不加引号的话 YAML 自己就把行尾空格吃了，.strip() 那行等于没被测到。
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


def test_a_good_skill_is_loaded(tmp_path):
    """写得对的技能：三个字段都对，描述首尾空白被去掉。

    负对照：把 load_skill 最后一行的 description.strip() 改成 description
            -> 描述末尾多出三个空格，这条红。
    """
    d = 写技能(tmp_path, "returns-policy", 正常)

    skill = load_skill(d)

    assert isinstance(skill, Skill)
    assert skill.name == "returns-policy"
    assert skill.description.endswith("退款时使用。")      # 末尾空格没了
    assert skill.path == d / "SKILL.md"


def test_body_divider_does_not_truncate_the_front_matter(tmp_path):
    """正文里的 --- 分隔线不能把 frontmatter 吃掉。

    正常 里正文有一条 ---。贪婪匹配会一路吃到那里，把整段正文当成 YAML。

    负对照：把 _FRONT_MATTER 正则里的 (.*?) 改成 (.*)
            -> 描述对不上（多半直接变成 None），这条红。
    """
    d = 写技能(tmp_path, "returns-policy", 正常)

    skill = load_skill(d)

    assert skill is not None
    assert skill.description.startswith("星辰科技退换货流程")


def test_directory_without_skill_md_is_skipped_silently(tmp_path, recwarn):
    """★ 没有 SKILL.md 的目录：跳过，而且**不警告**。

    skills/ 下面会有 README.md、.git、__pycache__ 这类东西，
    为它们每个都警告一次，警告就成了噪音，真问题反而被淹没。

    recwarn 是 pytest 自带的 fixture：它把这个测试里发生的警告全收起来，
    最后数一下有没有。断言"没有警告"只能这么写。

    负对照：把 load_skill 里 `if not md.is_file(): return None`
            改成 `return _skip(skill_dir, "没有 SKILL.md")` -> 冒出警告，这条红。
    """
    d = tmp_path / "empty-dir"
    d.mkdir()

    assert load_skill(d) is None
    assert len(recwarn) == 0


def test_missing_front_matter_warns(tmp_path):
    """有 SKILL.md 但没有 frontmatter：跳过 + 警告。

    这是"想当技能却写坏了"，必须出声。

    负对照：把 `if meta is None: return _skip(...)` 改成 `return None`
            -> 没有警告，这条红。
    """
    d = 写技能(tmp_path, "broken", "这个技能忘了写 frontmatter\n")

    with pytest.warns(UserWarning, match="frontmatter"):
        assert load_skill(d) is None


def test_an_empty_skill_md_also_warns(tmp_path):
    """空的 SKILL.md 也算写坏了 —— 和"没有这个文件"不是一回事。

    负对照：同上一条。
    """
    d = 写技能(tmp_path, "empty-file", "")

    with pytest.warns(UserWarning):
        assert load_skill(d) is None


def test_name_must_match_the_directory_name(tmp_path):
    """★ frontmatter 里的 name 和目录名对不上：跳过 + 警告。

    名字以目录名为准，frontmatter 的 name 只用来校验 ——
    因为模型要靠目录名拼路径去读正文，两者分家就得多维护一张映射表。

    负对照：把 `if name != skill_dir.name:` 改成 `if False:`
            -> 技能被放行，这条红。
    """
    d = 写技能(tmp_path, "mismatch", 正常.replace("name: returns-policy", "name: returns"))

    with pytest.warns(UserWarning, match="对不上"):
        assert load_skill(d) is None


def test_empty_description_is_rejected(tmp_path):
    """description 是空的：跳过 + 警告。

    没有描述的技能，模型永远不会去用它，留着只占 token。

    负对照：把 `or not description.strip()` 去掉（只留 isinstance 判断）
            -> 空描述被放行，这条红。
    """
    d = 写技能(tmp_path, "no-desc", '---\nname: no-desc\ndescription: "   "\n---\n\n正文\n')

    with pytest.warns(UserWarning, match="description"):
        assert load_skill(d) is None


def test_discovery_skips_the_broken_and_keeps_the_rest(tmp_path):
    """一个坏技能不影响其他技能被发现。

    负对照：把 discover_skills 里的 `if skill is not None` 去掉
            -> None 混进列表，长度变成 3，这条红。
    """
    写技能(tmp_path, "returns-policy", 正常)
    写技能(tmp_path, "shipping", 正常.replace("returns-policy", "shipping"))
    写技能(tmp_path, "broken", "没有 frontmatter\n")
    (tmp_path / "empty-dir").mkdir()
    (tmp_path / "README.md").write_text("我不是技能", encoding="utf-8")

    with pytest.warns(UserWarning):
        skills = discover_skills(tmp_path)

    assert [s.name for s in skills] == ["returns-policy", "shipping"]


def test_discovery_is_sorted_whatever_the_filesystem_says(tmp_path, monkeypatch):
    """★ 顺序必须稳定 —— 和工具自动发现同一个理由：前缀稳定才命中提示缓存。

    文件系统给的顺序不保证有序，所以这里把 iterdir 故意改成**倒序排列**（不是
    reversed —— 那只是把文件系统给的顺序翻过来，翻两次就又正过去了，测试会假绿），
    确认 discover_skills 自己排过。**不这样做的话，这个测试在多数机器上
    会假绿**：目录本来就常常是有序返回的。

    负对照：把 `sorted(root.iterdir())` 的 sorted 去掉 -> 顺序反了，这条红。
    """
    for 名字 in ["a-skill", "b-skill", "c-skill"]:
        写技能(tmp_path, 名字, 正常.replace("returns-policy", 名字))

    原始 = Path.iterdir
    monkeypatch.setattr(Path, "iterdir", lambda self: iter(sorted(原始(self), reverse=True)))

    assert [s.name for s in discover_skills(tmp_path)] == ["a-skill", "b-skill", "c-skill"]


def test_missing_skills_dir_is_normal_not_an_error(tmp_path):
    """没有 skills/ 目录是常态（还没写过任何技能），不是错误。

    和 MEMORY.md 缺文件同一个待遇；对比 templates/ 缺文件是直接抛 FileNotFoundError。

    负对照：把 `if not root.is_dir(): return []` 删掉
            -> iterdir 抛 FileNotFoundError，这条红。
    """
    assert discover_skills(tmp_path / "根本没有这个目录") == []


def test_parse_front_matter_returns_none_not_a_crash():
    """三种"没有能用的元数据"，都返回 None，不抛异常。

    负对照：把 `return data if isinstance(data, dict) else None` 改成 `return data`
            -> 第三种情况返回字符串，这条红。
    """
    assert parse_front_matter("") is None
    assert parse_front_matter("直接是正文\n\n---\n\n分隔线\n") is None
    assert parse_front_matter("---\n就一句话，不是键值对\n---\n") is None


# ─────────────── 第 32 讲：按名字读正文 ───────────────


def test_read_skill_body_strips_front_matter_and_adds_a_title(tmp_path):
    """★ 正文回来了，frontmatter 没跟来，开头补了一行标题。

    清单里已经发过 name 和 description，正文再带一遍就是同一段话进两次上下文。
    补标题是为了让模型知道这段文字是哪本手册。

    负对照：把 read_skill_body 最后一行的 "# 技能：{skill.name}\\n\\n" 去掉
            -> 没有标题行，这条红。
    """
    写技能(tmp_path, "returns-policy", 正常)

    正文 = read_skill_body("returns-policy", root=tmp_path)

    assert 正文.startswith("# 技能：returns-policy")
    assert "description:" not in 正文
    assert "# 退换货流程" in 正文


def test_read_skill_body_keeps_the_dividers_inside_the_body(tmp_path):
    """正文里的 --- 分隔线要原样留着，只切掉开头那段 frontmatter。

    靠的是正则的 ^ 锚（没开 MULTILINE），它只在字符串开头匹配。

    负对照：给 _FRONT_MATTER 加上 re.MULTILINE -> 正文里那条 --- 被当成
            新一段 frontmatter 的开头切掉，这条红。
    """
    写技能(tmp_path, "returns-policy", 正常)

    正文 = read_skill_body("returns-policy", root=tmp_path)

    assert "## 常见误区" in 正文                 # 分隔线后面的内容还在
    assert 正文.count("---") == 2                # 正文里两条分隔线原样留着
    assert "## 升级路径" in 正文                 # 两条分隔线之间的内容没被吞掉


def test_read_skill_body_is_none_for_unknown_and_for_broken(tmp_path):
    """名字不存在、技能写坏了，都返回 None。

    坏技能在 discover_skills 那一层就被跳过了，压根不在表里 ——
    半本坏手册比没有手册更危险。

    负对照：把 read_skill_body 改成直接拼路径读文件
            （(root or SKILLS_DIR) / name / "SKILL.md"）-> 坏技能被读出来，这条红。
    """
    写技能(tmp_path, "returns-policy", 正常)
    写技能(tmp_path, "broken", "忘了写 frontmatter\n")

    assert read_skill_body("不存在的技能", root=tmp_path) is None

    with pytest.warns(UserWarning):
        assert read_skill_body("broken", root=tmp_path) is None


def test_a_name_is_never_used_to_build_a_path(tmp_path):
    """★ 名字只用来查表，路径一律从表里取。

    防线是白名单，不是路径检查："../.." 永远不会出现在清单里。
    这一条和 tests/test_load_skill.py 里那条是同一件事的两个层次（函数 / 工具）。

    负对照：把 read_skill_body 改成直接拼路径 -> 外面那个技能被读出来，这条红。
    """
    技能根 = tmp_path / "skills"
    写技能(技能根, "returns-policy", 正常)

    # "外面"放在技能根目录之外、但仍在 tmp_path 之内 ——
    # 放到 tmp_path.parent 会和别的测试撞名（那个目录是整次 pytest 共用的）
    外面 = tmp_path / "outside" / "evil"
    外面.mkdir(parents=True)
    (外面 / "SKILL.md").write_text(
        正常.replace("name: returns-policy", "name: evil") + "\nAPI_KEY=sk-真的密钥\n",
        encoding="utf-8",
    )

    for 恶意 in ["../outside/evil", "returns-policy/../../outside/evil"]:
        assert read_skill_body(恶意, root=技能根) is None


def test_skill_names_lists_only_the_usable_ones(tmp_path):
    """skill_names 是给"名字错了"时反向指路用的，所以只列**能用**的技能。

    负对照：让 skill_names 直接用 iterdir 列目录名，不走 discover_skills
            -> 坏技能混进可用列表，这条红。
    """
    写技能(tmp_path, "shipping", 正常.replace("returns-policy", "shipping"))
    写技能(tmp_path, "returns-policy", 正常)
    写技能(tmp_path, "broken", "忘了写 frontmatter\n")

    with pytest.warns(UserWarning):
        名字 = skill_names(root=tmp_path)

    assert 名字 == ["returns-policy", "shipping"]        # 排过序，且不含 broken