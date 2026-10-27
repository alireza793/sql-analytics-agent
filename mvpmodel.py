"""
MVP orchestrator with chart support.

Per question:
    LLM #1     -> pick SQL function(s) + params   -> SQL writes CSV
    Python     -> validate month, block multi-month funcs
    LLM #2     -> pick pandas function + params   -> pandas reads CSV, returns dict
    LLM #3     -> decide if chart needed + which  -> matplotlib saves PNG
    LLM #4     -> explain result in Persian + mention chart

Interactive loop. 'exit' or 'quit' to stop.
"""

import json
import os
import time
from datetime import date

import pandas as pd
import requests
from dotenv import load_dotenv

from sql_functions import (
    get_conn,
    run_sql_function,
    SQL_FUNCTIONS,
    DATA_DIR,
)
from pandas_functions import AVAILABLE_FUNCTIONS as PANDAS_FUNCTIONS
from chart_functions import (
    run_chart_function,
    CHART_FUNCTIONS,
)

import pandas_functions as pf

load_dotenv()

API_KEY     = os.getenv("AVALAI_API_KEY", "")
API_URL     = "https://api.avalai.ir/v1/chat/completions"
MODEL       = "deepseek-v4.1-flash"
TEMPERATURE = 0.2

MULTI_MONTH_PANDAS = {"monthly_comparison", "wholesale_customer_change"}

AUTO_OPEN_CHART = True  # open chart in default image viewer (Windows: os.startfile)


# ---------------------------------------------------------------------------
# LLM wrapper
# ---------------------------------------------------------------------------
def call_llm(messages, retries=2):
    last_err = None
    for attempt in range(retries + 1):
        try:
            response = requests.post(
                API_URL,
                headers={
                    "Authorization": f"Bearer {API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": MODEL,
                    "messages": messages,
                    "temperature": TEMPERATURE,
                },
                timeout=90,
            )
            if not response.ok:
                raise Exception(f"API Error {response.status_code}: {response.text}")
            return response.json()["choices"][0]["message"]["content"]

        except (requests.exceptions.Timeout,
                requests.exceptions.ConnectionError) as e:
            last_err = e
            if attempt < retries:
                time.sleep(2)
                continue
            raise Exception(f"Network error: {e}")
    raise Exception(f"LLM call failed: {last_err}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def parse_json(text):
    text = text.strip()
    if "```" in text:
        text = text.split("```")[1].strip()
        if text.startswith("json"):
            text = text[4:].strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"No JSON in model output: {text[:200]}")
    return json.loads(text[start:end + 1])


def _anchors():
    today = date.today()
    prev_year = today.year if today.month > 1 else today.year - 1
    prev_month = (today.month - 1) or 12
    return today.isoformat(), prev_year * 100 + prev_month


def _validate_month(param_month, months_available):
    if param_month is None or not months_available:
        return param_month, False
    valid = {str(m) for m in months_available}
    if str(param_month) in valid:
        return param_month, False
    return months_available[-1], True


def _is_chartable(result):
    """True if result has at least one numeric value we could plot."""
    if not isinstance(result, dict) or not result:
        return False
    if "error" in result or "message" in result:
        return False
    for v in result.values():
        if isinstance(v, (int, float)):
            return True
        if isinstance(v, dict):
            if any(isinstance(x, (int, float)) for x in v.values()):
                return True
    return False


