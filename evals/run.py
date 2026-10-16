"""评估的入口：跑一遍任务集，打印成绩单，存下结果（改造 7）。

用法（在项目目录里）：

    .venv\\Scripts\\python.exe -m evals.run                     五道题各跑一次（要调模型、花钱）
    .venv\\Scripts\\python.exe -m evals.run --task fix-clip     只跑某一道（可以写多个 --task）
    .venv\\Scripts\\python.exe -m evals.run --repeat 3          每道题跑三次（模型每次答得不一样）
    .venv\\Scripts\\python.exe -m evals.run --self-check        自检判卷程序本身，不调模型、不花钱

结果存在 data/evals/{时间}.json，方便以后对比"改了提示词之后，成功率是升了还是降了"。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from evals.harness import REPO_ROOT, TaskResult, clone_repo, commit_baseline, remove_tree, run_task
from evals.tasks import TASKS, Task

RESULTS_DIR = REPO_ROOT / "data" / "evals"


def result_line(r: TaskResult) -> str:
    """一道题一行：过没过、转了几圈、花了多少、用了多久。"""
    c = r.cost
    hit = c["cache_hit"] / c["prompt"] if c.get("prompt") else 0.0
    mark = "✅ 通过" if r.passed else "❌ 没过"
    return (f"{r.task:<12} {mark}  {c.get('model_calls', 0):>2} 圈  "
            f"输入 {c.get('prompt', 0):>7,}（命中 {hit:.0%}） 输出 {c.get('output', 0):>6,}  "
            f"用时 {r.wall_seconds:>5.1f} 秒  | {r.detail}")


def report(results: list[TaskResult]) -> str:
    """最后的总结：成功率 + 合计花费。

    例子
        >>> r1 = TaskResult("a", "修 bug", True, "", "completed", 10.0,
        ...                 {"model_calls": 3, "prompt": 1000, "cache_hit": 800, "output": 50})
        >>> r2 = TaskResult("b", "问答", False, "", "completed", 5.0,
        ...                 {"model_calls": 1, "prompt": 500, "cache_hit": 400, "output": 20})
        >>> print(report([r1, r2]))
        成功 1/2（50%）  合计：4 圈，输入 1,500 token（命中 80%），输出 70 token，用时 15.0 秒
    """
    passed = sum(r.passed for r in results)
    rounds = sum(r.cost.get("model_calls", 0) for r in results)
    prompt = sum(r.cost.get("prompt", 0) for r in results)
    hit = sum(r.cost.get("cache_hit", 0) for r in results)
    output = sum(r.cost.get("output", 0) for r in results)
    wall = sum(r.wall_seconds for r in results)
    rate = passed / len(results) if results else 0.0
    hit_rate = hit / prompt if prompt else 0.0
    return (f"成功 {passed}/{len(results)}（{rate:.0%}）  合计：{rounds} 圈，"
            f"输入 {prompt:,} token（命中 {hit_rate:.0%}），输出 {output:,} token，用时 {wall:.1f} 秒")


def save(results: list[TaskResult], model: str) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / f"{datetime.now():%Y%m%d-%H%M%S}.json"
    data = {"model": model, "at": datetime.now().isoformat(timespec="seconds"),
            "summary": report(results), "results": [asdict(r) for r in results]}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def self_check(tasks: list[Task]) -> bool:
    """不调模型，只检查判卷程序：什么都不做必须判"没过"，照答案做必须判"过了"。"""
    all_good = True
    for task in tasks:
        for label, expect in (("什么都不做", False), ("照答案做", True)):
            work = Path(tempfile.mkdtemp(prefix="nb-selfcheck-"))
            repo = work / "repo"
            try:
                clone_repo(repo)
                task.setup(repo)
                commit_baseline(repo)
                answer = task.solution(repo) if expect else ""
                passed, detail = task.check(repo, answer)
            finally:
                remove_tree(work)
            good = passed == expect
            all_good &= good
            print(f"{'✓' if good else '✗'} {task.id:<12} {label}：判成{'过了' if passed else '没过'}（应该{'过' if expect else '不过'}）  {detail}")
    return all_good


async def evaluate(tasks: list[Task], repeat: int) -> None:
    from config.loader import load_agent_defaults, load_config
    from providers.factory import create_provider

    cfg = load_config()
    provider = create_provider(cfg)
    max_iterations = load_agent_defaults().max_iterations
    total = len(tasks) * repeat
    print(f"评估开始：{len(tasks)} 道题 × {repeat} 次，模型 {cfg.model}，每道题最多 {max_iterations} 圈\n")

    results: list[TaskResult] = []
    for task in tasks:
        for _ in range(repeat):
            print(f"[{len(results) + 1}/{total}] {task.id}（{task.kind}）做题中……", flush=True)
            r = await run_task(task, provider, max_iterations)
            print(f"    {result_line(r)}\n", flush=True)
            results.append(r)

    print(report(results))
    print(f"结果已存：{save(results, cfg.model)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="跑评估任务集")
    parser.add_argument("--task", action="append", help="只跑这道题（可以写多次）")
    parser.add_argument("--repeat", type=int, default=1, help="每道题跑几次")
    parser.add_argument("--self-check", action="store_true", help="只检查判卷程序，不调模型")
    args = parser.parse_args()

    tasks = [t for t in TASKS if not args.task or t.id in args.task]
    if not tasks:
        raise SystemExit(f"没有这道题。可选：{'、'.join(t.id for t in TASKS)}")

    if args.self_check:
        raise SystemExit(0 if self_check(tasks) else 1)
    asyncio.run(evaluate(tasks, args.repeat))


if __name__ == "__main__":
    main()
