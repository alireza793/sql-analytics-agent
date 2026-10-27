"""
Chart layer for the MVP.

LLM decides what chart to make. Python (matplotlib) draws it.
No exec() of LLM-generated code — deterministic and safe.

Charts are saved to charts/ with timestamp in filename.
"""

import os
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # headless — no window, just file output
import matplotlib.pyplot as plt

CHART_DIR = Path(__file__).parent / "charts"
CHART_DIR.mkdir(exist_ok=True)


def _timestamp():
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _normalize_data(data):
    """
    Convert various result shapes to {label: numeric} for charting.

    Handles:
      - {"APAC": 12446, "EU": 15682}                     → {APAC: 12446, EU: 15682}
      - {"202601->202602": {"current": 42618, ...}, ...} → {202602: 42618, ...}
      - {"23": 18040, "29": 15642}                       → {23: 18040, 29: 15642}
    """
    if not isinstance(data, dict):
        return {}
    normalized = {}
    for k, v in data.items():
        if isinstance(v, (int, float)):
            normalized[str(k)] = float(v)
        elif isinstance(v, dict):
            value = None
            for key in ("current", "revenue", "value", "total", "count", "available", "sold_qty"):
                if key in v and isinstance(v[key], (int, float)):
                    value = float(v[key])
                    break
            if value is not None:
                label = str(k)
                # For "202601->202602" use just "202602" as the label
                if "->" in label:
                    label = label.split("->")[-1]
                normalized[label] = value
    return normalized


def bar_chart(data, title="Chart", xlabel="", ylabel="Value"):
    """Vertical bar chart — for comparing categories (regions, segments)."""
    normalized = _normalize_data(data)
    if not normalized:
        return None
    fig, ax = plt.subplots(figsize=(10, 6))
    ax.bar(list(normalized.keys()), list(normalized.values()), color="#4C72B0")
    ax.set_title(title, fontsize=14)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    plt.xticks(rotation=45, ha="right")
    plt.tight_layout()
    path = CHART_DIR / f"bar_{_timestamp()}.png"
    plt.savefig(path, dpi=100)
    plt.close(fig)
    return str(path)


def line_chart(data, title="Chart", xlabel="", ylabel="Value"):
    """Line chart — for trends over time (monthly revenue)."""
    normalized = _normalize_data(data)
    if not normalized:
        return None
    fig, ax = plt.subplots(figsize=(10, 6))
    ax.plot(list(normalized.keys()), list(normalized.values()),
            marker="o", color="#4C72B0", linewidth=2)
    ax.set_title(title, fontsize=14)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.grid(True, alpha=0.3)
    plt.xticks(rotation=45, ha="right")
    plt.tight_layout()
    path = CHART_DIR / f"line_{_timestamp()}.png"
    plt.savefig(path, dpi=100)
    plt.close(fig)
    return str(path)


def pie_chart(data, title="Chart"):
    """Pie chart — for share of total (segment distribution)."""
    normalized = _normalize_data(data)
    if not normalized:
        return None
    fig, ax = plt.subplots(figsize=(8, 8))
    ax.pie(list(normalized.values()), labels=list(normalized.keys()),
           autopct="%1.1f%%", startangle=90)
    ax.set_title(title, fontsize=14)
    plt.tight_layout()
    path = CHART_DIR / f"pie_{_timestamp()}.png"
    plt.savefig(path, dpi=100)
    plt.close(fig)
    return str(path)


def horizontal_bar_chart(data, title="Chart", xlabel="Value", ylabel=""):
    """Horizontal bar — for rankings (top products, top customers)."""
    normalized = _normalize_data(data)
    if not normalized:
        return None
    fig, ax = plt.subplots(figsize=(10, 6))
    ax.barh(list(normalized.keys()), list(normalized.values()), color="#4C72B0")
    ax.set_title(title, fontsize=14)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.invert_yaxis()  # top value at top
    plt.tight_layout()
    path = CHART_DIR / f"hbar_{_timestamp()}.png"
    plt.savefig(path, dpi=100)
    plt.close(fig)
    return str(path)


# Registry for the LLM
CHART_FUNCTIONS = {
    "bar_chart": {
        "description": "Bar chart for comparing values across categories (regions, segments, channels)",
        "params": {"title": "str", "xlabel": "str", "ylabel": "str"},
    },
    "line_chart": {
        "description": "Line chart for trends over time (monthly revenue, month-by-month changes)",
        "params": {"title": "str", "xlabel": "str", "ylabel": "str"},
    },
    "pie_chart": {
        "description": "Pie chart for share of total (segment distribution, channel mix)",
        "params": {"title": "str"},
    },
    "horizontal_bar_chart": {
        "description": "Horizontal bar chart for rankings (top products, top customers)",
        "params": {"title": "str", "xlabel": "str", "ylabel": "str"},
    },
}


def run_chart_function(name, data, title="Chart", xlabel="", ylabel="Value"):
    """Call the chart function by name and return the saved path (or None)."""
    if name not in CHART_FUNCTIONS:
        raise ValueError(f"Unknown chart function: {name}")
    fn = globals()[name]
    if name == "pie_chart":
        return fn(data, title)
    return fn(data, title, xlabel, ylabel)