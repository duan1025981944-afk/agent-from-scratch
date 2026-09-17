"""工作区边界：所有文件类工具共用的路径解析与越界检查。

──────────────────────────────────────────────────────────────
这个文件是什么
──────────────────────────────────────────────────────────────
一道**围栏**。它规定 Agent 只能读写某一个目录（以及它的子目录）里的文件，
出了这个范围就拒绝。

read_file / write_file / list_dir 三个工具都要做同一件事：把模型给的
路径变成真实路径，顺便确认没跑出去。这件事只在这里写一遍。

──────────────────────────────────────────────────────────────
这个文件不是什么
──────────────────────────────────────────────────────────────
- 它**不**读写文件，只算路径、只做判断
- 它**不**检查文件存不存在——那是各个工具自己的事
- 它**不**是安全沙箱。它挡的是"模型不小心写错路径"，
  挡不住有人存心绕过（比如 Agent 要是有 shell 工具，围栏就形同虚设）

──────────────────────────────────────────────────────────────
为什么必须是"先展开、再判断"（本文件最重要的一点）
──────────────────────────────────────────────────────────────
模型可能给出这样的路径：

    ../../Windows/System32/config

它看起来是个普通的相对路径，字符串里也没有任何可疑的地方。
如果先判断"它是不是以工作区开头"，再去展开 ..，那就漏了——
判断的时候它还没暴露真面目。

正确顺序是**先 resolve() 把 .. 全部展开、变成绝对路径，然后才判断**：

    (root / "../../Windows/...").resolve()
        ->  C:/Windows/System32/config      这下藏不住了
        ->  is_relative_to(root) == False   拒绝

resolve() 还会跟随符号链接，所以"工作区里放一个指向外面的快捷方式"
这招也一起挡掉了。

──────────────────────────────────────────────────────────────
围栏里还有一个"禁区"：data/
──────────────────────────────────────────────────────────────
工作区里有一个目录是 Agent **只能看、不能动**的：

    data/sessions/*.jsonl    聊天存档（不变量 1：存档存全文）
    data/memory/MEMORY.md    长期记忆（第 24 讲；只归 Dream 写）

道理很简单：**记账的人不能自己改账本。** 模型答错一次是一次的事，
它要是能改自己的存档和记忆，错误就会被写进下一次的开场白，反复喂给自己。

所以写入类工具走 resolve_for_write()，它在越界检查之后多做一道禁区检查。
读取类工具仍走 resolve()——让模型能翻自己的旧记录是有用的，风险也小得多。

参照物说明：nanobot 没有这道代码围栏，它的 write_file 只受工作区限制
（见 agent/tools/filesystem.py 的 _resolve_write），"只有 Dream 能改记忆"
是写在提示词 templates/agent/identity.md 里的约定。我们这里做得更严：
提示词是建议，代码才是保证。
"""
from pathlib import Path

# 工作区根目录。启动时由 main.py 调 set_root() 设置一次，之后全程只读。
#
# 为什么用模块级全局变量：所有工具都要用它，但工具是被自动发现、自动实例化的
# （见 loader.py），没有地方能方便地把根目录传给每一个工具。
# 存成模块级变量之后，谁要用就 import 一下。
_root: Path | None = None


class OutsideWorkspace(Exception):
    """路径越界时抛出的异常。

    谁会抛它
        本文件的 resolve()。

    谁会接它
        各个文件类工具（read_file / write_file / list_dir）。它们统一这么写：

            try:
                target = workspace.resolve(path)
            except workspace.OutsideWorkspace as exc:
                return ToolResult.error(str(exc))

    为什么工具要把它转成 ToolResult.error，而不是让它往上抛
        因为**工具报错对模型来说和工具成功是同一件事——都是一段文本**。
        抛异常会中断整个循环，Agent 就只剩"成功或崩溃"两条路；
        转成文本回给模型，它能自己换个路径重试。这是 Agent 能自愈的根本。

    异常消息写什么
        要告诉模型**边界在哪**，不能只说"不行"。resolve() 抛出的消息里
        带着工作区根目录，模型看到之后知道该往哪儿找。
    """


def set_root(path: str) -> Path:
    """设置工作区根目录。**整个程序只该调用一次。**

    谁会用它
        main.py，在模块级初始化时调：root = workspace.set_root(".")。
        以及测试里的 sandbox fixture，把根目录指到临时目录。

    传入什么
        path: 目录路径。可以是相对的（"." = 当前目录）、绝对的，
              也可以带 ~（"~/projects" 会被展开成用户主目录）。

    返回什么
        解析后的**绝对路径** Path 对象。main.py 会拿它去拼系统提示里的
        "工作区根目录：……"那一行。

    会抛什么
        ValueError —— 目录不存在，或者那个路径指向的是个文件而不是目录。
        这是启动期的配置错误，就该当场炸掉，不该等到模型来读文件时才发现。

    顺序要求（重要）
        必须在 discover_tools() **之前**调用。文件类工具在执行时要用
        resolve()，而 resolve() 需要根目录已经设好。

    例子
        >>> import tempfile
        >>> tmp = tempfile.mkdtemp()
        >>> root = set_root(tmp)
        >>> root.is_absolute()
        True
    """
    global _root
    _root = Path(path).expanduser().resolve()
    if not _root.is_dir():
        raise ValueError(f"工作区根目录不存在或不是目录：{_root}")
    return _root


