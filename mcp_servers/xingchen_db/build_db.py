"""建星辰科技的演示数据库 xingchen.db：建表 + 灌演示数据 + 打印标准答案。

──────────────────────────────────────────────────────────────
这个文件是什么
──────────────────────────────────────────────────────────────
第 39 讲。一个一次性脚本，每跑一次都把同目录下的 xingchen.db 删掉重建：
    ① 按 schema.sql 建表
    ② 灌演示数据：每一条都是为某道目标题准备的
    ③ 打印 6 道目标题的标准答案，证明数据够用

演示数据里的日期都跟着"今天"走：签收时间写成"3 天前""10 天前"，
退货时间写成"上个月 5 号"。哪天重建，第 ② 题和第 ⑤ 题都照样成立。

──────────────────────────────────────────────────────────────
这个文件不是什么
──────────────────────────────────────────────────────────────
- 它不是 MCP server：server 另写（server.py），而且只读这个数据库
- 它不属于 Agent：不 import agent/ 里的任何东西

──────────────────────────────────────────────────────────────
怎么用
──────────────────────────────────────────────────────────────
    python mcp_servers/xingchen_db/build_db.py
"""
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB = HERE / "xingchen.db"
SCHEMA = HERE / "schema.sql"


def fmt(t: datetime) -> str:
    """把时间写成数据库里约定的格式 'YYYY-MM-DD HH:MM:SS'。"""
    return t.strftime("%Y-%m-%d %H:%M:%S")


def seed(conn: sqlite3.Connection, now: datetime) -> None:
    """灌演示数据。每一组数据后面都注明了是为哪道题准备的。"""
    days = lambda n: now - timedelta(days=n)                           # n 天前
    first_this = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    first_last = (first_this - timedelta(days=1)).replace(day=1)        # 上个月 1 号
    last_month = lambda d: first_last + timedelta(days=d - 1, hours=10)  # 上个月 d 号上午 10 点
    this_month = lambda k: first_this + (now - first_this) * k / 5      # 本月 1 号到现在之间，k/5 处

    # 客户。phone、address 是敏感列：题 ⑥ 用来检验围栏
    conn.executemany("INSERT INTO customers VALUES (?, ?, ?, ?)", [
        (1, "张三", "13800000001", "上海市浦东新区星辰路 1 号"),
        (2, "李四", "13800000002", "杭州市西湖区星辰路 2 号"),
        (3, "王五", "13800000003", "北京市朝阳区星辰路 3 号"),
        (4, "赵六", "13800000004", "广州市天河区星辰路 4 号"),
    ])

    # 商品。价格单位是分；4 号、6 号是定制商品（题 ②）
    conn.executemany("INSERT INTO products VALUES (?, ?, ?, ?)", [
        (1, "星辰智能台灯", 29900, 0),
        (2, "星辰蓝牙耳机", 39900, 0),
        (3, "星辰保温杯", 8900, 0),
        (4, "星辰定制刻字保温杯", 12900, 1),
        (5, "星辰机械键盘", 49900, 0),
        (6, "星辰定制照片台历", 6900, 1),
    ])

    SF = "顺丰速运"
    orders = [
        # (订单号, 客户, 状态, 下单时间, 签收时间, 快递公司, 快递单号)
        # 题 ②③：签收 3 天前，7 天内 → 可以无理由退
        ("XC1024", 1, "已签收", days(6), days(3), SF, "SF1000001024"),
        # 题 ②：签收 10 天前，超过 7 天 → 只能走质量问题
        ("XC1025", 1, "已签收", days(13), days(10), SF, "SF1000001025"),
        # 题 ②：签收 2 天前，但买的是定制商品 → 不支持无理由退
        ("XC1026", 2, "已签收", days(5), days(2), SF, "SF1000001026"),
        # 题 ①：已发货，还在路上
        ("XC1027", 3, "已发货", days(2), None, SF, "SF1000001027"),
        # 题 ④：张三刚下的单，还没发货
        ("XC1028", 1, "待发货", now - timedelta(hours=1), None, None, None),
        # 题 ⑤：上个月的订单，后面都有退货
        ("XC1010", 2, "已签收", last_month(1), last_month(3), SF, "SF1000001010"),
        ("XC1011", 3, "已签收", last_month(1), last_month(3), SF, "SF1000001011"),
        ("XC1012", 4, "已签收", last_month(1), last_month(3), SF, "SF1000001012"),
        ("XC1013", 1, "已签收", last_month(1), last_month(3), SF, "SF1000001013"),
        ("XC1014", 4, "已签收", last_month(1), last_month(3), SF, "SF1000001014"),
        # 题 ⑤ 的陷阱：上个月下单签收，这个月才退
        ("XC1015", 2, "已签收", last_month(20), last_month(25), SF, "SF1000001015"),
        ("XC1016", 3, "已签收", last_month(20), last_month(25), SF, "SF1000001016"),
        ("XC1017", 4, "已签收", last_month(20), last_month(25), SF, "SF1000001017"),
        ("XC1018", 2, "已签收", last_month(20), last_month(25), SF, "SF1000001018"),
    ]
    conn.executemany("INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?, ?)", [
        (oid, cid, status, fmt(created), fmt(delivered) if delivered else None, carrier, tracking)
        for oid, cid, status, created, delivered, carrier, tracking in orders
    ])

    # 订单明细：(订单号, 商品, 数量, 下单时的单价)
    conn.executemany("INSERT INTO order_items VALUES (?, ?, ?, ?)", [
        ("XC1024", 1, 1, 29900), ("XC1024", 3, 2, 8900),   # 题 ③：299 + 89×2 = 477 元
        ("XC1025", 2, 1, 39900),
        ("XC1026", 4, 1, 12900),                           # 定制刻字保温杯
        ("XC1027", 5, 1, 49900),
        ("XC1028", 5, 1, 49900),
        ("XC1010", 2, 1, 39900), ("XC1011", 2, 1, 39900), ("XC1012", 2, 1, 39900),
        ("XC1013", 1, 1, 29900), ("XC1014", 3, 1, 8900),
        ("XC1015", 3, 1, 8900), ("XC1016", 3, 1, 8900), ("XC1017", 3, 1, 8900), ("XC1018", 3, 1, 8900),
    ])

    # 物流轨迹：题 ① 的 XC1027 走到了哪
    conn.executemany("INSERT INTO shipment_events (order_id, happened_at, description) VALUES (?, ?, ?)", [
        ("XC1027", fmt(days(2) + timedelta(hours=3)), "【上海仓】已揽收"),
        ("XC1027", fmt(days(1)), "【杭州转运中心】已发出"),
        ("XC1027", fmt(now - timedelta(hours=3)), "【北京朝阳营业部】派送中"),
    ])

    # 退货：题 ⑤。上个月：耳机 3 次、台灯 1 次、保温杯 1 次 → 答案是耳机
    #       本月：保温杯 4 次 → 不看日期、全部加起来的话，保温杯（5 次）会排第一
    conn.executemany("INSERT INTO returns (order_id, product_id, quantity, reason, created_at) "
                     "VALUES (?, ?, ?, ?, ?)", [
        ("XC1010", 2, 1, "音质不满意", fmt(last_month(5))),
        ("XC1011", 2, 1, "左耳没声音", fmt(last_month(12))),
        ("XC1012", 2, 1, "蓝牙连接不稳定", fmt(last_month(20))),
        ("XC1013", 1, 1, "灯光偏黄", fmt(last_month(6))),
        ("XC1014", 3, 1, "杯盖漏水", fmt(last_month(8))),
        ("XC1015", 3, 1, "杯盖漏水", fmt(this_month(1))),
        ("XC1016", 3, 1, "保温效果差", fmt(this_month(2))),
        ("XC1017", 3, 1, "杯身有划痕", fmt(this_month(3))),
        ("XC1018", 3, 1, "杯盖漏水", fmt(this_month(4))),
    ])


