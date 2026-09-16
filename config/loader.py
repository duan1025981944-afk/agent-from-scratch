"""读 config.json，解析成主程序能直接用的一个对象。

──────────────────────────────────────────────────────────────
这个文件是什么
──────────────────────────────────────────────────────────────
配置的**入口**。它做三件事：

    1. 读 config.json
    2. 把里面的 ${环境变量} 占位符替换成真实的值
    3. 把三块嵌套结构**压平**成一个 ResolvedModel

第 3 件是关键。config.json 长这样（结构说明见 config/schema.py）：

    providers    -> 怎么连上（密钥、地址）
    modelPresets -> 给"用哪家的哪个模型"这个组合起个名字
    agents       -> 默认用哪个名字

主程序不该关心"preset 指向哪个 provider"这种内部结构，它只想知道
key、url、model 是什么。压平这件事就在这里做一次。

──────────────────────────────────────────────────────────────
密钥为什么不直接写在 config.json 里
──────────────────────────────────────────────────────────────
写进去就有被提交到 git 的风险。所以 config.json 里写的是占位符：

    "apiKey": "${DEEPSEEK_API_KEY}"

真正的密钥放在系统环境变量里，本文件启动时替换。

双保险：config.json 本身也在 .gitignore 里，仓库里只有
config.example.json 这份不含密钥的模板。

──────────────────────────────────────────────────────────────
这个文件的报错为什么写得比代码还长
──────────────────────────────────────────────────────────────
**配置写错是最高频的问题**，而原生报错的信息量几乎为零：

    KeyError: 'ds'            <- 完全看不出该怎么改

所以这里每个失败分支都补上了"可用的有哪些"：

    配置里没有名为 'ds' 的 preset。可用的有：deepseek, siliconflow

这是"错误信息要带修正建议"这条原则的又一次应用——它**对人也一样适用**，
不只是对模型。
"""
import json
import os
import re
from pathlib import Path

from .schema import ProviderConfig, ResolvedModel

# 匹配 ${VAR} 形式的占位符。变量名限定为大写字母、数字、下划线，
# 且必须以字母或下划线开头——这是环境变量的通行写法。
_ENV_PATTERN = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")


def _expand_env(value):
    """把配置里的 ${VAR} 替换成环境变量的值。**递归处理嵌套结构。**

    这是个内部函数，只给 load_config() 用。

    传入什么
        value: 任意值。函数会自己判断类型：

                   str   -> 查找并替换里面所有的 ${VAR}
                   dict  -> 对每个 value 递归
                   list  -> 对每一项递归
                   其他  -> 原样返回（数字、布尔、None）

    返回什么
        替换之后的同形状数据。

    会抛什么
        RuntimeError —— 配置里用到了某个环境变量，但系统里没设置它。
        错误消息里**直接给出 PowerShell 的设置方法**，不用再去搜。

    为什么要递归
        因为占位符可能出现在任意深度：

            {"providers": {"deepseek": {"apiKey": "${DEEPSEEK_API_KEY}"}}}
                                                  ^ 藏在第三层

        只处理顶层的话就漏了。

    为什么不用 os.path.expandvars
        标准库那个函数在变量不存在时**静默保留原样**，于是密钥会变成
        字符串 "${DEEPSEEK_API_KEY}" 被发出去，报一个莫名其妙的鉴权失败。
        自己写才能做到"缺了就当场报错、并告诉你怎么设"。

    例子
        >>> os.environ["DEMO_KEY"] = "sk-abc"
        >>> _expand_env({"a": {"b": "${DEMO_KEY}"}})
        {'a': {'b': 'sk-abc'}}

        非字符串原样返回：

        >>> _expand_env([1, True, None])
        [1, True, None]

        变量没设置时报错，并告诉你怎么设：

        >>> try:
        ...     _expand_env("${NO_SUCH_VAR_HERE}")
        ... except RuntimeError as exc:
        ...     print("$env:NO_SUCH_VAR_HERE" in str(exc))
        True
    """
    if isinstance(value, str):
        def repl(m):
            name = m.group(1)
            got = os.environ.get(name)
            if got is None:
                raise RuntimeError(
                    f"配置里用到了环境变量 {name}，但系统里没有设置它。\n"
                    f"PowerShell 设置方法：$env:{name} = \"你的值\""
                )
            return got
        return _ENV_PATTERN.sub(repl, value)

    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


def load_config(path: str = "config.json") -> ResolvedModel:
    """读配置文件，把三块结构解析成主程序能直接用的形式。

    谁会用它
        - main.py，启动时调一次
        - scripts/check_tokens.py，对账脚本也用它，这样换模型不用改脚本

    传入什么
        path: 配置文件路径，默认 "config.json"。
              **是相对当前工作目录的**——所以必须在项目根目录启动程序。

    返回什么
        一个 ResolvedModel，四个字段：kind / api_key / base_url / model。

    拿到之后怎么用
        cfg = load_config()
        provider = create_provider(cfg)       # 交给工厂
        print(f"模型：{cfg.model}")            # 或者直接取字段

    会抛什么（四种，都是启动期的配置问题，就该当场炸掉）
        FileNotFoundError  找不到 config.json。
                           消息里给的是**绝对路径**，方便确认是不是在
                           错误的目录下启动的。

        RuntimeError       用到的环境变量没设置（来自 _expand_env）。

        ValueError         agents.defaults.modelPreset 指向的 preset 不存在。
                           消息里会列出所有可用的 preset 名。

        ValueError         preset 里的 provider 不存在。同样会列出可用的。

        还有一种没专门处理的：三块结构本身缺失（比如整个 "agents" 键没写）。
        那会抛原生的 KeyError。正常照着 config.example.json 抄不会遇到。

    解析的三步（对应函数体里的三段注释）
        ① agents.defaults.modelPreset  -> 默认用哪个 preset
        ② preset.provider              -> 这个 preset 用哪家的服务
        ③ 把两边的信息合并成 ResolvedModel

    kind 的默认值
        provider 里不写 kind 的话，默认 "openai_compat"。
        因为目前只有这一种实现，绝大多数配置都不用写它。
    """
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"找不到配置文件：{config_path.absolute()}")

    raw = json.loads(config_path.read_text(encoding="utf-8"))
    raw = _expand_env(raw)

    # ① 默认用哪个 preset
    preset_name = raw["agents"]["defaults"]["modelPreset"]
    preset = raw["modelPresets"].get(preset_name)
    if preset is None:
        available = ", ".join(raw["modelPresets"].keys())
        raise ValueError(f"配置里没有名为 '{preset_name}' 的 preset。可用的有：{available}")

    # ② preset 指向哪个 provider
    provider_name = preset["provider"]
    provider_raw = raw["providers"].get(provider_name)
    if provider_raw is None:
        available = ", ".join(raw["providers"].keys())
        raise ValueError(
            f"preset '{preset_name}' 指向了不存在的 provider '{provider_name}'。"
            f"可用的有：{available}"
        )

    provider = ProviderConfig(
        api_key=provider_raw["apiKey"],
        base_url=provider_raw["baseUrl"],
    )

    # ③ 合并成主程序要的形式
    return ResolvedModel(
        kind=provider_raw.get("kind", "openai_compat"),
        api_key=provider.api_key,
        base_url=provider.base_url,
        model=preset["model"],
    )