# ---------------------------------------------------------------------------
# STEP 1: LLM picks SQL function(s)
# ---------------------------------------------------------------------------
def select_sql(question, months, regions):
    today_str, last_month = _anchors()

    prompt = f"""You are a data analyst assistant.

Today is {today_str}.
"Last month" = {last_month} (integer YYYYMM).
"Last 3 months" = the 3 calendar months before today.

User question: {question}

Available months in database: {months}
Available regions: {regions}

Available SQL functions (each refreshes one CSV from the database):
{json.dumps(SQL_FUNCTIONS, ensure_ascii=False, indent=2)}

Rules:
1. Choose the minimum set of SQL functions needed.
2. month param MUST be an integer YYYYMM.
3. Resolve "last month" / "2 months ago" etc. using the anchor above.
4. CRITICAL: If the resolved month is NOT in "Available months in database",
   use the LATEST month from that list. Never pass a month not in the list.
5. Only pass a month param if the question mentions a SPECIFIC month.
6. If the question asks for a TREND or COMPARISON across multiple months
   ("last few months", "trend", "over time", "monthly"), do NOT pass a month
   param — pass EMPTY params {{}} so ALL months are loaded.
7. Return valid JSON only, no explanation.

Return shape:
{{"calls": [{{"function": "fetch_orders", "params": {{}}}}]}}
"""
    raw = call_llm([{"role": "user", "content": prompt}])
    return parse_json(raw)


# ---------------------------------------------------------------------------
# STEP 2: LLM picks pandas function
# ---------------------------------------------------------------------------
def select_pandas(question, refreshed_pandas_names, month_hint=None):
    today_str, last_month = _anchors()

    candidates = {k: v for k, v in PANDAS_FUNCTIONS.items()
                  if k in refreshed_pandas_names}
    if not candidates:
        raise ValueError("No pandas function available for refreshed CSVs.")

    if month_hint is not None:
        hint = (
            f"IMPORTANT: Data is already filtered to ONE month ({month_hint}) "
            f"by the SQL layer. Do NOT pass a month param."
        )
    else:
        hint = (
            "IMPORTANT: Data contains ALL months. Do NOT pass a month param. "
            "Time filtering is the SQL layer's job, not yours."
        )

    prompt = f"""You are a data analyst assistant. Choose the BEST pandas function.

Today is {today_str}. "Last month" = {last_month}.

User question: {question}

Available pandas functions (only these can run):
{json.dumps(candidates, ensure_ascii=False, indent=2)}

{hint}

Function-choice guide:
- "trend", "over time", "month by month", "monthly", "growth", "decline",
  "فروش در ماه‌های اخیر", "روند"
    -> monthly_comparison
- "top products", "best sellers", "most popular products", "پرفروش‌ترین محصولات"
    -> top_products
- "which region", "by region", "compare regions", "EU vs NA", "مقایسه مناطق"
    -> region_sales_summary
- "by segment", "consumer vs enterprise", "سگمنت"
    -> segment_analysis
- "shipping", "late delivery", "ارسال", "دیرکرد"
    -> shipping_performance
- "wholesale", "big customers", "مشتریان عمده"
    -> wholesale_customer_change
- "returns", "refund", "مرجوعی"
    -> returns_analysis
- "support", "tickets", "پشتیبانی"
    -> support_performance
- "inventory", "stock", "shortage", "موجودی", "انبار"
    -> inventory_shortage

Rules:
1. Pick exactly one function.
2. Return valid JSON only.

Return shape:
{{"function": "region_sales_summary", "params": {{}}}}
"""
    raw = call_llm([{"role": "user", "content": prompt}])
    return parse_json(raw)


# ---------------------------------------------------------------------------
# STEP 3: LLM decides if a chart is needed
# ---------------------------------------------------------------------------
def select_chart(question, pandas_name, result):
    if not _is_chartable(result):
        return {"needs_chart": False}

    prompt = f"""You are a data visualization assistant.

User question: {question}
Analysis function used: {pandas_name}
Data to visualize: {json.dumps(result, ensure_ascii=False, default=str)}

Available chart types:
{json.dumps(CHART_FUNCTIONS, ensure_ascii=False, indent=2)}

Decide whether a chart adds value.

When to make a chart:
- Multiple categories compared (regions, segments, channels, products)
- Trend over time (months, periods)
- Ranking (top products, top customers)

When NOT to make a chart:
- Single number (e.g. one total, one average)
- Error or empty result

IMPORTANT:
- Use ENGLISH for title, xlabel, ylabel (matplotlib cannot render Persian well).
- For nested results like {{"202601->202602": {{"current": x}}}} use line_chart.
- Keep titles short and descriptive.

Return JSON:
{{"needs_chart": true, "function": "bar_chart", "title": "Sales by Region", "xlabel": "Region", "ylabel": "Revenue"}}
or
{{"needs_chart": false}}
"""
    raw = call_llm([{"role": "user", "content": prompt}])
    try:
        decision = parse_json(raw)
        if not decision.get("needs_chart"):
            return {"needs_chart": False}
        if decision.get("function") not in CHART_FUNCTIONS:
            return {"needs_chart": False}
        return decision
    except Exception:
        return {"needs_chart": False}