def get_root() -> Path:
    """取当前工作区根目录。

    谁会用它
        本文件的 resolve()，以及 list_dir 工具（要把绝对路径显示成相对路径）。

    传入什么
        无。

    返回什么
        绝对路径 Path。

    会抛什么
        RuntimeError —— 还没调 set_root() 就来取。

        这个报错是有意为之：如果这里返回 None 或者默认成当前目录，
        围栏就会在无人察觉的情况下失效。**宁可炸掉，也不要悄悄放行。**

    例子
        >>> import tempfile
        >>> _ = set_root(tempfile.mkdtemp())
        >>> get_root().is_absolute()
        True
    """
    if _root is None:
        raise RuntimeError("工作区未初始化，请先调用 set_root()")
    return _root


# 禁区：这些目录（相对工作区根目录）里的文件，写入类工具一律拒绝。
# 现在只有一项，所以直接写死成元组——等第二项出现再考虑要不要做成配置。
NO_WRITE_DIRS = ("data",)


class ProtectedPath(Exception):
    """路径在禁区里，不许写。

    谁会抛它
        本文件的 resolve_for_write()。

    谁会接它
        写入类工具（目前只有 write_file）。写法和 OutsideWorkspace 一样：

            try:
                target = workspace.resolve_for_write(path)
            except (OutsideWorkspace, ProtectedPath) as exc:
                return ToolResult.error(str(exc))

    为什么单独开一个异常，不复用 OutsideWorkspace
        两件事的**原因不同，模型该做的反应也不同**：

            OutsideWorkspace  你跑出工作区了      -> 换个工作区内的路径重试
            ProtectedPath     这个地方谁都不许写  -> 别重试，换个地方或者告诉用户

        共用一个异常的话，模型只会看到"不行"，然后在禁区里换着花样重试。
    """


def resolve_for_write(path: str) -> Path:
    """写入类工具专用：先做越界检查，再做禁区检查。

    谁会用它
        write_file。以后有 edit_file、append_file 也一样用它。
        read_file / list_dir **不用**——读禁区是允许的。

    传入什么
        path: 和 resolve() 完全一样。

    返回什么
        解析后的绝对路径 Path。同样不保证文件存在。

    会抛什么
        OutsideWorkspace —— 跑出工作区了（由 resolve() 抛）
        ProtectedPath    —— 在禁区里（data/ 及其子目录）

    为什么禁区检查一定要放在 resolve() **之后**
        和文件顶部说的是同一个道理：路径要先展开才看得出真面目。

            "data/../data/memory/MEMORY.md"   字符串里看着绕，展开后还是禁区
            "./data/memory/../memory/x.md"    同上

        先展开再比对，这些写法一个都跑不掉。

    例子
        >>> import tempfile
        >>> root = set_root(tempfile.mkdtemp())
        >>> resolve_for_write("notes.md").name          # 普通位置，放行
        'notes.md'

        禁区里的文件，绕着写也拦得住：

        >>> for p in ["data/memory/MEMORY.md", "data/../data/sessions/a.jsonl"]:
        ...     try:
        ...         resolve_for_write(p)
        ...     except ProtectedPath:
        ...         print("拦住了")
        拦住了
        拦住了
    """
    target = resolve(path)              # ① 先过越界检查（它内部已经展开好了）
    root = get_root()

    for name in NO_WRITE_DIRS:         # ② 再看展开后的路径落在哪
        protected = root / name
        if target == protected or target.is_relative_to(protected):
            raise ProtectedPath(
                f"{path} 在受保护的目录 {name}/ 里，不能写入。"
                f"这里存的是聊天存档和长期记忆，只能读、不能改。"
                f"如果你想记住一件事，直接在回复里告诉用户，由用户决定要不要记。"
            )
    return target


def resolve(path: str) -> Path:
    """把工具收到的路径解析成绝对路径，并检查有没有越界。

    谁会用它
        read_file / write_file / list_dir，每个工具的第一步都是它。

    传入什么
        path: 模型给的路径字符串。常见的几种：

                  "agent/runner.py"     相对路径，接在工作区后面
                  "."                   工作区本身
                  "./data/x.txt"        同上
                  "D:/其他地方/x.txt"    绝对路径，**会覆盖工作区**，
                                        然后被下面的越界检查拦住

    返回什么
        解析后的绝对路径 Path。

        **它不保证这个文件/目录存在。** 存不存在是工具自己要判断的——
        write_file 需要"不存在也能用"，read_file 需要"不存在要报错"，
        两者要求相反，所以这里不管。

    会抛什么
        OutsideWorkspace —— 解析出来的路径不在工作区内。
        异常消息里带着工作区根目录，模型看到之后知道边界在哪。

    拿到之后怎么用
        工具里的标准写法：

            try:
                target = workspace.resolve(path)
            except workspace.OutsideWorkspace as exc:
                return ToolResult.error(str(exc))

            if not target.exists():                  # 存在性自己判断
                return ToolResult.error(f"文件不存在：{path}")

    两行代码的顺序不能换（见文件顶部说明）
        p = (root / path).expanduser()
        p = p.resolve()              # ★ 先展开 ..、转绝对、跟随符号链接
        if not p.is_relative_to(root):   # ★ 展开之后才判断

    例子
        >>> import tempfile, os
        >>> root = set_root(tempfile.mkdtemp())
        >>> resolve("a/b.txt").is_relative_to(root)
        True

        往上跳出去会被拦住：

        >>> try:
        ...     resolve("../../etc/passwd")
        ... except OutsideWorkspace:
        ...     print("拦住了")
        拦住了
    """
    root = get_root()
    p = (root / path).expanduser()      # 相对路径接在 root 后面；绝对路径会覆盖 root
    p = p.resolve()                     # ★ 展开 ..、转绝对、跟随符号链接

    if not p.is_relative_to(root):      # ★ 解析之后再判断
        raise OutsideWorkspace(
            f"路径 {path} 超出了工作区范围。"
            f"只能访问 {root} 及其子目录下的文件。"
        )
    return p