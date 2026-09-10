import json
from pathlib import Path

from .schema import ProviderConfig, ResolvedModel

import os
import re

_ENV_PATTERN = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")


def _expand_env(value):
    """把配置里的 ${VAR} 替换成环境变量的值。递归处理嵌套的字典和列表。"""
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
    """读配置文件，把三块结构解析成主程序能直接用的形式。"""
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
        raise ValueError(f"preset '{preset_name}' 指向了不存在的 provider '{provider_name}'。可用的有：{available}")

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