"""配置的数据形状：config.json 解析之后长什么样。

──────────────────────────────────────────────────────────────
这个文件是什么
──────────────────────────────────────────────────────────────
两个 dataclass，没有任何逻辑。它们是 config/loader.py 的产出形状。

为什么要有这个文件：让"主程序需要哪几样配置"这件事**有一个明确的地方写着**，
而不是散在代码里靠 raw["providers"]["deepseek"]["apiKey"] 这种写法到处取。

好处很实际：编辑器能补全 cfg.api_key，拼错 cfg.api_kye 会立刻标红；
用原始字典的话，拼错要到运行时才报 KeyError。

──────────────────────────────────────────────────────────────
config.json 的三块结构
──────────────────────────────────────────────────────────────
配置文件本身分三块，这个结构是照抄 nanobot 的：

    {
      "providers":    { "deepseek": { "kind": ..., "apiKey": ..., "baseUrl": ... } },
      "modelPresets": { "ds": { "provider": "deepseek", "model": "deepseek-flash" } },
      "agents":       { "defaults": { "modelPreset": "ds" } }
    }

三块的关系：

    providers      怎么连上          （密钥、地址）
    modelPresets   给"用哪家的哪个模型"这个组合**起个名字**
    agents.defaults 默认用哪个名字

**中间那层 preset 为什么存在**：以后想加备用模型、或者让子 Agent 用便宜的
模型，只要多写几个 preset，provider 配置一个字不用改。

loader.py 的活儿就是把这三块**压平**成一个 ResolvedModel——
主程序不该关心"preset 指向哪个 provider"这种内部结构，
它只想知道 key、url、model 是什么。
"""
from dataclasses import dataclass


@dataclass
class ProviderConfig:
    """怎么连上某家服务。

    谁会产生它
        config/loader.py 的 load_config()，作为解析过程中的中间产物。

    字段
        api_key:  密钥。
        base_url: 接口地址，如 "https://api.deepseek.com"。

    注意：它是个中间产物，主程序拿不到
        loader 最后返回的是 ResolvedModel，这个类只在解析途中出现一下。
        留着它是为了让"providers 这一块长什么样"有个明确的类型。

    例子
        >>> ProviderConfig(api_key="sk-x", base_url="https://api.deepseek.com").base_url
        'https://api.deepseek.com'
    """

    api_key: str
    base_url: str


@dataclass
class ResolvedModel:
    """三块配置解析合并之后的最终结果——主程序真正需要的东西。

    谁会产生它
        config/loader.py 的 load_config()。

    谁会用它
        - main.py：把它交给工厂，另外拿 cfg.model 写进新会话的文件头
        - providers/factory.py：按 cfg.kind 选实现类，其余三个字段用来实例化
        - scripts/check_tokens.py：直接拿它建一个客户端做对账

    字段
        kind:     用哪个**实现类**，如 "openai_compat"。
                  这是"换厂商"的开关——见 providers/factory.py 的 _REGISTRY。
                  config.json 里不写的话，loader 默认给 "openai_compat"。

        api_key:  密钥。
        base_url: 接口地址。
        model:    用哪个**模型**，如 "deepseek-flash"。

    kind 和 model 的区别（容易混）
        kind 决定"用哪段代码去调"，model 决定"调哪个模型"。

            DeepSeek 和硅基流动   kind 相同（都是 openai_compat），base_url 不同
            DeepSeek 和 Anthropic kind 不同（接口形状根本不一样）

    例子
        >>> cfg = ResolvedModel(kind="openai_compat", api_key="sk-x",
        ...                     base_url="https://api.deepseek.com",
        ...                     model="deepseek-flash")
        >>> cfg.kind, cfg.model
        ('openai_compat', 'deepseek-flash')
    """

    kind: str
    api_key: str
    base_url: str
    model: str
