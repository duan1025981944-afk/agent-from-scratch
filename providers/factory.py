from .base import Provider
from .openai_compat import OpenAICompatProvider
from config.schema import ResolvedModel

# 字符串 → 实现类。加新 provider 只需在这里加一行
_REGISTRY: dict[str, type[Provider]] = {
    "openai_compat": OpenAICompatProvider,
}


def create_provider(cfg: ResolvedModel) -> Provider:
    """按配置里的 kind 选出实现类并实例化。"""
    provider_cls = _REGISTRY.get(cfg.kind)
    if provider_cls is None:
        available = ", ".join(_REGISTRY.keys())
        raise ValueError(f"未知的 provider kind '{cfg.kind}'。可用的有：{available}")

    return provider_cls(cfg.api_key, cfg.base_url, cfg.model)