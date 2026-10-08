"""星辰科技数据库 MCP server：让模型能查订单。

──────────────────────────────────────────────────────────────
这个文件是什么
──────────────────────────────────────────────────────────────
第 39 讲。一个独立的小程序，把同目录下的 xingchen.db（build_db.py 建的）
包装成几个工具。它只读数据库，从不改数据。

菜单上的菜（工具）：
    get_order     查一个订单的全部事实          目标题 ①②③
    list_orders   按客户姓名列出他的订单        目标题 ④
    run_sql       带围栏的只读 SQL，兜底用      目标题 ⑤（⑥ 要被它的围栏挡住）

工具是按"一道题一次要拿齐哪些事实"来切的，不是按表来切的：
get_order 一次就把状态、物流、商品、金额、签收了几天都给齐，
一个工具就能答 ①②③ 三道题。

──────────────────────────────────────────────────────────────
这个文件不是什么
──────────────────────────────────────────────────────────────
- 它不属于 Agent：不 import agent/ 里的任何东西，Agent 只通过配置认识它
- 它不知道 returns-policy 技能的存在：它只给事实，判断规则归 Agent 那边
- 它不建库：库由 build_db.py 建；库不存在时，server 启动就报错退出

──────────────────────────────────────────────────────────────
写工具的规矩（第 37、38 讲，外加第 39 讲四条）
──────────────────────────────────────────────────────────────
- 每个参数都写类型注解；docstring 只写给模型看的话；预料到的失败 raise ToolError
- 只返回回答问题需要的列：手机号、住址永远不出现在结果里（最小权限）
- 模型需要算的东西替它算好：比如"签收了几天"，模型就不用自己去想今天几号
- 模型给的值一律用 ? 占位交给数据库，绝不拼进 SQL 字符串（39.6）
- 整条 SQL 都由模型来写的工具（run_sql）必须上围栏：
  只读、白名单授权、限行数、限时间（39.7、39.8）
"""
import sqlite3
import sys
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

sys.stderr.reconfigure(encoding="utf-8")   # 日志走 stderr，用 UTF-8（第 36 讲插曲三）

DB = Path(__file__).resolve().parent / "xingchen.db"

# 启动期的错误要大声：库都没有，就别假装能营业（第 36 讲插曲一：死因写在 stderr）
if not DB.exists():
    sys.exit(f"找不到数据库 {DB}，先运行：python mcp_servers/xingchen_db/build_db.py")

server = MCPServer("xingchen")


