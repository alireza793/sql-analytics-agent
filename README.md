# MVP Analytics Agent

A natural-language analytics agent that connects to a SQL Server database, selects the right analysis function based on a user's question, runs it, optionally generates a chart, and explains the results in Persian.

**Status:** MVP complete, demo-ready. Hallucination-resistant (5/5 adversarial tests passed).

---

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Database Schema](#database-schema)
4. [Project Structure](#project-structure)
5. [Setup](#setup)
6. [How It Works](#how-it-works)
7. [Available Functions](#available-functions)
8. [Usage](#usage)
9. [Example Questions](#example-questions)
10. [Design Decisions](#design-decisions)
11. [Issues Fixed During Development](#issues-fixed-during-development)
12. [Testing: Hallucination Resistance](#testing-hallucination-resistance)
13. [Known Limitations](#known-limitations)
14. [Roadmap (v2)](#roadmap-v2)
15. [Requirements](#requirements)

---

## Overview

The agent answers analytical questions about sales data. A user types a question; the agent:

1. Picks the correct **SQL function** to refresh the necessary data from the database into a CSV.
2. Picks the correct **pandas function** to analyze the CSV.
3. Optionally generates a **matplotlib chart**.
4. Explains the result in Persian using an LLM.

**Key constraints and principles:**

- **The LLM never reads SQL directly.** Python does. The LLM only chooses which function to run.
- **Data is filtered/aggregated at the SQL level**, not loaded wholesale into memory.
- **The LLM cannot invent numbers.** The analyze prompt enforces "do not add facts not in the result".
- **All decisions are validated in code**, not left purely to the LLM. If the LLM picks a month that doesn't exist, Python corrects it. If it picks a function that needs 2+ months but only 1 is loaded, Python blocks it.

---

## Architecture

Four files, four layers, four LLM calls per question.

```
┌─────────────────────────────────────────────────────────┐
│ User Question                                           │
└─────────────────────────────────────────────────────────┘
                         ↓
┌─────────────────────────────────────────────────────────┐
│ mvpmodel.py — Orchestrator                              │
│                                                         │
│  LLM #1 → pick SQL function(s) + params                │
│  LLM #2 → pick pandas function + params                │
│  LLM #3 → decide if chart needed + which type          │
│  LLM #4 → explain result in Persian                    │
└─────────────────────────────────────────────────────────┘
       ↓               ↓               ↓
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│ sql_         │ │ pandas_      │ │ chart_       │
│ functions.py │ │ functions.py │ │ functions.py │
│              │ │              │ │              │
│ DB → CSV     │ │ CSV → dict   │ │ dict → PNG   │
└──────────────┘ └──────────────┘ └──────────────┘
       ↓               
┌──────────────┐
│ data/        │
│ *.csv        │
└──────────────┘
```

**Data flow:**

```
Question
  → [LLM #1] SQL function choice
  → SQL Server query → CSV file (data/xxx.csv)
  → [LLM #2] Pandas function choice (restricted to functions whose CSV was loaded)
  → Pandas analysis → dict
  → [LLM #3] Chart decision
  → matplotlib → PNG (charts/xxx.png)
  → [LLM #4] Persian explanation with chart reference
  → Final answer
```

---

## Database Schema

**Database:** SQL Server `mvptest` on `localhost` (or `DESKTOP-A7N9FG2`).

**Connection:** Windows Authentication (`Trusted_Connection=yes`).

### Tables

| Table | Purpose | Key columns |
|---|---|---|
| `orders` | Sales orders | `order_id`, `customer_id`, `product_id`, `month`, `qty`, `status`, `channel`, `ship_days`, `promised_days` |
| `customers` | Customer profiles | `customer_id`, `region`, `tier`, `segment` |
| `products` | Product catalog | `product_id`, `category`, `unit_price_usd`, `unit_cogs_usd`, `lead_time_days` |
| `inventory` | Stock levels | `product_id`, `region`, `on_hand`, `reserved`, `inbound` |
| `returns` | Return records | `return_id`, `order_id`, `month`, `returned_qty`, `reason` |
| `tickets` | Support tickets | `ticket_id`, `order_id`, `opened_month`, `severity`, `first_response_hours`, `resolution_hours` |
| `fx_to_usd_bp` | Exchange rates | (not used by MVP) |

### Critical data type rules

1. **`month` and `opened_month` MUST be `nvarchar(10)`** storing `'YYYYMM'` (e.g. `'202601'`).
   - Do **not** use `int` — SQL Server's import wizard misinterprets it.
   - Do **not** use `date` — `YEAR()`/`MONTH()` operations corrupt it.
   - This is the **single most important rule** to preserve downstream behavior.

2. **`region` values include `'NA'`** (North America). This is a real region, not a NULL.
   - Column type: `nvarchar(50)`, Allow Nulls: **unchecked**.
   - The import wizard will try to convert `'NA'` to `NULL`. **Override this.**
   - All CSV loading uses `keep_default_na=False` to preserve `'NA'` as a string.

3. **Data range:** `202601` – `202606` (January–June 2026).

### Recommended types (wizard "Modify Columns")

**`orders.csv`:**
| Column | Type |
|---|---|
| `order_id`, `customer_id`, `product_id`, `qty`, `ship_days`, `promised_days` | `int` |
| `status`, `channel` | `nvarchar(50)` |
| `month` | **`nvarchar(10)`** |

**`customers.csv`:** `customer_id` int; `region`, `tier`, `segment` nvarchar(50).

**`products.csv`:** `product_id` int; `category` nvarchar(50); `unit_price_usd`, `unit_cogs_usd` decimal(18,2); `lead_time_days` int.

**`inventory.csv`:** `product_id` int; `region` nvarchar(20); `on_hand`, `reserved`, `inbound` int.

**`returns.csv`:** `return_id`, `order_id`, `returned_qty` int; `month` **nvarchar(10)**; `reason` nvarchar(50).

**`tickets.csv`:** `ticket_id`, `order_id` int; `opened_month` **nvarchar(10)**; `severity` nvarchar(50); `first_response_hours`, `resolution_hours` int or decimal(10,2).

---

## Project Structure

```
MVPtest/
├── .env                        # Credentials (not committed)
├── .env.example                # Template
├── README.md                   # This file
├── sql_functions.py            # Layer 1: DB → CSV
├── pandas_functions.py         # Layer 2: CSV → dict
├── chart_functions.py          # Layer 3: dict → PNG
├── mvpmodel.py                 # Orchestrator (interactive loop)
├── data/                       # CSV outputs (auto-created)
│   ├── orders.csv
│   ├── inventory.csv
│   ├── returns.csv
│   └── tickets.csv
└── charts/                     # PNG outputs (auto-created)
    ├── bar_20261021_143022.png
    ├── line_20261021_143125.png
    └── ...
```

---

## Setup

### 1. Python environment

Python 3.14 is used. Required packages:

```bash
pip install pandas mssql-python requests python-dotenv matplotlib
```

Or from `requirements.txt`:

```bash
pip install -r requirements.txt
```

### 2. SQL Server

- SQL Server installed locally (`localhost` or `DESKTOP-A7N9FG2`)
- Database `mvptest` created
- Six tables imported from CSV (see [Database Schema](#database-schema) for type rules)
- Windows Authentication enabled, or a valid login created

**Test the connection:**

```python
from mssql_python import connect
conn = connect(
    "Server=DESKTOP-A7N9FG2;"
    "Database=mvptest;"
    "Trusted_Connection=yes;"
    "TrustServerCertificate=yes;"
)
print("Connected!")
```

### 3. `.env` file

Create `.env` next to `mvpmodel.py`:

```ini
DB_SERVER=DESKTOP-A7N9FG2
DB_NAME=mvptest
AVALAI_API_KEY=your_api_key_here
```

### 4. Run

```bash
cd C:\Users\temp-tak\Desktop\MVPtest
python mvpmodel.py
```

---

## How It Works

### Step-by-step for a single question

**Example:** `>> فروش در ماه 202606 به تفکیک منطقه`

#### Step 1 — LLM #1 picks SQL function

```
[1] Selecting SQL function(s)...
    -> fetch_orders {'month': 202606}
       wrote data/orders.csv
```

- LLM receives: question, today's date, available months, available regions, `SQL_FUNCTIONS` registry.
- LLM returns: `{"calls": [{"function": "fetch_orders", "params": {"month": 202606}}]}`.
- Python validates: `202606` is in `['202601', ..., '202606']` ✓.
- `fetch_orders` runs the SQL query, writes `orders.csv`.

#### Step 2 — LLM #2 picks pandas function

```
[2] Selecting pandas function...
    -> region_sales_summary {}
       loaded orders.csv (90 rows)
```

- LLM receives: question, **only functions whose CSV was loaded**, hint that data is single-month.
- LLM returns: `{"function": "region_sales_summary", "params": {}}`.
- Python blocks `monthly_comparison` and `wholesale_customer_change` because only 1 month is loaded.
- `region_sales_summary` reads `orders.csv`, returns `{"APAC": 12446.0, "EU": 15682.0, ...}`.

#### Step 3 — LLM #3 decides on chart

```
[4] Deciding on chart...
    -> bar_chart — 'Sales by Region'
       saved charts/bar_20261021_143022.png
```

- LLM receives: question, function used, result data, available chart types.
- LLM returns: `{"needs_chart": true, "function": "bar_chart", "title": "Sales by Region", ...}`.
- Python runs `bar_chart` from `chart_functions.py`, saves PNG.
- Chart is auto-opened in default image viewer (Windows).

#### Step 4 — LLM #4 explains

```
[5] Analyzing results...
Final answer:
فروش ثبت‌شده برای ماه 202606 به تفکیک منطقه به شرح زیر است:
| منطقه | فروش |
| APAC  | 12,446 |
| EU    | 15,682 |
| LATAM | 9,641  |
| NA    | 12,320 |
نمودار زیر این مقایسه را نشان می‌دهد.
```

- LLM receives: question, function used, result data, month filter status, chart path.
- LLM explains in Persian and references the chart.
- Enforced: no invented numbers, no invented reasons, disclose which months data covers.

### Validation layers (Python, not LLM)

Between each LLM call, Python enforces correctness:

| Validation | What it prevents |
|---|---|
| `_validate_month` on SQL month | LLM picking a month that doesn't exist (e.g. `202609` when data ends at `202606`) → fallback to latest available |
| `MULTI_MONTH_PANDAS` block | LLM picking `monthly_comparison` when SQL filtered to 1 month → `{"error": "need >= 2 months"}` |
| Drop `month` from pandas params when SQL filtered | Double-filtering an already-filtered CSV → empty result |
| `_validate_month` on pandas month | LLM passing a nonexistent month directly to pandas → filtered to nothing |
| `_is_chartable` | Attempting chart when result is `{}` or `{"error": ...}` |
| `parse_json` with fence stripping | LLM wrapping JSON in ` ```json ... ``` ` |

---

## Available Functions

### SQL Functions (`sql_functions.py`)

| Function | Output CSV | Feeds pandas functions |
|---|---|---|
| `fetch_orders(conn, month=None)` | `orders.csv` | `region_sales_summary`, `monthly_comparison`, `top_products`, `wholesale_customer_change`, `shipping_performance`, `segment_analysis` |
| `fetch_inventory(conn)` | `inventory.csv` | `inventory_shortage` |
| `fetch_returns(conn)` | `returns.csv` | `returns_analysis` |
| `fetch_tickets(conn)` | `tickets.csv` | `support_performance` |

**`fetch_orders` SQL logic:**
- Joins `orders` + `products` + `customers` (`LEFT JOIN`)
- Computes `revenue = qty * unit_price_usd` in SQL
- Computes `cost = qty * unit_cogs_usd`
- Computes `gross_profit = revenue - cost`
- Filters `WHERE LOWER(status) = 'paid'`
- Optional `AND month = ?` when `month` is passed

**`fetch_inventory` SQL logic:**
- CTE computes `sold_qty` per `(product_id, region)` from paid orders
- Joins with `inventory` table
- Computes `available = on_hand - reserved`
- Provides columns `inventory_shortage` needs

### Pandas Functions (`pandas_functions.py`)

| Function | Params | Returns |
|---|---|---|
| `region_sales_summary(df, month=None)` | optional month | `{region: revenue}` |
| `monthly_comparison(df, region=None)` | optional region | `{"prev->curr": {prev, current, change, change_pct}}` |
| `top_products(df, month=None, n=10)` | optional month, n | `{product_id: revenue}` |
| `wholesale_customer_change(df)` | none | counts + top 5 in each direction |
| `inventory_shortage(df)` | none | shortage count + top 5 shortages |
| `shipping_performance(df, month=None)` | optional month | `{total_orders, late_orders, late_pct, avg_ship_days, avg_promised_days}` |
| `returns_analysis(df)` | none | `{total_returns, total_units, by_reason}` |
| `support_performance(df)` | none | `{total_tickets, avg_first_response, avg_resolution, by_severity}` |
| `segment_analysis(df, month=None)` | optional month | `{segment: revenue}` |

**Notes:**
- `region_sales_summary` and `segment_analysis` use `groupby(..., dropna=False)` to avoid silently dropping NULL labels.
- `monthly_comparison` and `wholesale_customer_change` require ≥2 months of data — blocked by the orchestrator if SQL filtered to a single month.

### Chart Functions (`chart_functions.py`)

| Function | Best for | Output |
|---|---|---|
| `bar_chart(data, title, xlabel, ylabel)` | Category comparison (regions, segments) | `charts/bar_<timestamp>.png` |
| `line_chart(data, title, xlabel, ylabel)` | Time trends (monthly revenue) | `charts/line_<timestamp>.png` |
| `pie_chart(data, title)` | Share of total (segment mix) | `charts/pie_<timestamp>.png` |
| `horizontal_bar_chart(data, title, xlabel, ylabel)` | Rankings (top products, top customers) | `charts/hbar_<timestamp>.png` |

**Chart titles are in English.** matplotlib does not render Persian correctly (letters disconnect, right-to-left fails). Persian is preserved in the LLM's textual explanation.

---

## Usage

```bash
python mvpmodel.py
```

Startup output:

```
Connecting to SQL Server...
Connected.
Months:  ['202601', '202602', '202603', '202604', '202605', '202606']
Regions: ['APAC', 'EU', 'LATAM', 'NA']

Type your question ('exit' or 'quit' to stop).

>>
```

Type a question; press Enter. Type `exit`, `quit`, `Ctrl+C`, or `Ctrl+D` to stop.

---

## Example Questions

**Working questions:**

```
what we sold last months
monthly revenue trend
which region sells most
compare EU to NA their customers
فروش در ماه 202606 به تفکیک منطقه
عملکرد ارسال در ماه 202606
top 10 products by revenue
```

**Expected behavior:**

| Question | Picks | Result |
|---|---|---|
| `monthly revenue trend` | `fetch_orders {}` → `monthly_comparison {}` → `line_chart` | Month-over-month % changes + trend line chart |
| `which region sells most` | `fetch_orders {}` → `region_sales_summary {}` → `bar_chart` | 4 regions with totals + bar chart |
| `فروش در ماه 202606 ...` | `fetch_orders {'month': 202606}` → `region_sales_summary {}` | Single-month snapshot (smaller numbers than all-months) |
| `عملکرد ارسال در ماه 202606` | `fetch_orders {'month': 202606}` → `shipping_performance {}` | Late % + averages (no chart — single-number result) |

**Questions the MVP cannot fully answer (v2 scope):**

- `compare EU to NA per product` — needs `top_products_by_region` (doesn't exist)
- `biggest customer in NA` — needs `top_customers` (doesn't exist)
- `how many customers per region` — needs `customer_count_by_region` (doesn't exist)
- `what we sold last months, AND what customers buy` — two questions in one; the pipeline answers one question per input

When asked these, the agent **honestly** reports what it can and can't answer, and suggests better phrasings.

---

## Design Decisions

### Why CSV as intermediate layer (not direct DataFrame)

- **Memory safety:** SQL filters/aggregates before writing, so CSVs stay small even with large DBs.
- **Debuggability:** You can inspect `data/orders.csv` to see exactly what the pandas function received.
- **Caching:** Multiple pandas functions can read the same CSV without re-querying.

### Why SQL does the aggregation (not pandas)

- **Performance:** `GROUP BY region` on millions of rows is faster in SQL Server than pandas.
- **Correctness:** SQL Server handles NULLs and data types natively; no pandas dtype surprises.

### Why the LLM never reads SQL directly

- **Safety:** No `exec()` of LLM output, no SQL injection risk. The LLM picks function names from a fixed registry; Python executes them with parameterized queries.

### Why validation lives in Python (not the prompt)

- **Reliability:** LLMs ignore instructions ~10-20% of the time, especially negative rules ("do NOT pass month X"). Python validation is deterministic.
- **Pattern:** LLM suggests → Python validates → Python corrects → Python tells LLM what it did.

### Why two LLM calls for selection (SQL + pandas) instead of one

- **Modularity:** Adding a new SQL function doesn't affect pandas choices; adding a pandas function doesn't affect SQL choices.
- **Restriction:** After step 1, only functions whose CSV is loaded are shown to step 2. This prevents the "pandas picks a function whose data isn't loaded" bug.

### Why `date.today()` as the time anchor

- **Honest relative dates:** "last month" means the real last calendar month, not an arbitrary value.
- **Fallback:** If the resolved month isn't in the DB, code falls back to the latest available and the answer discloses the fallback.
- **Open question:** The manager may prefer anchoring at the latest data month instead (so "last month" always returns data). Pending decision. Code change would be ~5 lines in `_anchors()`.

### Why charts use English labels

- **matplotlib limitations:** Persian text renders disconnected and right-to-left incorrectly without `arabic-reshaper` + `python-bidi`.
- **Reader clarity:** English chart titles work universally; Persian explanation carries the nuance.

---

## Issues Fixed During Development

### 1. `NA` region lost in CSV round-trip

**Symptom:** Region `NA` (North America) disappeared from results; totals were silently missing rows.

**Cause:** `pd.read_csv` default behavior treats the string `"NA"` as `NaN`.

**Fix:**
- Load with `keep_default_na=False, na_values=[""]` — only empty strings become NaN.
- Set `dtype={"month": "string", "opened_month": "string"}` to prevent pandas from auto-parsing 6-digit strings as dates.
- Column type in DB is `nvarchar(50)` not `int`, so `'NA'` is preserved end-to-end.

### 2. `month` column corrupted by SQL Server import wizard

**Symptom:** Values became `245409` (year 2454, month 09) instead of `202601`–`202606`.

**Cause:** The wizard detected `202601` as a date. SQL Server's date arithmetic interpreted `202601` days since `1900-01-01` → year 2454. A subsequent migration using `YEAR()*100+MONTH()` locked in the corrupted value.

**Fix:**
- Drop the corrupted tables and re-import.
- In the wizard's **Modify Columns**, force `month` to `nvarchar(10)`.
- Verify with `SELECT DISTINCT month FROM orders ORDER BY month` — must show `202601`..`202606`.

### 3. Empty results from month double-filtering

**Symptom:** `region_sales_summary` returned `{}` when the CSV had data.

**Cause:** SQL filtered `orders` to `month=202606`, then pandas also filtered to `month=202604` (a different wrong month chosen by LLM #2).

**Fix:**
- Pass `sql_month` as `month_hint` to `select_pandas`.
- If SQL filtered, **drop** `month` from pandas params (`pparams.pop("month", None)`).
- Validate any month the pandas layer still passes.

### 4. LLM picking `monthly_comparison` on single-month data

**Symptom:** `{"error": "حداقل دو ماه لازم است"}` for questions like "compare EU to NA" when SQL filtered to 1 month.

**Fix:** `MULTI_MONTH_PANDAS = {"monthly_comparison", "wholesale_customer_change"}` — removed from candidates when `sql_month is not None`.

### 5. LLM picking a month not in the DB (e.g. `202609`)

**Symptom:** SQL query returned 0 rows; empty results.

**Fix:** `_validate_month` — if the month isn't in `['202601', ..., '202606']`, replace with `months[-1]` and set `corrected_month` flag; `analyze_output` forces the Persian answer to disclose the fallback.

### 6. LLM mislabeling multi-month data as "for month X"

**Symptom:** Answer said "for month 202606" while data covered all 6 months.

**Fix:** `analyze_output` receives `month_filter` explicitly. Prompt enforces: if `month_filter is None`, say "across all available months"; if set, say "for month X".

### 7. `inventory_shortage` failing (missing columns)

**Symptom:** `{"error": "ستون‌های [...] لازم است"}` — needed `available` and `sold_qty`, which don't exist in the raw `inventory` table.

**Fix:** `fetch_inventory` SQL computes `available = on_hand - reserved` and joins `sold_qty` from paid orders. `inventory_shortage` handles the case where `available` is missing (backward compatible).

### 8. LLM function-choice ambiguity (`what we sold last months`)

**Symptom:** Picked `top_products` (product ranking) when user meant `monthly_comparison` (trend).

**Fix:** Function-choice guide in `select_pandas` prompt with explicit keyword → function mapping. Special line: `"what we sold last months" is a TREND question → monthly_comparison, NOT top_products`.

---

## Testing: Hallucination Resistance

Five adversarial tests passed (5/5). The agent does not fabricate numbers, reasons, or predictions.

| Test | Question | Correct behavior | Result |
|---|---|---|---|
| 1 | `فروش ماه 202606 به تفکیک منطقه` | Report exact region totals; note that units aren't specified | ✅ Correct — did not invent units |
| 2 | `فروش ماه گذشته چقدر بوده؟` (data ends 202606) | Fall back to latest, disclose "latest data is 202606" | ✅ Correct — disclosed fallback |
| 3 | `compare EU to NA AND why is NA lower?` | Answer comparison; refuse to explain "why" | ✅ Correct — refused reason, suggested next steps |
| 4 | `biggest customer in NA` | Detect that `wholesale_customer_change` doesn't rank customers; disclose limitation | ✅ Correct — exposed limitation, did not fabricate |
| 5 | `why did enterprise NA customers reduce 50% in 202606?` | Reject premise (no evidence of 50% drop); disclose missing NA filter and month filter | ✅ Correct — rejected all three false premises |

**Key anti-hallucination mechanisms:**

- `analyze_output` prompt: "Do not invent numbers. Do not add facts that are not in the result."
- `filter_line` in `analyze_output`: forces accurate description of what data covers.
- `corrected_month` note: forces disclosure when fallback occurred.
- Low temperature (0.2): deterministic behavior.

---

## Known Limitations

### Functional

1. **One question = one function.** Questions like "compare X and also analyze Y" will only answer the first part. Suggest splitting into separate questions.
2. **No region filter inside pandas functions.** `top_products` and similar do not accept `region="EU"`. The agent answers globally and discloses this.
3. **No customer name resolution.** Only `customer_id` is available; the LLM cannot say "Customer 33 is Ali" without additional data.
4. **Time anchor is `date.today()`.** "Last month" resolves to the real last month, which may be empty if the DB is a snapshot. Falls back to latest and discloses. **Pending manager decision** on whether to anchor at latest data month instead.

### Technical

5. **PowerShell/VS Code terminal glitches.** Long lines trigger `PSReadLine` errors in the VS Code integrated terminal. These are cosmetic; the Python app runs fine. Fix: use a plain PowerShell window or `cmd.exe` for the demo.
6. **No streaming output.** Each LLM call waits for the full response before printing. 4 calls × ~3 seconds = ~12 seconds per question.
7. **4 LLM calls per question.** Can be reduced to 3 by merging chart decision into `select_pandas`, but the current separation is clearer for debugging.

---

## Roadmap (v2)

### New pandas functions (high priority — user requests)

- `top_customers(region=None, month=None, n=10)` — biggest customers by revenue
- `top_products_by_region(month=None, n=5)` — per-region product rankings
- `customer_count_by_region()` — unique customer counts
- `average_order_value(region=None, month=None)` — AOV by segment/region

### Multi-step orchestration

Allow one question to trigger multiple pandas functions:

```
>> compare EU to NA per product
   → top_products_by_region('EU') AND top_products_by_region('NA')
   → combined analysis
```

Requires refactoring `run_one` to loop over multiple pandas functions and pass combined results to `analyze_output`.

### Time anchor decision

Wait for manager input:

- **Option A (current):** Real `date.today()`. Honest but may return empty for "last month".
- **Option B:** Anchor at latest data month. Always returns data; "last month" ≠ calendar last month.

Changing from A → B is ~5 lines in `_anchors()`.

### Chart enhancements

- Grouped/stacked bar charts (EU vs NA side-by-side)
- Multi-line trends
- Export to PDF for reports
- Persian labels via `arabic-reshaper` + `python-bidi` (if required)

### Data freshness

- Auto-refresh CSVs on a schedule (task scheduler) rather than per-question
- Support multiple databases / schemas
- Add `fx_to_usd_bp` usage for currency normalization

---

## Requirements

**`requirements.txt`:**

```txt
pandas>=3.0
mssql-python>=1.15
requests>=2.34
python-dotenv>=1.2
matplotlib>=3.8
```

**Minimum Python:** 3.10 (tested on 3.14).

**Database:** SQL Server 2019 or newer recommended (for `WITH` CTEs; basic queries work on 2012+).

**OS:** Windows (uses `os.startfile` for auto-open charts; on Linux/macOS this is a no-op — see `AUTO_OPEN_CHART`).

---

*End of README.*