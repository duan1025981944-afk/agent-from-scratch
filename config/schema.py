from dataclasses import dataclass


@dataclass
class ProviderConfig:
    """怎么连上某家服务。"""
    api_key: str
    base_url: str


@dataclass
class ResolvedModel:
    """三块配置解析合并后的最终结果——主程序真正需要的东西。"""
    kind: str
    api_key: str
    base_url: str
    model: str