def connect() -> sqlite3.Connection:
    """只读地打开数据库。每次调用工具都新开一个连接，用完就关。

    mode=ro：只读模式，这个连接写不了任何东西。
    row_factory = sqlite3.Row：查出来的每一行可以用列名取值，比如 row["status"]。
    """
    conn = sqlite3.connect(f"{DB.as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def yuan(cents: int) -> float:
    """分 → 元。数据库里的钱都是分，给模型看的时候换成元。"""
    return cents / 100


@server.tool()
def get_order(
    order_id: Annotated[str, Field(
        pattern=r"^XC\d{4}$",
        description="订单号：大写 XC 加 4 位数字，例如 XC1024",
    )],
) -> dict[str, Any]:
    """查一个订单的全部事实：状态、下单时间、签收时间和签收了几天、快递和物流轨迹、
    买了哪些商品（数量、单价、是不是定制商品）、合计金额。

    用户问订单到哪了、买了什么、花了多少钱、能不能退货时，先用它把事实查清楚。
    """
    with closing(connect()) as conn:
        # SQL 里的 ? 是占位符：值单独交给数据库，不拼进 SQL 字符串（39.6）
        order = conn.execute(
            "SELECT o.id, o.status, o.created_at, o.delivered_at, o.carrier, o.tracking_no, "
            "       c.name AS customer "
            "FROM orders o JOIN customers c ON c.id = o.customer_id "
            "WHERE o.id = ?",
            (order_id,),
        ).fetchone()
        if order is None:
            # 纸条（第 37 讲）：写清楚错在哪、下一步怎么办
            raise ToolError(f"查不到订单 {order_id}。请向用户确认订单号是否写对（形如 XC1024）。")

        items = conn.execute(
            "SELECT p.name, i.quantity, i.unit_price_cents, p.is_custom "
            "FROM order_items i JOIN products p ON p.id = i.product_id "
            "WHERE i.order_id = ? ORDER BY p.id",
            (order_id,),
        ).fetchall()
        events = conn.execute(
            "SELECT happened_at, description FROM shipment_events "
            "WHERE order_id = ? ORDER BY happened_at",
            (order_id,),
        ).fetchall()

    # 签收了几天：替模型算好。没签收的订单是 None（JSON 里会变成 null）
    days_since_delivered = None
    if order["delivered_at"]:
        delivered = datetime.strptime(order["delivered_at"], "%Y-%m-%d %H:%M:%S")
        days_since_delivered = (datetime.now() - delivered).days

    return {
        "order_id": order["id"],
        "customer": order["customer"],            # 只给姓名；手机号、住址不给
        "status": order["status"],
        "created_at": order["created_at"],
        "delivered_at": order["delivered_at"],
        "days_since_delivered": days_since_delivered,
        "carrier": order["carrier"],
        "tracking_no": order["tracking_no"],
        "shipment_events": [
            {"time": e["happened_at"], "description": e["description"]} for e in events
        ],
        "items": [
            {
                "product": i["name"],
                "quantity": i["quantity"],
                "unit_price_yuan": yuan(i["unit_price_cents"]),
                "is_custom": bool(i["is_custom"]),
            }
            for i in items
        ],
        "total_yuan": yuan(sum(i["quantity"] * i["unit_price_cents"] for i in items)),
    }


@server.tool()
def list_orders(
    customer_name: Annotated[str, Field(
        min_length=1,
        max_length=20,
        description="客户的姓名，例如 张三",
    )],
) -> dict[str, Any]:
    """按客户姓名列出他的全部订单，新的在前。每个订单给出订单号、状态、下单时间和合计金额。

    用户问某人最近买过什么、下过哪些订单时用。要看某个订单的细节，再用 get_order。
    """
    with closing(connect()) as conn:
        customer = conn.execute(
            "SELECT id FROM customers WHERE name = ?", (customer_name,)
        ).fetchone()
        if customer is None:
            raise ToolError(f"没有找到叫「{customer_name}」的客户。请向用户确认姓名是否写对。")

        rows = conn.execute(
            "SELECT o.id, o.status, o.created_at, "
            "       (SELECT SUM(i.quantity * i.unit_price_cents) "
            "        FROM order_items i WHERE i.order_id = o.id) AS total_cents "
            "FROM orders o WHERE o.customer_id = ? "
            "ORDER BY o.created_at DESC",
            (customer["id"],),
        ).fetchall()

    return {
        "customer": customer_name,
        "orders": [
            {
                "order_id": r["id"],
                "status": r["status"],
                "created_at": r["created_at"],
                "total_yuan": yuan(r["total_cents"] or 0),
            }
            for r in rows
        ],
    }


# ── run_sql 的围栏 ───────────────────────────────────────────────
# 围栏①：只读连接。connect() 本来就是 mode=ro，改不了任何数据。
# 围栏②：授权回调。SQLite 每做一个动作（读某一列、调一个函数、挂载一个库……）
#         都会先来问一声"可以吗"。我们用白名单回答：只放行"读"要用到的动作，
#         读敏感列也不行，其余一律拒绝。
# 围栏③：限行数。一次最多给模型看 MAX_ROWS 行，多了就截断，并告诉它怎么改查询。
# 围栏④：限时间。一条 SQL 最多跑 MAX_SECONDS 秒，超时当场中断。
#         run_sql 是同步函数，第 38 讲说过：退菜单停不下它，只能 server 自己掐断。
#         所以这块表要比客户端的表（mcp_raw.py 是 5 秒，nanobot 默认 30 秒）走得快。

MAX_ROWS = 50
MAX_SECONDS = 2

SENSITIVE_COLUMNS = {("customers", "phone"), ("customers", "address")}

ALLOWED_ACTIONS = {
    sqlite3.SQLITE_SELECT,      # 执行一条 SELECT
    sqlite3.SQLITE_READ,        # 读某张表的某一列
    sqlite3.SQLITE_FUNCTION,    # 调用函数，比如 date()、SUM()
    sqlite3.SQLITE_RECURSIVE,   # 递归的 WITH 查询
}


def authorizer(action: int, arg1: str | None, arg2: str | None,
               db_name: str | None, trigger: str | None) -> int:
    """SQLite 每做一个动作之前，都先调用这个函数问一声：可以吗？

    action 是动作的编号；arg1、arg2 的含义随动作变化。
    对"读一列"（SQLITE_READ）来说，arg1 是表名，arg2 是列名。
    """
    if action == sqlite3.SQLITE_READ and (arg1, arg2) in SENSITIVE_COLUMNS:
        return sqlite3.SQLITE_DENY          # 敏感列：一碰就拒
    if action not in ALLOWED_ACTIONS:
        return sqlite3.SQLITE_DENY          # 不在白名单里的动作：写、挂载、PRAGMA……一律拒
    return sqlite3.SQLITE_OK


def connect_for_sql() -> sqlite3.Connection:
    """给 run_sql 用的连接：在只读连接（围栏①）上，再装上授权回调（围栏②）。"""
    conn = connect()
    conn.set_authorizer(authorizer)
    return conn


@server.tool()
def run_sql(
    sql: Annotated[str, Field(
        min_length=1,
        max_length=2000,
        description="一条 SQLite 语法的 SELECT 语句",
    )],
) -> dict[str, Any]:
    """用一条只读的 SQL（SQLite 语法）查询星辰科技的订单数据库。

    只在 get_order、list_orders 答不了的时候用，比如跨订单的统计：
    "上个月退货最多的商品是哪个？"。每次只能执行一条 SELECT 语句。

    表结构：
      customers(id, name)  客户；手机号、住址不可查询
      products(id, name, price_cents, is_custom)  商品；price_cents 单位是分；is_custom=1 是定制商品
      orders(id, customer_id, status, created_at, delivered_at, carrier, tracking_no)
          订单；id 形如 'XC1024'；status 取值：待发货、已发货、已签收；还没签收的 delivered_at 是 NULL
      order_items(order_id, product_id, quantity, unit_price_cents)  订单明细；单价是下单时的价格，单位是分
      shipment_events(id, order_id, happened_at, description)  物流轨迹
      returns(id, order_id, product_id, quantity, reason, created_at)  退货记录

    时间都是 'YYYY-MM-DD HH:MM:SS' 格式的文本，可以直接比大小。
    算"上个月"可以用：date('now', 'localtime', 'start of month', '-1 month') 到
    date('now', 'localtime', 'start of month') 之间。
    """
    with closing(connect_for_sql()) as conn:
        # 围栏④：SQLite 每执行一万步，就调用一次这个函数；它返回 True，查询当场中断
        deadline = time.monotonic() + MAX_SECONDS
        conn.set_progress_handler(lambda: time.monotonic() > deadline, 10_000)
        try:
            cursor = conn.execute(sql)
            columns = [d[0] for d in cursor.description]
            # 围栏③：只取 MAX_ROWS + 1 行。多出来的那 1 行只用来判断"后面还有没有"
            rows = [list(row) for row in cursor.fetchmany(MAX_ROWS + 1)]
        except sqlite3.OperationalError as e:
            if str(e) == "interrupted":
                raise ToolError(
                    f"查询超过 {MAX_SECONDS} 秒，被中断了。"
                    "请简化 SQL：加 WHERE 缩小范围，或者先用 GROUP BY 汇总。"
                )
            raise ToolError(f"SQL 没有执行：{e}。注意：只能执行一条 SELECT 语句。")
        except sqlite3.Error as e:
            # SQL 写错了、碰了不该碰的，都是模型这边的问题：把原因告诉它，让它改（纸条）
            raise ToolError(
                f"SQL 没有执行：{e}。注意：只能执行一条 SELECT 语句；"
                "customers 表的 phone、address 不可查询。"
            )

    result: dict[str, Any] = {
        "columns": columns,
        "rows": rows[:MAX_ROWS],
        "row_count": min(len(rows), MAX_ROWS),
    }
    if len(rows) > MAX_ROWS:
        # 截断了就明说，还要告诉模型下一步怎么改（Anthropic 那篇文章：截断时要给出引导）
        result["truncated"] = True
        result["note"] = (
            f"结果超过 {MAX_ROWS} 行，只给了前 {MAX_ROWS} 行。"
            "要看全貌，请用 COUNT、GROUP BY 先汇总，或者加 WHERE、LIMIT 缩小范围。"
        )
    return result


if __name__ == "__main__":
    server.run()
