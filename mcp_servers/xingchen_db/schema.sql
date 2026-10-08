-- 星辰科技演示数据库的表结构（第 39 讲）
--
-- 从 6 道目标题倒推出来的 6 张表：
--   customers        客户            题 ④（按客户查订单）、题 ⑥（手机号：在库里，但不给模型看）
--   products         商品            题 ②（是不是定制）、题 ③⑤（商品名）
--   orders           订单            题 ①②③④ 的主角
--   order_items      订单明细        题 ②③（一个订单买了哪几样）
--   shipment_events  物流轨迹        题 ①（到哪了）
--   returns          退货记录        题 ⑤（上个月退货最多的商品）
--
-- 几条约定：
--   钱一律用"分"存成整数，列名带 _cents
--   时间一律存成文本 'YYYY-MM-DD HH:MM:SS'
--   是 / 否存成 1 / 0
--   没有的值就是 NULL（比如还没签收的订单，delivered_at 是 NULL）

CREATE TABLE customers (
    id      INTEGER PRIMARY KEY,
    name    TEXT NOT NULL,
    phone   TEXT NOT NULL,                      -- 敏感：发快递要用，但模型永远不该看到
    address TEXT NOT NULL                       -- 敏感：同上
);

CREATE TABLE products (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    price_cents INTEGER NOT NULL,               -- 现价，单位：分
    is_custom   INTEGER NOT NULL DEFAULT 0      -- 1 = 定制商品（不支持无理由退货）
                CHECK (is_custom IN (0, 1))
);

CREATE TABLE orders (
    id           TEXT PRIMARY KEY,              -- 订单号，比如 'XC1024'
    customer_id  INTEGER NOT NULL REFERENCES customers(id),
    status       TEXT NOT NULL
                 CHECK (status IN ('待发货', '已发货', '已签收')),
    created_at   TEXT NOT NULL,                 -- 下单时间
    delivered_at TEXT,                          -- 签收时间；还没签收是 NULL
    carrier      TEXT,                          -- 快递公司；还没发货是 NULL
    tracking_no  TEXT                           -- 快递单号；还没发货是 NULL
);

CREATE TABLE order_items (
    order_id         TEXT NOT NULL REFERENCES orders(id),
    product_id       INTEGER NOT NULL REFERENCES products(id),
    quantity         INTEGER NOT NULL CHECK (quantity > 0),
    unit_price_cents INTEGER NOT NULL,          -- 下单时的单价，单位：分（商品以后调价也不影响）
    PRIMARY KEY (order_id, product_id)          -- 同一个订单里，同一种商品只占一行
);

CREATE TABLE shipment_events (
    id          INTEGER PRIMARY KEY,
    order_id    TEXT NOT NULL REFERENCES orders(id),
    happened_at TEXT NOT NULL,
    description TEXT NOT NULL                   -- 比如 '【杭州转运中心】已发出'
);

CREATE TABLE returns (
    id         INTEGER PRIMARY KEY,
    order_id   TEXT NOT NULL REFERENCES orders(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    quantity   INTEGER NOT NULL CHECK (quantity > 0),
    reason     TEXT NOT NULL,
    created_at TEXT NOT NULL
);
