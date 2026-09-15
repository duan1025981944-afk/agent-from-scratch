"""
验证②：尺子准不准 —— 拿估算值和 DeepSeek 自己数的真实值对账。

这个脚本【不是 Agent 的一部分】，是一次性的检查工具：
跑一次，看误差，决定 agent/tokens.py 里的系数要不要改。

用法（在项目根目录）：
    python -m scripts.check_tokens
    python -m scripts.check_tokens README.md agent\runner.py main.py
"""

import sys
from pathlib import Path

from openai import OpenAI

from agent.tokens import estimate_text_tokens
from config.loader import load_config

# 默认量两个文件：一个中文为主，一个代码为主。
files = sys.argv[1:] or ["README.md", "agent/runner.py"]

# 和 main.py 读同一份 config.json：换模型只改配置，这个脚本不用跟着改
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
    real = resp.usage.prompt_tokens                 # DeepSeek 数的（回复后才知道）

    error = (estimated - real) / real * 100
    print(f"{name}")
    print(f"    字符数 {len(text):>6}   估算 {estimated:>6}   真实 {real:>6}   误差 {error:+.0f}%")