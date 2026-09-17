# mini-nanobot

从零复刻 [nanobot](https://github.com/HKUDS/nanobot) 的极简 Agent 框架，用尽可能少的代码讲清楚一个 Agent 到底是怎么转起来的。

不依赖 LangChain / LlamaIndex 等框架，核心循环、工具系统、会话持久化全部手写。**每个阶段都有可运行的验证，不接受"感觉更好了"。**

---

## 它能做什么

```
$ python main.py
[新会话 20260909-165921]

你 > 看一下 agent 目录里都有什么，然后读一下 runner.py，告诉我主循环的分支在哪
  [第 1 圈]
    → list_dir({'path': 'agent'})
  [第 2 圈]
    → read_file({'path': 'agent/runner.py'})
  [第 3 圈]
AI > 主循环只有一个分支点：模型这一轮要不要用工具……
```

- 多轮工具调用循环，模型可以连续调用多个工具直到任务完成
- 文件工具（`read_file` / `list_dir` / `write_file` / `echo`），操作严格限制在工作区内
- 新增工具**不需要改任何已有代码**——放一个 `.py` 文件进去就会被自动发现
- 会话持久化，关掉重开可以接着聊，支持多会话切换

---



## 快速开始

**环境**：Python 3.11+

```bash
git clone https://github.com/<你的用户名>/mini-nanobot.git
cd mini-nanobot

python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux

python -m pip install -r requirements.txt
```

**配置**：复制模板，填入你自己的模型服务地址

```bash
copy config.example.json config.json
```

API key 从环境变量读取，不写进配置文件：

```powershell
# Windows PowerShell（永久设置，设完需重开终端）
[Environment]::SetEnvironmentVariable("DEEPSEEK_API_KEY", "sk-...", "User")
```

```bash
# macOS / Linux
export DEEPSEEK_API_KEY="sk-..."
```

**运行**：

```bash
python main.py                        # 开一个新会话
python main.py --list                 # 列出所有历史会话
python main.py --session 20260909-165921   # 恢复指定会话
```

---



## 架构

```
main.py                  编排层：命令行参数、一轮对话的先后顺序、怎么打印
│
├── config/loader.py     配置加载，支持 ${ENV_VAR} 占位符
├── providers/           模型服务抽象（Provider 基类 + 工厂 + OpenAI 兼容实现）
│
├── templates/           所有提示词的正文，改文件即生效
│   ├── SOUL.md          常驻层：我是谁
│   ├── AGENTS.md        常驻层：在这个工作区怎么做事
│   └── prompts/
│       └── compaction.md  压缩历史时给模型的指令（末尾一节顺便提炼长期事实）
│
├── agent/
│   ├── runner.py        执行层：请求模型 → 要不要用工具 → 执行 → 下一圈
│   ├── payload.py       上下文层：把「存档」加工成「这一次要发的东西」
│   ├── compaction.py    上下文层：超预算时把旧历史压成摘要
│   ├── context.py       上下文层：系统提示按变动频率分层拼装
│   ├── tokens.py        上下文层：估算一份消息大约多少 token
│   ├── prompts.py       模板文件的唯一入口
│   ├── memory.py        记忆层：MEMORY.md（便利贴）的读取 + history.jsonl（流水账）的读写
│   └── tools/
│       ├── base.py      Tool 基类 + ToolResult
│       ├── loader.py    基于 pkgutil 的工具自动发现
│       ├── workspace.py 工作区边界检查
│       └── *.py         具体工具，一个文件一个
│
├── session/
│   ├── manager.py       存储层：会话生命周期（新建 / 恢复 / 列出 / 保存）
│   └── messages.py      存储层：jsonl 底层读写
├── storage/
│   └── jsonl.py         最底层：逐行读 jsonl、跳过坏行（存档和流水账共用）
│
├── data/                运行时数据，不进 git
│   ├── sessions/        聊天存档
│   └── memory/          MEMORY.md + history.jsonl
│
└── scripts/             一次性工具，不属于 Agent
    ├── check_tokens.py  估算值 vs 真实 usage 对账
    └── check_facts.py   调小触发线，看真模型提炼出什么事实
```



### 一轮对话的数据流

```
用户输入
   ↓
compact_if_needed(messages)        超预算才压缩，一轮最多一次（要花钱）
   ↓
boundary = len(messages)           记下写盘边界（在压缩之后！）
   ↓
messages.append(user)
   ↓
┌─ runner 循环 ─────────────────────────────┐
│  build_payload(messages) ──→ 模型          │   ← 截断与清理在这里，每圈都做
│  messages.append(assistant)                │
│  要用工具？ ── 是 ──→ 执行 ──→ append(tool) │   ← 存的是全文
│              └─ 否 ──→ 退出循环             │
└───────────────────────────────────────────┘
   ↓
session.save_turn(messages[boundary:])       整轮一次性落盘，先落盘再打印
```

同一段对话有三种形态，别搞混（详见 [docs/设计文档.md](docs/设计文档.md) §3）：


|      | 内存 `messages` | 磁盘 jsonl | `payload`      |
| ---- | ------------- | -------- | -------------- |
| 活多久  | 一次运行          | 永久       | 一圈             |
| 工具结果 | 全文            | 全文       | 可能截断 / 被占位说明替换 |
| 能不能改 | 能             | **只能追加** | 随便改            |


---



## 设计决策

完整的设计说明见 [docs/设计文档.md](docs/设计文档.md)。它分两部分：
**回顾路线**（隔一段时间没看，从哪里开始一层层读完）和**设计说明**
（分层规则表、一条消息的旅程、三份 messages 的区别、七条不变量、两道防线与阈值、
十六条「为什么放在这里」、与 nanobot 的差异、负对照的测试写法）。

几条最要紧的：

- **存档存全文**，截断与压缩只作用于发送前的 payload
- **两道防线按成本排队**：先清理工具结果（不花钱），再摘要压缩（要多调一次模型）
- **提示词全部在** `templates/` **下**，改文件即生效，不用改代码
- **循环层不碰产品层**：`runner.py` 不知道 `SOUL.md` 存在，也不知道在调哪家模型



## 与 nanobot 的差异

本项目不是 nanobot 的功能等价实现，而是为了理解其设计而做的重写。
以下是有意做出不同选择的地方，每条都核对过 nanobot 源码：


| 点         | nanobot                          | 本项目                             | 理由                                              |
| --------- | -------------------------------- | ------------------------------- | ----------------------------------------------- |
| 截断时机      | `max_tool_result_chars` 在执行工具时生效 | 抽出独立的 `build_payload()`，仅在发送前生效 | 存档保真；调整阈值后历史立即按新值生效                             |
| 压缩后保留多少原文 | 只保留还没发给模型的增量                     | 保留最近「半个预算」那么多 token 的原文         | 摘要有损，最近的内容模型正在用                                 |
| token 估算  | `tiktoken`（OpenAI 的分词器）          | 字符数 × 系数，零依赖                    | 拿 OpenAI 分词器数 DeepSeek 本来就是估算；系数已用真实 `usage` 校准 |
| 会话文件头     | ——                               | jsonl 首行写 `_meta`（模型、工作区、创建时间）  | `--list` 只需读一行；恢复时可检测工作区变更                      |


**一处一致、但容易想当然的地方**：压缩**不改写存档**。nanobot 的 `set_summary_checkpoint`
注释写明 *"while preserving the transcript"* —— 它同样保留完整记录，只是移动
`last_archived` 指针让重放从摘要之后开始。本项目的 `_summary` 便条 + `covered` 指针是同一思路。

### 1. 存档存全文，截断只发生在发送前

工具返回的内容超长时需要截断，否则上下文会被撑爆。问题是：**截断应该发生在写入存档之前，还是发送给模型之前？**

选择**发送前**。因为截断本质上是一种压缩策略，而压缩是有损的——一旦在写入前截断，全文就永久丢失了。


|        | 存截断版  | **存全文（本项目）**     |
| ------ | ----- | ---------------- |
| 磁盘占用   | 小     | 与读过的内容等大         |
| 调整截断阈值 | 只影响未来 | **历史立刻按新阈值重新生效** |
| 存档保真   | 否     | 是                |


实测：一次读取 20000 字的文件，终端显示发出 4027 字，磁盘上是 20000 字；多圈之后磁盘仍是 20000 字（证明 `build_payload` 确实没有修改原始数据）。

### 2. 整轮写盘，不是每条消息立即写

一轮对话可能包含多次工具调用，产生若干条消息。**要么整轮全部落盘，要么一条都不落。**

好处是磁盘上永远不会出现「assistant 说要调工具，但对应的 tool 消息不存在」这种半截状态——这种残缺消息会让 OpenAI 兼容接口直接报错。

代价是崩溃时丢掉整轮。这在「一问一答约 5 秒」的场景下可以接受；如果单轮会持续几十分钟（如 Claude Code、Codex 那类长任务工具），就应该反过来选每条立即写，并在**加载时**修复残缺——复杂度会从写入端转移到读取端。

实测：工具执行到一半 `Ctrl+C`，重启后该轮一条消息都没有落盘，存档完全自洽。

### 3. 默认新建会话，不默认续接

每次启动开一个新会话（key 用时间戳），要接着上次必须显式传 `--session`。

理由是上下文成本：默认续接意味着历史只增不减，长期运行下每一轮的 token 消耗会持续上涨。**换个会话就是最便宜的上下文清理。**

### 4. 系统提示不进存档

存档里只放真实发生过的对话（`user` / `assistant` / `tool`）。系统提示每次启动重新拼接。

这样修改提示词之后，所有历史会话下次加载都会用新的提示词，不会出现同一个会话前后提示词不一致的情况。

### 5. 工作区边界在工具执行之前检查

所有文件操作先 `Path.resolve()` 再 `is_relative_to()` 判断是否在工作区内，越界直接拒绝。

`resolve()` 不能省——不解析就无法防住 `../../etc/passwd` 这类路径穿越。

对写操作尤其重要：**读操作出错还能重来，写操作是不可逆的**，检查必须在动作之前。

---



## 开发路线


| 阶段  | 内容                     | 状态  |
| --- | ---------------------- | --- |
| 0   | 通读 nanobot 主干，画出数据流    | ✅   |
| 1   | 最小对话循环 + Provider 抽象   | ✅   |
| 2   | 工具系统：自动发现、边界检查、错误防御    | ✅   |
| 3   | 会话持久化：jsonl 存档、存档与输入分离 | ✅   |
| 4   | 上下文分层与压缩               | ✅   |
| 5   | 记忆系统（注入 / 禁区 / 流水账 / 提炼 ✅，Dream 整理 ⬜） | 🔶  |
| 6   | Skills 懒加载             | ⬜   |
| 7   | MCP 桥接                 | ⬜   |
| 8   | 子 Agent 委派             | ⬜   |
| 9   | 事件追踪                   | ⬜   |
| 10  | 整合                     | ⬜   |


---



## 参考

- [nanobot](https://github.com/HKUDS/nanobot) —— 本项目复刻的对象
- [ReAct: Synergizing Reasoning and Acting in Language Models](https://arxiv.org/abs/2210.03629)（ICLR 2023）



## License

MIT