import pandas as pd

# ---------------------------------------------------------------------------
# Recommended loader: keep "na"/"NA" as a real region value
# ---------------------------------------------------------------------------
def load_data(path, **kwargs):
    """Load CSV without converting the literal string 'na' to NaN."""
    return pd.read_csv(
        path,
        keep_default_na=False,
        na_values=["", "NULL", "null", "None", "NaN", "nan"],
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Helper: make sure a legit region 'na' is never silently dropped
# ---------------------------------------------------------------------------
def _with_region_na(data, fill="na"):
    data = data.copy()
    if "region" in data.columns:
        data["region"] = data["region"].fillna(fill)
    return data


def region_sales_summary(df, month=None):
    """جمع فروش به تفکیک منطقه"""
    data = df if month is None else df[df["month"] == month]
    if "region" not in data.columns or "revenue" not in data.columns:
        return {"error": "ستون‌های region و revenue لازم است"}
    data = _with_region_na(data)
    return data.groupby("region", dropna=False)["revenue"].sum().round(2).to_dict()


def monthly_comparison(df, region=None):
    """مقایسه فروش بین ماه‌ها"""
    data = df if region is None else df[df["region"] == region]
    monthly = data.groupby("month")["revenue"].sum().sort_index()
    if len(monthly) < 2:
        return {"error": "حداقل دو ماه لازم است"}
    result = {}
    months = monthly.index.tolist()
    for i in range(1, len(months)):
        prev, curr = months[i - 1], months[i]
        change = monthly[curr] - monthly[prev]
        pct = (change / monthly[prev] * 100) if monthly[prev] != 0 else None
        result[f"{prev}->{curr}"] = {
            "prev": round(float(monthly[prev]), 2),
            "current": round(float(monthly[curr]), 2),
            "change": round(float(change), 2),
            "change_pct": round(float(pct), 2) if pct is not None else "N/A",
        }
    return result


def top_products(df, month=None, n=10):
    """n محصول برتر بر اساس فروش"""
    data = df if month is None else df[df["month"] == month]
    top = data.groupby("product_id")["revenue"].sum().nlargest(n)
    return {int(k): round(float(v), 2) for k, v in top.to_dict().items()}


def wholesale_customer_change(df):
    """تغییر خرید مشتریان عمده (خلاصه)"""
    wholesale = df[df["segment"].isin(["enterprise", "smb"])]
    if wholesale.empty:
        return {"error": "مشتری عمده‌ای یافت نشد"}

    result = []
    for cust_id, group in wholesale.groupby("customer_id"):
        months = group.groupby("month")["revenue"].sum().sort_index()
        if len(months) >= 2:
            change = months.iloc[-1] - months.iloc[0]
            result.append({
                "customer_id": int(cust_id),
                "prev": round(float(months.iloc[0]), 2),
                "current": round(float(months.iloc[-1]), 2),
                "change": round(float(change), 2),
            })

    decreased = [r for r in result if r["change"] < 0]
    increased = [r for r in result if r["change"] > 0]

    return {
        "total_wholesale_customers": len(result),
        "decreased_count": len(decreased),
        "increased_count": len(increased),
        "total_decrease": round(sum(r["change"] for r in decreased), 2),
        "total_increase": round(sum(r["change"] for r in increased), 2),
        "top_5_decreased": sorted(decreased, key=lambda x: x["change"])[:5],
        "top_5_increased": sorted(increased, key=lambda x: x["change"], reverse=True)[:5],
    }


def inventory_shortage(df):
    """محصولاتی که موجودی کمتر از فروش بوده (خلاصه)"""
    required = ["product_id", "region", "available", "sold_qty"]
    if not all(c in df.columns for c in required):
        return {"error": f"ستون‌های {required} لازم است"}

    df = _with_region_na(df)
    df["shortage"] = df["sold_qty"] - df["available"]
    shortage = df[df["shortage"] > 0]

    if shortage.empty:
        return {"message": "هیچ کمبودی وجود ندارد"}

    return {
        "total_shortage_count": len(shortage),
        "total_shortage_units": int(shortage["shortage"].sum()),
        "top_5_shortages": shortage.nlargest(5, "shortage")[
            ["product_id", "region", "available", "sold_qty", "shortage"]
        ].round(2).to_dict(orient="records"),
    }


def shipping_performance(df, month=None):
    """درصد ارسال دیر"""
    data = df if month is None else df[df["month"] == month]
    late = (data["ship_days"] > data["promised_days"]).sum()
    total = len(data)
    return {
        "total_orders": total,
        "late_orders": int(late),
        "late_pct": round(float(late / total * 100), 2) if total > 0 else 0,
        "avg_ship_days": round(float(data["ship_days"].mean()), 2),
        "avg_promised_days": round(float(data["promised_days"].mean()), 2),
    }


def returns_analysis(df):
    """تحلیل مرجوعی"""
    return {
        "total_returns": len(df),
        "total_units": int(df["returned_qty"].sum()) if "returned_qty" in df.columns else 0,
        "by_reason": df["reason"].value_counts().to_dict(),
    }


def support_performance(df):
    """عملکرد پشتیبانی"""
    result = {
        "total_tickets": len(df),
        "avg_first_response": round(float(df["first_response_hours"].mean()), 2),
        "avg_resolution": round(float(df["resolution_hours"].mean()), 2)
        if "resolution_hours" in df.columns else None,
    }
    if "severity" in df.columns:
        result["by_severity"] = df["severity"].value_counts().to_dict()
    return result


def segment_analysis(df, month=None):
    """فروش به تفکیک سگمنت"""
    data = df if month is None else df[df["month"] == month]
    return data.groupby("segment")["revenue"].sum().round(2).to_dict()


# دیکشنری برای LLM
AVAILABLE_FUNCTIONS = {
    "region_sales_summary": {
        "description": "جمع فروش به تفکیک منطقه",
        "params": {"month": "int (اختیاری)"},
    },
    "monthly_comparison": {
        "description": "مقایسه فروش بین ماه‌ها",
        "params": {"region": "str (اختیاری)"},
    },
    "top_products": {
        "description": "محصولات برتر بر اساس فروش",
        "params": {"month": "int (اختیاری)", "n": "int (پیش‌فرض 10)"},
    },
    "wholesale_customer_change": {
        "description": "تغییر خرید مشتریان عمده (خلاصه)",
        "params": {},
    },
    "inventory_shortage": {
        "description": "کمبود موجودی انبار (خلاصه)",
        "params": {},
    },
    "shipping_performance": {
        "description": "عملکرد ارسال (دیرکرد)",
        "params": {"month": "int (اختیاری)"},
    },
    "returns_analysis": {
        "description": "تحلیل مرجوعی",
        "params": {},
    },
    "support_performance": {
        "description": "عملکرد پشتیبانی",
        "params": {},
    },
    "segment_analysis": {
        "description": "فروش به تفکیک سگمنت",
        "params": {"month": "int (اختیاری)"},
    },
}