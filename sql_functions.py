"""
SQL data layer for the MVP.

Each function runs a parameterized SELECT against SQL Server,
writes the result to a CSV file, and returns the path.

The model file (mvpmodel.py) calls these to refresh only the data it needs.
The pandas_functions.py file then reads the CSV and does the analysis.

Connection uses mssql_python + Windows Authentication (Trusted_Connection).
"""

import os
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from mssql_python import connect

load_dotenv()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DB_SERVER = os.getenv("DB_SERVER", "DESKTOP-A7N9FG2")
DB_NAME   = os.getenv("DB_NAME",   "mvptest")

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------
def get_conn():
    """Open a connection to SQL Server with Windows auth."""
    return connect(
        f"Server={DB_SERVER};"
        f"Database={DB_NAME};"
        "Trusted_Connection=yes;"
        "TrustServerCertificate=yes;"
    )


# ---------------------------------------------------------------------------
# Low-level fetch: run SQL, return DataFrame
# ---------------------------------------------------------------------------
def fetch_df(conn, sql, params=None):
    """
    Run a parameterized query and return the result as a DataFrame.
    Parameters are passed as a tuple; use '?' placeholders in the SQL.
    """
    cur = conn.cursor()
    try:
        if params:
            cur.execute(sql, params)
        else:
            cur.execute(sql)
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
        return pd.DataFrame.from_records(rows, columns=cols)
    finally:
        cur.close()


# ---------------------------------------------------------------------------
# SQL functions: each writes one CSV
# ---------------------------------------------------------------------------
def fetch_orders(conn, month=None):
    """
    Wide paid-orders table: orders + products + customers + computed revenue.
    Optional month filter (YYYYMM).

    Output columns (what pandas_functions.py expects):
        order_id, customer_id, product_id, qty, status, channel, month,
        ship_days, promised_days,
        category, unit_price_usd, unit_cogs_usd,
        region, tier, segment,
        revenue, cost, gross_profit
    """
    sql = """
        SELECT
            o.order_id,
            o.customer_id,
            o.product_id,
            o.qty,
            o.status,
            o.channel,
            o.month,
            o.ship_days,
            o.promised_days,
            p.category,
            p.unit_price_usd,
            p.unit_cogs_usd,
            c.region,
            c.tier,
            c.segment,
            o.qty * p.unit_price_usd               AS revenue,
            o.qty * p.unit_cogs_usd                AS cost,
            (o.qty * p.unit_price_usd)
              - (o.qty * p.unit_cogs_usd)          AS gross_profit
        FROM orders o
        LEFT JOIN products  p ON o.product_id  = p.product_id
        LEFT JOIN customers c ON o.customer_id = c.customer_id
        WHERE LOWER(o.status) = 'paid'
    """
    params = ()
    if month is not None:
        sql += " AND o.month = ?"
        params = (int(month),)

    df = fetch_df(conn, sql, params or None)
    path = DATA_DIR / "orders.csv"
    df.to_csv(path, index=False)
    return str(path)


def fetch_inventory(conn):
    """
    Inventory table enriched with sold_qty (from paid orders).
    Computes 'available' = on_hand - reserved so pandas_functions.py
    inventory_shortage() works without modification.

    Output columns:
        product_id, region, on_hand, reserved, inbound,
        available, sold_qty
    """
    sql = """
        WITH sold AS (
            SELECT
                o.product_id,
                c.region,
                SUM(o.qty) AS sold_qty
            FROM orders o
            LEFT JOIN customers c ON o.customer_id = c.customer_id
            WHERE LOWER(o.status) = 'paid'
            GROUP BY o.product_id, c.region
        )
        SELECT
            i.product_id,
            i.region,
            i.on_hand,
            i.reserved,
            i.inbound,
            (i.on_hand - i.reserved)               AS available,
            COALESCE(s.sold_qty, 0)                AS sold_qty
        FROM inventory i
        LEFT JOIN sold s
            ON i.product_id = s.product_id
           AND i.region     = s.region
    """
    df = fetch_df(conn, sql)
    path = DATA_DIR / "inventory.csv"
    df.to_csv(path, index=False)
    return str(path)


def fetch_returns(conn):
    """
    Returns table.

    Output columns:
        return_id, order_id, month, returned_qty, reason
    """
    sql = """
        SELECT
            return_id,
            order_id,
            month,
            returned_qty,
            reason
        FROM returns
    """
    df = fetch_df(conn, sql)
    path = DATA_DIR / "returns.csv"
    df.to_csv(path, index=False)
    return str(path)


def fetch_tickets(conn):
    """
    Support tickets table.

    Output columns:
        ticket_id, order_id, opened_month, severity,
        first_response_hours, resolution_hours
    """
    sql = """
        SELECT
            ticket_id,
            order_id,
            opened_month,
            severity,
            first_response_hours,
            resolution_hours
        FROM tickets
    """
    df = fetch_df(conn, sql)
    path = DATA_DIR / "tickets.csv"
    df.to_csv(path, index=False)
    return str(path)


# ---------------------------------------------------------------------------
# Registry: what the LLM sees to pick a SQL function
# ---------------------------------------------------------------------------
SQL_FUNCTIONS = {
    "fetch_orders": {
        "description": "جدول کامل سفارش‌های پرداخت‌شده با اطلاعات محصول، مشتری، منطقه، سگمنت و درآمد",
        "params": {"month": "int (اختیاری، فرمت YYYYMM)"},
        "csv": "orders.csv",
        "pandas": [
            "region_sales_summary",
            "monthly_comparison",
            "top_products",
            "wholesale_customer_change",
            "shipping_performance",
            "segment_analysis",
        ],
    },
    "fetch_inventory": {
        "description": "موجودی انبار به‌همراه مقدار فروش‌رفته به تفکیک محصول و منطقه",
        "params": {},
        "csv": "inventory.csv",
        "pandas": ["inventory_shortage"],
    },
    "fetch_returns": {
        "description": "اطلاعات مرجوعی‌ها (تعداد، دلیل، ماه)",
        "params": {},
        "csv": "returns.csv",
        "pandas": ["returns_analysis"],
    },
    "fetch_tickets": {
        "description": "تیکت‌های پشتیبانی (شدت، زمان پاسخ، زمان حل)",
        "params": {},
        "csv": "tickets.csv",
        "pandas": ["support_performance"],
    },
}


# ---------------------------------------------------------------------------
# Dispatcher: call a SQL function by name
# ---------------------------------------------------------------------------
def run_sql_function(conn, name, params):
    """Call the SQL function by name and return (csv_path, sql_meta)."""
    if name not in SQL_FUNCTIONS:
        raise ValueError(f"Unknown SQL function: {name}")
    fn = globals()[name]
    meta = SQL_FUNCTIONS[name]
    path = fn(conn, **params) if params else fn(conn)
    return path, meta