# ---------------------------------------------------------------------------
# STEP 4: LLM analyzes output (with optional chart)
# ---------------------------------------------------------------------------
def analyze_output(question, pandas_name, result, months_available,
                   corrected_month=None, month_filter=None, chart_path=None):
    latest = months_available[-1] if months_available else "unknown"

    filter_line = (
        f"SQL filter applied: month={month_filter}"
        if month_filter is not None
        else "SQL filter applied: NONE — results cover ALL available months combined."
    )

    note = ""
    if corrected_month:
        note = (
            f"\n[NOTE: The user asked about a period with no data. "
            f"Data used is from month {corrected_month}. "
            f"START your answer in Persian by telling the user that the "
            f"latest available data is from month {corrected_month}.]"
        )

    chart_line = ""
    if chart_path:
        chart_line = (
            f"\nA chart was generated at: {chart_path}\n"
            f"Mention the chart briefly in your Persian answer, e.g. "
            f"'نمودار زیر این مقایسه را نشان می‌دهد.' — do NOT describe the "
            f"chart in detail, just acknowledge it."
        )

    prompt = f"""You are a data analyst.

User question: {question}
Function used: {pandas_name}
Latest available month in DB: {latest}
{filter_line}
Results: {json.dumps(result, ensure_ascii=False, default=str)}
{note}
{chart_line}

Explain the result in simple, professional Persian.
Do not invent numbers. Do not add facts that are not in the result.

IMPORTANT: if "SQL filter applied" is NONE, the numbers are sums across ALL
months combined. Do NOT say "for month X". Say "across all available months".

If the result is empty ({{}}) or has an 'error' key, explain briefly
that no data was available and suggest a clearer question.
"""
    return call_llm([{"role": "user", "content": prompt}])


# ---------------------------------------------------------------------------
# CSV loader
# ---------------------------------------------------------------------------
def load_csv(csv_name):
    path = DATA_DIR / csv_name
    if not path.exists():
        raise FileNotFoundError(f"CSV not found: {path}")
    return pd.read_csv(
        path,
        keep_default_na=False,
        na_values=[""],
        dtype={"month": "string", "opened_month": "string"},
    )


