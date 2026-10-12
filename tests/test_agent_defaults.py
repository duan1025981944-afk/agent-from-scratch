"""load_agent_defaults 的测试：一轮最多转几圈，从配置里读。

每个测试的 docstring 末尾写了"负对照"：把代码改成那样，这个测试必须红。
"""
import json

import pytest

from config.loader import DEFAULT_MAX_ITERATIONS, load_agent_defaults


def write_config(tmp_path, raw: dict) -> str:
    """在临时目录里写一份 config.json，返回它的路径。"""
    path = tmp_path / "config.json"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    return str(path)


def test_default_is_used_when_not_configured(tmp_path):
    """配置里没写 maxIterations：用默认值，老的 config.json 不用改也能跑。

    负对照：把 defaults.get("maxIterations", DEFAULT_MAX_ITERATIONS) 写成 defaults["maxIterations"]
    → KeyError，本测试红。
    """
    path = write_config(tmp_path, {"agents": {"defaults": {"modelPreset": "ds"}}})

    assert load_agent_defaults(path).max_iterations == DEFAULT_MAX_ITERATIONS


def test_configured_value_is_used(tmp_path):
    """配置里写了多少就是多少。

    负对照：把 return 里的 value 写成 DEFAULT_MAX_ITERATIONS → 永远是默认值，本测试红。
    """
    path = write_config(tmp_path, {"agents": {"defaults": {"maxIterations": 7}}})

    assert load_agent_defaults(path).max_iterations == 7


@pytest.mark.parametrize("bad", [0, -3, "20", 2.5, True])
def test_nonsense_values_are_rejected_at_startup(tmp_path, bad):
    """0、负数、字符串、小数、true：启动时就报错，并说出是哪一项写错了。

    负对照：把那段 if ... raise ValueError 删掉 → 不报错，本测试红。
    """
    path = write_config(tmp_path, {"agents": {"defaults": {"maxIterations": bad}}})

    with pytest.raises(ValueError, match="maxIterations"):
        load_agent_defaults(path)