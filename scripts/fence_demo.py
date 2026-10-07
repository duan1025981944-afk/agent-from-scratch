"""围栏实验：同一批"坏 SQL"，交给三种连接，看各自拦不拦得住。

──────────────────────────────────────────────────────────────
这个文件是什么
──────────────────────────────────────────────────────────────
第 39 讲的一次性实验。三种连接：
    A  什么围栏都没有：普通的可读可写连接
    B  只有围栏①：只读连接（server.py 的 connect()）
    C  围栏①②都有：只读 + 授权回调（server.py 的 connect_for_sql()，run_sql 用的就是它）

B、C 直接借 server.py 里的同一个函数，保证测的就是 server 里真在用的那一份。
写数据的那次演示在 A 上做，做完马上回滚，数据库不会真被改。

──────────────────────────────────────────────────────────────
怎么用
──────────────────────────────────────────────────────────────
    python scripts/fence_demo.py
"""
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "mcp_servers" / "xingchen_db"))
from server import DB, connect, connect_for_sql   # noqa: E402  借 server.py 里的同一份代码

# 模型"顺手"在磁盘上建的文件：放在系统临时目录里，演示完删掉
ATTACHED = Path(tempfile.gettempdir()) / "attached_by_model.db"

ATTACKS = [
    ("改数据", "DELETE FROM orders"),
    ("读手机号", "SELECT name, phone FROM customers"),
    ("UNION 夹带", "SELECT id, status FROM orders UNION SELECT phone, address FROM customers"),
    ("挂载一个新库", f"ATTACH DATABASE '{ATTACHED.as_uri()}?mode=rwc' AS x"),
]


def attempt(conn: sqlite3.Connection, sql: str) -> str:
    """执行一条 SQL，把结果或报错整理成一句话。"""
    ATTACHED.unlink(missing_ok=True)
    try:
        cursor = conn.execute(sql)
        rows = cursor.fetchall()
    except sqlite3.Error as e:
        return f"拦下了 → {e}"
    if sql.startswith("DELETE"):
        deleted = cursor.rowcount
        conn.rollback()   # 马上回滚：数据库原样不动，后面的演示也不受影响
        return f"删掉了 {deleted} 个订单（已经回滚，没有真删）"
    if sql.startswith("ATTACH"):
        conn.execute("DETACH DATABASE x")
        return f"挂上了，磁盘上多了一个文件：{ATTACHED.name}" if ATTACHED.exists() else "挂上了"
    return f"查出 {len(rows)} 行，比如 {tuple(rows[0])}"


def main() -> None:
    conns = {
        "A（没有围栏）": sqlite3.connect(DB.as_uri(), uri=True),
        "B（只读）": connect(),
        "C（只读 + 授权回调）": connect_for_sql(),
    }
    for label, sql in ATTACKS:
        print(f"\n【{label}】{sql}")
        for name, conn in conns.items():
            print(f"  {name}：{attempt(conn, sql)}")
    for conn in conns.values():
        conn.close()
    ATTACHED.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
