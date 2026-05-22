"""
wpp_kdp.py
─────────────────────────────────────────────────────────────────
WPP KDP Revenue Module — standalone module.

Reads kdp_snapshots from wpp.db and builds the KDP Revenue
section for the social analytics dashboard.

USAGE in social_dashboard.py:
    from wpp_kdp import build_kdp_revenue_html

DEPENDENCIES:
    pip install pandas python-dotenv
    wpp.db must be in the working directory with kdp_snapshots table populated.
    Use WPP_Analytics_Importer.html monthly to populate kdp_snapshots.
"""

import sqlite3
from pathlib import Path

import pandas as pd


# ─────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────

WPP_DB_FILE   = "wpp.db"
DB_CANDIDATES = [
    Path(WPP_DB_FILE),
    Path(__file__).parent / WPP_DB_FILE,
    Path.home() / "myapps" / "social-analytics-dashboard" / WPP_DB_FILE,
]


# ─────────────────────────────────────────────────────────────────
# DB helpers
# ─────────────────────────────────────────────────────────────────

def _find_db() -> Path | None:
    for candidate in DB_CANDIDATES:
        if candidate.exists():
            return candidate
    return None


# ─────────────────────────────────────────────────────────────────
# Data loader
# ─────────────────────────────────────────────────────────────────

def get_kdp_revenue_data() -> tuple:
    """Read kdp_snapshots from wpp.db.

    Returns:
        (by_book DataFrame, by_month DataFrame, grand_total float)
        Returns (None, None, None) if DB not found or table empty.
    """
    db_path = _find_db()
    if db_path is None:
        return None, None, None

    try:
        conn = sqlite3.connect(db_path)

        by_book = pd.read_sql_query("""
            SELECT b.title, b.book_key,
                   SUM(k.kindle_units)    AS kindle_units,
                   SUM(k.paperback_units) AS paperback_units,
                   SUM(k.ku_pages)        AS ku_pages,
                   ROUND(SUM(k.royalties_usd), 2) AS total_revenue
            FROM kdp_snapshots k
            JOIN books b ON k.book_key = b.book_key
            GROUP BY b.book_key
            ORDER BY total_revenue DESC
        """, conn)

        by_month = pd.read_sql_query("""
            SELECT snapshot_month,
                   ROUND(SUM(royalties_usd), 2) AS monthly_revenue,
                   SUM(kindle_units)  AS kindle_units,
                   SUM(ku_pages)      AS ku_pages
            FROM kdp_snapshots
            GROUP BY snapshot_month
            ORDER BY snapshot_month DESC
            LIMIT 12
        """, conn)

        total = pd.read_sql_query("""
            SELECT ROUND(SUM(royalties_usd), 2) AS total
            FROM kdp_snapshots
        """, conn)

        conn.close()
        grand_total = float(total.iloc[0]["total"]) if not total.empty else 0.0
        return by_book, by_month, grand_total

    except Exception as e:
        print(f"KDP revenue query error: {e}")
        return None, None, None


# ─────────────────────────────────────────────────────────────────
# HTML builder — called by social_dashboard.py
# ─────────────────────────────────────────────────────────────────

def build_kdp_revenue_html() -> str:
    """Build the KDP Book Sales section for the dashboard.

    Returns empty string if no data found — dashboard renders
    without the section rather than crashing.
    """
    by_book, by_month, grand_total = get_kdp_revenue_data()

    if by_book is None or by_book.empty:
        return ""

    book_rows = ""
    for _, r in by_book.iterrows():
        revenue = float(r.get("total_revenue", 0))
        kindle  = int(r.get("kindle_units", 0))
        pb      = int(r.get("paperback_units", 0))
        ku      = int(r.get("ku_pages", 0))
        book_rows += f"""
        <tr>
            <td>{r["title"]}</td>
            <td style="text-align:center">{kindle}</td>
            <td style="text-align:center">{pb}</td>
            <td style="text-align:center">{ku:,}</td>
            <td style="text-align:center"><strong style="color:#C9A84C">${revenue:.2f}</strong></td>
        </tr>"""

    month_rows = ""
    for _, r in by_month.head(6).iterrows():
        rev    = float(r.get("monthly_revenue", 0))
        kindle = int(r.get("kindle_units", 0))
        ku     = int(r.get("ku_pages", 0))
        month_rows += f"""
        <tr>
            <td>{r["snapshot_month"]}</td>
            <td style="text-align:center">{kindle}</td>
            <td style="text-align:center">{ku:,}</td>
            <td style="text-align:center"><strong style="color:#C9A84C">${rev:.2f}</strong></td>
        </tr>"""

    return f"""
    <h2 style="color:#C9A84C">KDP Book Sales
        <span style="font-size:12px;color:#AAB4C0;font-weight:normal;margin-left:12px">
        All-time · Updated via KDP Analytics Importer monthly</span>
    </h2>
    <div class="scoreboard-grid">
        <div class="scoreboard-card">
            <h2>Revenue by Book — All Time</h2>
            <table class="scoreboard-table">
                <thead>
                    <tr>
                        <th>Book</th>
                        <th style="text-align:center">Kindle</th>
                        <th style="text-align:center">PB</th>
                        <th style="text-align:center">KU Pages</th>
                        <th style="text-align:center">Revenue</th>
                    </tr>
                </thead>
                <tbody>{book_rows}</tbody>
                <tfoot>
                    <tr style="background:#0A1628">
                        <td colspan="4"><strong style="color:#C9A84C">TOTAL ALL TIME</strong></td>
                        <td style="text-align:center"><strong style="color:#C9A84C">${grand_total:.2f}</strong></td>
                    </tr>
                </tfoot>
            </table>
        </div>
        <div class="scoreboard-card">
            <h2>Monthly Revenue Trend</h2>
            <table class="scoreboard-table">
                <thead>
                    <tr>
                        <th>Month</th>
                        <th style="text-align:center">Kindle</th>
                        <th style="text-align:center">KU Pages</th>
                        <th style="text-align:center">Revenue</th>
                    </tr>
                </thead>
                <tbody>{month_rows}</tbody>
            </table>
        </div>
    </div>"""
