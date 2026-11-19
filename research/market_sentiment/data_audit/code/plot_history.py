"""Plot retained daily price levels; no fitting, forecasts or return-performance claims."""
from pathlib import Path
import sqlite3
import datetime as dt
import json
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates


def plot(dataset):
    conn = sqlite3.connect(dataset / "market_research.sqlite")
    specs = [
        ("US.QQQ", 1, "QQQ: forward-adjusted price", "USD (adjusted)", "#2563eb"),
        ("SH.000001", 0, "SSE Composite", "Index points", "#059669"),
        ("SH.000688", 0, "STAR 50: from live publication", "Index points", "#7c3aed"),
        ("US..SOX", 0, "PHLX Semiconductor Index (SOX)", "Index points", "#d97706"),
    ]
    fig, axes = plt.subplots(2, 2, figsize=(14, 9))
    chart_inputs = []
    for ax, (symbol, autype, title, ylabel, color) in zip(axes.flat, specs):
        rows = conn.execute("SELECT local_date,close FROM research_daily WHERE symbol=? AND autype=? ORDER BY local_date",
                            (symbol, autype)).fetchall()
        values = {d: v for d, v in rows}
        if symbol == "US..SOX":
            # Empty plot markers, not invented prices; break line at provider gaps.
            for (day,) in conn.execute("SELECT local_date FROM unresolved_dates WHERE symbol=?", (symbol,)):
                values[day] = float("nan")
        ordered = sorted(values)
        ax.plot([dt.date.fromisoformat(x) for x in ordered],
                [values[x] for x in ordered], color=color, linewidth=0.9)
        ax.set_title(title, loc="left", fontsize=13, fontweight="bold")
        ax.set_ylabel(ylabel)
        ax.set_yscale("log")
        ax.grid(True, alpha=0.18, which="major")
        ax.xaxis.set_major_locator(mdates.YearLocator(2 if symbol == "SH.000688" else 5))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
        ax.spines[["top", "right"]].set_visible(False)
        ax.text(0.01, 0.02, "%s to %s | %s observed rows" %
                (rows[0][0], rows[-1][0], f"{len(rows):,}"),
                transform=ax.transAxes, fontsize=8, color="#475569")
        chart_inputs.append({"symbol": symbol, "autype": autype, "view": "research_daily",
                             "observations": len(rows), "first": rows[0][0], "last": rows[-1][0]})
    conn.close()
    fig.suptitle("Historical price data — moomoo research dataset v0.1", x=0.055, y=0.985,
                 ha="left", fontsize=18, fontweight="bold")
    fig.text(0.055, 0.945,
             "Current-vintage vendor prices • separate time ranges • logarithmic axes • not total returns",
             fontsize=10, color="#475569")
    fig.text(0.055, 0.018,
             "Known exclusions applied. SOX daily gaps remain empty. This figure is descriptive; no sentiment or forecasting claim.",
             fontsize=9, color="#475569")
    fig.tight_layout(rect=[0.02, 0.045, 0.99, 0.92], h_pad=3.0, w_pad=3.0)
    fig.savefig(dataset / "market_history_overview.png", dpi=160, facecolor="white")
    plt.close(fig)
    (dataset / "audit" / "chart_inputs.json").write_text(json.dumps(chart_inputs, indent=2))


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    plot(parser.parse_args().dataset)
