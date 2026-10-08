"""SQL 注入小实验：同一个"按客户姓名查订单"，一种用 f-string 拼 SQL，一种用 ? 占位。

──────────────────────────────────────────────────────────────
这个文件是什么
──────────────────────────────────────────────────────────────
第 39 讲的一次性实验，只读 mcp_servers/xingchen_db/xingchen.db（要先跑过 build_db.py）。
它不碰 server.py：server 里的查询本来就全用 ? 占位，这里专门演示"不用会怎样"。

──────────────────────────────────────────────────────────────
怎么用
──────────────────────────────────────────────────────────────
    python scripts/injection_demo.py
"""
import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "mcp_servers" / "xingchen_db" / "xingchen.db"

# 三个"客户姓名"：一个正常的，两个不怀好意的
NAMES = [
    "张三",
    "张三' OR '1'='1",
    "张三' UNION SELECT phone, address FROM customers --",
]

QUERY = "SELECT o.id, c.name FROM orders o JOIN customers c ON c.id = o.customer_id WHERE c.name = "


def show(rows: list) -> str:
    """查出来的行，最多列 5 行，免得刷屏。"""
    head = "  ".join(str(r) for r in rows[:5])
    return f"{len(rows)} 行  {head}" + ("  ……" if len(rows) > 5 else "")


def main() -> None:
    conn = sqlite3.connect(f"{DB.as_uri()}?mode=ro", uri=True)
    for name in NAMES:
        print(f"\n输入的姓名：{name}")

        # 错误示范：把外面传进来的字直接拼进 SQL
        sql = QUERY + f"'{name}'"
        print(f"  [f-string] 拼出来的条件：WHERE c.name = '{name}'")
        print(f"             查出 {show(conn.execute(sql).fetchall())}")

        # 正确做法：SQL 里只留一个 ?，值单独交给数据库
        rows = conn.execute(QUERY + "?", (name,)).fetchall()
        print(f"  [? 占位]   查出 {show(rows)}")
    conn.close()


if __name__ == "__main__":
    main()