# ---------------------------------------------------------------------------
# One question
# ---------------------------------------------------------------------------
def run_one(question, conn, months, regions):
    print(f"\n{'=' * 55}")
    print(f"Question: {question}")
    print("=" * 55)

    # STEP 1 — pick SQL
    print("\n[1] Selecting SQL function(s)...")
    sql_decision = select_sql(question, months, regions)
    calls = sql_decision.get("calls", [])
    if not calls:
        print("Model returned no SQL calls.")
        return

    refreshed_pandas = []
    sql_month = None
    corrected_month = None

    for call in calls:
        name = call.get("function")
        params = dict(call.get("params") or {})

        if "month" in params:
            safe, was_corrected = _validate_month(params["month"], months)
            if was_corrected:
                print(f"       [corrected] month {params['month']} -> {safe}")
                corrected_month = safe
            params["month"] = safe

        print(f"    -> {name} {params}")
        try:
            path, meta = run_sql_function(conn, name, params)
            print(f"       wrote {path}")
            refreshed_pandas.extend(meta["pandas"])
            if "month" in params and sql_month is None:
                sql_month = params["month"]
        except Exception as e:
            print(f"       ERROR: {e}")
            return

    # Block multi-month functions when single-month
    if sql_month is not None and refreshed_pandas:
        before = len(refreshed_pandas)
        refreshed_pandas = [f for f in refreshed_pandas
                            if f not in MULTI_MONTH_PANDAS]
        if len(refreshed_pandas) < before:
            print("       [filter] removed multi-month functions")
        if not refreshed_pandas:
            print("       No pandas function fits single-month data.")
            return

    # STEP 2 — pick pandas
    print("\n[2] Selecting pandas function...")
    pandas_decision = select_pandas(question, refreshed_pandas, month_hint=sql_month)
    pname = pandas_decision.get("function")
    pparams = pandas_decision.get("params") or {}
    print(f"    -> {pname} {pparams}")

    if pname not in PANDAS_FUNCTIONS:
        print(f"       Unknown pandas function: {pname}")
        return
    if not hasattr(pf, pname):
        print(f"       pandas_functions.py has no function '{pname}'")
        return

    csv_name = None
    for meta in SQL_FUNCTIONS.values():
        if pname in meta["pandas"]:
            csv_name = meta["csv"]
            break
    if csv_name is None:
        print(f"       No CSV mapped for '{pname}'")
        return

    df = load_csv(csv_name)
    print(f"       loaded {csv_name} ({len(df)} rows)")

    if sql_month is not None:
        pparams.pop("month", None)

    if "month" in pparams:
        safe, was_corrected = _validate_month(pparams["month"], months)
        if was_corrected:
            print(f"       [pandas-month] {pparams['month']} -> {safe}")
        pparams["month"] = safe

    # STEP 3 — run pandas
    print("\n[3] Running analysis...")
    fn = getattr(pf, pname)
    try:
        result = fn(df, **pparams) if pparams else fn(df)
    except TypeError:
        result = fn(df)
    print("\nRaw results:")
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))

    # STEP 4 — chart (optional)
    print("\n[4] Deciding on chart...")
    chart_path = None
    try:
        chart_decision = select_chart(question, pname, result)
        if chart_decision.get("needs_chart"):
            cname = chart_decision.get("function", "bar_chart")
            ctitle = chart_decision.get("title", "")
            cxlabel = chart_decision.get("xlabel", "")
            cylabel = chart_decision.get("ylabel", "Value")
            print(f"    -> {cname} — '{ctitle}'")
            chart_path = run_chart_function(
                cname, result,
                title=ctitle, xlabel=cxlabel, ylabel=cylabel,
            )
            if chart_path:
                print(f"       saved {chart_path}")
            else:
                print("       chart skipped (no numeric data after normalize)")
        else:
            print("    -> no chart needed")
    except Exception as e:
        print(f"       chart error: {e}")

    # STEP 5 — analyze
    print("\n[5] Analyzing results...")
    answer = analyze_output(
        question, pname, result, months,
        corrected_month=corrected_month,
        month_filter=sql_month,
        chart_path=chart_path,
    )
    print("\nFinal answer:")
    print(answer)

    # open chart in default viewer
    if chart_path:
        print(f"\n[chart] {chart_path}")
        if AUTO_OPEN_CHART:
            try:
                os.startfile(chart_path)
            except Exception as e:
                print(f"[chart] could not auto-open: {e}")


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def main():
    if not API_KEY.strip():
        raise ValueError("AVALAI_API_KEY is not set. Put it in .env.")

    print("Connecting to SQL Server...")
    conn = get_conn()
    print("Connected.")

    cur = conn.cursor()
    cur.execute("SELECT DISTINCT month FROM orders ORDER BY month")
    months = [r[0] for r in cur.fetchall()]
    cur.execute("SELECT DISTINCT region FROM customers ORDER BY region")
    regions = [r[0] for r in cur.fetchall()]
    cur.close()

    print(f"Months:  {months}")
    print(f"Regions: {regions}")
    print("\nType your question ('exit' or 'quit' to stop).\n")

    try:
        while True:
            try:
                q = input(">> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not q:
                continue
            if q.lower() in {"exit", "quit"}:
                break
            try:
                run_one(q, conn, months, regions)
            except Exception as e:
                print(f"\nError for this question: {e}")
    finally:
        conn.close()
        print("Bye.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\nProgram error: {e}")