def print_answer_key(conn: sqlite3.Connection, now: datetime) -> None:
    """把 6 道目标题的标准答案从数据里查出来打印，证明数据够用。"""
    q = lambda sql, *args: conn.execute(sql, args).fetchall()
    print("\n—— 标准答案 ——")

    status, carrier = q("SELECT status, carrier FROM orders WHERE id = 'XC1027'")[0]
    when, where = q("SELECT happened_at, description FROM shipment_events "
                    "WHERE order_id = 'XC1027' ORDER BY happened_at DESC LIMIT 1")[0]
    print(f"① XC1027 到哪了：{status}（{carrier}），最新：{when} {where}")

    for oid in ("XC1024", "XC1025", "XC1026"):
        status, delivered = q("SELECT status, delivered_at FROM orders WHERE id = ?", oid)[0]
        custom = q("SELECT p.name FROM order_items i JOIN products p ON p.id = i.product_id "
                   "WHERE i.order_id = ? AND p.is_custom = 1", oid)
        passed = (now - datetime.strptime(delivered, "%Y-%m-%d %H:%M:%S")).days
        print(f"② {oid}：{status}，签收 {passed} 天了，定制商品：{[c[0] for c in custom] or '无'}")

    rows = q("SELECT p.name, i.quantity, i.unit_price_cents FROM order_items i "
             "JOIN products p ON p.id = i.product_id WHERE i.order_id = 'XC1024'")
    total = sum(n * price for _, n, price in rows)
    items = "、".join(f"{name}×{n}（单价 {price / 100:.0f} 元）" for name, n, price in rows)
    print(f"③ XC1024：{items}，合计 {total / 100:.0f} 元")

    rows = q("SELECT o.id, o.status FROM orders o JOIN customers c ON c.id = o.customer_id "
             "WHERE c.name = '张三' ORDER BY o.created_at DESC")
    print("④ 张三的订单（新的在前）：" + "、".join(f"{oid}（{s}）" for oid, s in rows))

    first_this = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    first_last = (first_this - timedelta(days=1)).replace(day=1)
    top_last = q("SELECT p.name, SUM(r.quantity) AS n FROM returns r JOIN products p ON p.id = r.product_id "
                 "WHERE r.created_at >= ? AND r.created_at < ? GROUP BY p.name ORDER BY n DESC",
                 fmt(first_last), fmt(first_this))
    top_all = q("SELECT p.name, SUM(r.quantity) AS n FROM returns r JOIN products p ON p.id = r.product_id "
                "GROUP BY p.name ORDER BY n DESC")
    print(f"⑤ 上个月（{first_last:%Y-%m}）退货最多：{top_last[0][0]}（{top_last[0][1]} 次）"
          f"｜陷阱：不看日期的话会得到 {top_all[0][0]}（{top_all[0][1]} 次）")

    print("⑥ 张三的手机号：数据在库里，但不该给模型看（围栏要挡住）")


def main(now: datetime | None = None) -> None:
    now = now or datetime.now()
    DB.unlink(missing_ok=True)                        # 从头建，免得留着上一次的旧数据
    conn = sqlite3.connect(DB)
    conn.execute("PRAGMA foreign_keys = ON")          # 外键门卫上岗：演示数据写错编号会直接报错
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    seed(conn, now)
    conn.commit()

    for table in ("customers", "products", "orders", "order_items", "shipment_events", "returns"):
        n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]   # 表名是写死的，不是外面传进来的
        print(f"{table:16s}{n:3d} 行")
    print_answer_key(conn, now)
    conn.close()


if __name__ == "__main__":
    main()
