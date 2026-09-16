"""对账脚本：拿 agent/tokens.py 的估算值，和模型返回的真实 token 数比一比。

──────────────────────────────────────────────────────────────
这个文件是什么
──────────────────────────────────────────────────────────────
一次性的检查工具。**它不是 Agent 的一部分**——放在 scripts/ 而不是 agent/
就是这个意思，项目里没有任何代码 import 它。

它回答一个问题：**我们那把"字符数 × 系数"的尺子，到底准不准？**

    尺子估的     agent/tokens.py 的 estimate_text_tokens()，发送前就能算
    真实值       模型回复里的 usage.prompt_tokens，只有回复之后才知道

Agent 运行时只能用前者（要不要压缩必须在发送之前决定），
所以需要偶尔拿后者来校准一下。

──────────────────────────────────────────────────────────────
什么时候该跑它
──────────────────────────────────────────────────────────────
    换了模型之后              分词方式可能变了，系数要重新校
    改了 tokens.py 的系数      确认改对了方向
    发现估算偏得离谱时         定位是哪类字符估错了

平时不用跑。

──────────────────────────────────────────────────────────────
用法
──────────────────────────────────────────────────────────────
    python -m scripts.check_tokens                       量默认的两个文件
    python -m scripts.check_tokens README.md main.py     量指定的文件

**必须用 -m，不能用 python scripts\\check_tokens.py。**
因为脚本里要 from agent.tokens import ...，只有从项目根目录用 -m 运行，
Python 才会把根目录加进模块搜索路径，找得到 agent 这个包。

默认量两个文件是有讲究的：README.md 中文为主，agent/runner.py 代码为主。
两类字符的 token 成本差很多（实测中文 0.60、代码 0.21），
分开量才能看出是哪个系数需要调。

──────────────────────────────────────────────────────────────
花多少钱
──────────────────────────────────────────────────────────────
几乎不花。max_tokens=1 让模型只吐一个 token 就停——我们要的是
usage 里的**输入**统计，不是它的回答。输入按正常价计费，
两个文件加起来几千 token，可以忽略。

──────────────────────────────────────────────────────────────
怎么看输出
──────────────────────────────────────────────────────────────
    模型：deepseek-flash

    README.md
        字符数   4386   估算   1840   真实   2004   误差  -8%
    agent/runner.py
        字符数   3604   估算   1186   真实    952   误差 +25%

    误差为正（估高了）   会提前压缩，稍微浪费一点上下文，**安全**
    误差为负（估低了）   可能撑爆窗口，**危险**

原则是：**触发用的尺子，宁可估高，不能估低。**

单个文件误差大是正常的——内容太单一。真实 payload 里中文对话和代码混在一起，
误差会互相抵消，实测混合文本上只有 +4.5%。
"""

import sys
from pathlib import Path

from openai import OpenAI

from agent.tokens import estimate_text_tokens
from config.loader import load_config

# 默认量两个文件：一个中文为主，一个代码为主。
# sys.argv[1:] 是命令行上跟在脚本名后面的参数；没给就用默认的。
files = sys.argv[1:] or ["README.md", "agent/runner.py"]

# 和 main.py 读同一份 config.json：换模型只改配置，这个脚本不用跟着改。
# 早先这里的模型名是写死的，改了两次之后才抽出来——先写死、撞到第二次再抽象。
cfg = load_config()
client = OpenAI(api_key=cfg.api_key, base_url=cfg.base_url)
print(f"模型：{cfg.model}\n")

for name in files:
    text = Path(name).read_text(encoding="utf-8")

    estimated = estimate_text_tokens(text)          # 尺子量的（发送前就能知道）

    resp = client.chat.completions.create(
        model=cfg.model,
        messages=[{"role": "user", "content": text}],
        max_tokens=1,                               # 不要回答，只要 usage，几乎不花钱
    )
    real = resp.usage.prompt_tokens                 # 模型自己数的（回复后才知道）

    # 正数 = 估高了（安全），负数 = 估低了（危险）
    error = (estimated - real) / real * 100
    print(f"{name}")
    print(f"    字符数 {len(text):>6}   估算 {estimated:>6}   真实 {real:>6}   误差 {error:+.0f}%")
