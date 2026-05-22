"""
wpp_x_importer.py
─────────────────────────────────────────────────────────────────
WPP X Analytics Importer — standalone module.

Replaces the manual X_BOOK_MAP / X_PILLAR_MAP in the HTML importer.
Matches X CSV post IDs against x_url values already stored in the
content table, so no manual map maintenance is ever needed.

USAGE:
    # Import and run from python
    python wpp_x_importer.py account_analytics_content_DATE_DATE.csv

    # Or import into social_dashboard.py
    from wpp_x_importer import import_x_analytics, build_x_performance_html

WHAT IT DOES:
    1. Reads the X CSV exported from x.com/analytics
    2. Builds a lookup of post_id → (book_key, content_pillar) from wpp.db
       content table — no hardcoded map needed
    3. Matches each CSV row by post ID extracted from the Post Link URL
    4. Reports unmatched posts (posts not yet in content table)
    5. DELETEs existing rows for the snapshot_year then INSERTs fresh data
    6. Writes changes directly to wpp.db — no DB Browser paste needed

COLUMNS READ FROM X CSV:
    Post Link, Impressions, Likes, Reposts, Profile visits, URL Clicks

TABLE WRITTEN: x_analytics
    snapshot_date, x_url, book_key, content_pillar,
    impressions, likes, reposts, profile_visits, link_clicks, snapshot_year
"""

import re
import sys
import sqlite3
import argparse
from pathlib import Path
from datetime import date

import pandas as pd


# ─────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────

WPP_DB_FILE = "wpp.db"

# Candidate paths to search for wpp.db
DB_CANDIDATES = [
    Path(WPP_DB_FILE),
    Path(__file__).parent / WPP_DB_FILE,
    Path.home() / "myapps" / "social-analytics-dashboard" / WPP_DB_FILE,
]


# ─────────────────────────────────────────────────────────────────
# DB helpers
# ─────────────────────────────────────────────────────────────────

def find_db() -> Path | None:
    for candidate in DB_CANDIDATES:
        if candidate.exists():
            return candidate
    return None


def get_db_connection() -> sqlite3.Connection:
    db_path = find_db()
    if db_path is None:
        searched = "\n  ".join(str(c) for c in DB_CANDIDATES)
        raise FileNotFoundError(
            f"wpp.db not found. Searched:\n  {searched}\n"
            "Run from the social-analytics-dashboard directory."
        )
    return sqlite3.connect(db_path)


# ─────────────────────────────────────────────────────────────────
# Post ID extraction
# ─────────────────────────────────────────────────────────────────

def extract_post_id(url: str) -> str:
    """Extract the numeric post ID from an X/Twitter status URL."""
    if not url:
        return ""
    match = re.search(r"/status/(\d+)", str(url))
    return match.group(1) if match else ""


def normalize_x_url(url: str) -> str:
    """Normalize X URL to canonical form for DB matching."""
    match = re.search(r"(?:twitter|x)\.com/([^/?]+)/status/(\d+)", str(url), re.IGNORECASE)
    if match:
        return f"https://x.com/{match.group(1)}/status/{match.group(2)}"
    return str(url).strip().rstrip("/")


# ─────────────────────────────────────────────────────────────────
# Build lookup from content table
# ─────────────────────────────────────────────────────────────────

def build_post_lookup(conn: sqlite3.Connection) -> dict:
    """
    Build a dict of post_id → {book_key, content_pillar, x_url}
    from the content table. This replaces the hardcoded X_BOOK_MAP
    and X_PILLAR_MAP in the HTML importer entirely.
    """
    df = pd.read_sql_query("""
        SELECT
            c.x_url,
            c.book_key,
            c.content_pillar
        FROM content c
        WHERE c.x_url IS NOT NULL
          AND c.x_url != ''
    """, conn)

    lookup = {}
    for _, row in df.iterrows():
        post_id = extract_post_id(str(row["x_url"]))
        if post_id:
            lookup[post_id] = {
                "book_key":       str(row["book_key"]),
                "content_pillar": str(row["content_pillar"] or ""),
                "x_url":          normalize_x_url(str(row["x_url"])),
            }

    print(f"DB lookup built: {len(lookup)} X posts found in content table.")
    return lookup


# ─────────────────────────────────────────────────────────────────
# CSV reader
# ─────────────────────────────────────────────────────────────────

def read_x_csv(csv_path: str | Path) -> pd.DataFrame:
    """
    Read the X analytics CSV exported from x.com/analytics.
    Handles both 'Profile visits' and 'Profile Visits' column name variants.
    """
    df = pd.read_csv(csv_path, skiprows=0)

    # Normalize column names — X sometimes ships different capitalizations
    col_map = {}
    for col in df.columns:
        lower = col.strip().lower()
        if lower == "post link":
            col_map[col] = "Post Link"
        elif lower == "impressions":
            col_map[col] = "Impressions"
        elif lower == "likes":
            col_map[col] = "Likes"
        elif lower == "reposts":
            col_map[col] = "Reposts"
        elif lower in ("profile visits", "profile_visits"):
            col_map[col] = "Profile Visits"
        elif lower in ("url clicks", "url_clicks"):
            col_map[col] = "URL Clicks"

    df = df.rename(columns=col_map)

    required = ["Post Link", "Impressions"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(
            f"X CSV missing required columns: {missing}\n"
            f"Found columns: {list(df.columns)}"
        )

    df = df.dropna(subset=["Post Link"])
    df = df[df["Post Link"].str.strip() != ""]
    print(f"X CSV loaded: {len(df)} rows from {Path(csv_path).name}")
    return df


# ─────────────────────────────────────────────────────────────────
# Core import logic
# ─────────────────────────────────────────────────────────────────

def import_x_analytics(
    csv_path: str | Path,
    snapshot_year: int | None = None,
    dry_run: bool = False,
) -> dict:
    """
    Main entry point. Reads the X CSV, matches posts against wpp.db,
    and writes to x_analytics.

    Args:
        csv_path:      Path to the X account_analytics_content CSV.
        snapshot_year: Year to tag rows with. Defaults to current year.
        dry_run:       If True, print SQL but do not write to DB.

    Returns:
        dict with keys: matched, unmatched, inserted, unmatched_ids
    """
    if snapshot_year is None:
        snapshot_year = date.today().year

    today_str = date.today().isoformat()

    conn = get_db_connection()
    lookup = build_post_lookup(conn)
    df = read_x_csv(csv_path)

    matched_rows = []
    unmatched_ids = []

    for _, row in df.iterrows():
        post_link = str(row.get("Post Link", "")).strip()
        post_id   = extract_post_id(post_link)

        impressions    = _safe_int(row.get("Impressions"))
        likes          = _safe_int(row.get("Likes"))
        reposts        = _safe_int(row.get("Reposts"))
        profile_visits = _safe_int(row.get("Profile Visits"))
        link_clicks    = _safe_int(row.get("URL Clicks"))

        if post_id and post_id in lookup:
            info = lookup[post_id]
            matched_rows.append({
                "snapshot_date":  today_str,
                "x_url":          normalize_x_url(post_link),
                "book_key":       info["book_key"],
                "content_pillar": info["content_pillar"],
                "impressions":    impressions,
                "likes":          likes,
                "reposts":        reposts,
                "profile_visits": profile_visits,
                "link_clicks":    link_clicks,
                "snapshot_year":  snapshot_year,
            })
        else:
            unmatched_ids.append({
                "post_id":     post_id or "(no ID)",
                "post_link":   post_link,
                "impressions": impressions,
            })

    # ── Report ──────────────────────────────────────────────────
    print(f"\n{'─'*50}")
    print(f"X Analytics Import — {today_str} — year {snapshot_year}")
    print(f"  Matched:   {len(matched_rows)}")
    print(f"  Unmatched: {len(unmatched_ids)}")

    if unmatched_ids:
        print(f"\n  ⚠️  Unmatched posts (not yet in content table):")
        for u in unmatched_ids:
            print(f"     {u['post_id']:>22}  {u['impressions']:>6} impressions  {u['post_link']}")
        print(
            "\n  To track these posts: add their x_url to the content table\n"
            "  in DB Browser (UPDATE content SET x_url=... WHERE ...).\n"
            "  Re-run this importer and they will match automatically."
        )

    if not matched_rows:
        print("\n  Nothing to write — no matched posts.")
        conn.close()
        return {"matched": 0, "unmatched": len(unmatched_ids), "inserted": 0, "unmatched_ids": unmatched_ids}

    # ── Build SQL ────────────────────────────────────────────────
    matched_df = pd.DataFrame(matched_rows)

    delete_clauses = " OR ".join(
        f"x_url = '{r['x_url']}'"
        for r in matched_rows
    )
    delete_sql = (
        f"DELETE FROM x_analytics\n"
        f"WHERE snapshot_year = {snapshot_year}\n"
        f"AND ({delete_clauses});"
    )

    value_rows = ", ".join(
        f"('{r['snapshot_date']}', '{r['x_url']}', '{r['book_key']}', "
        f"'{r['content_pillar']}', {r['impressions']}, {r['likes']}, "
        f"{r['reposts']}, {r['profile_visits']}, {r['link_clicks']}, "
        f"{r['snapshot_year']})"
        for r in matched_rows
    )
    insert_sql = (
        "INSERT INTO x_analytics\n"
        "(snapshot_date, x_url, book_key, content_pillar,\n"
        " impressions, likes, reposts, profile_visits, link_clicks, snapshot_year)\n"
        f"VALUES {value_rows};"
    )

    if dry_run:
        print("\n── DRY RUN — SQL that would be executed ──")
        print(delete_sql)
        print()
        print(insert_sql)
        conn.close()
        return {
            "matched": len(matched_rows),
            "unmatched": len(unmatched_ids),
            "inserted": 0,
            "unmatched_ids": unmatched_ids,
        }

    # ── Execute ──────────────────────────────────────────────────
    try:
        cursor = conn.cursor()
        cursor.executescript(f"{delete_sql}\n{insert_sql}")
        conn.commit()
        print(f"\n  ✅ Written to wpp.db — {len(matched_rows)} rows upserted.")
    except Exception as e:
        conn.rollback()
        print(f"\n  ❌ DB write failed: {e}")
        raise
    finally:
        conn.close()

    return {
        "matched":       len(matched_rows),
        "unmatched":     len(unmatched_ids),
        "inserted":      len(matched_rows),
        "unmatched_ids": unmatched_ids,
    }


# ─────────────────────────────────────────────────────────────────
# build_x_performance_html — drop-in replacement for social_dashboard.py
# ─────────────────────────────────────────────────────────────────

def build_x_performance_html(content_map=None) -> str:
    """
    Build the X Performance section HTML for the dashboard.
    Reads directly from wpp.db x_analytics + content tables.
    content_map arg is accepted for compatibility but not used.
    """
    try:
        conn = get_db_connection()
    except FileNotFoundError:
        return ""

    try:
        x_data = pd.read_sql_query("""
            SELECT
                xa.snapshot_date,
                xa.x_url,
                b.title         AS book_or_offer,
                xa.content_pillar,
                xa.impressions  AS x_views,
                xa.likes        AS x_likes,
                xa.reposts      AS x_reposts,
                xa.profile_visits,
                xa.link_clicks
            FROM x_analytics xa
            LEFT JOIN books b ON xa.book_key = b.book_key
            ORDER BY xa.impressions DESC
        """, conn)

        x_content = pd.read_sql_query("""
            SELECT c.x_url, b.title AS book_or_offer,
                   c.content_pillar, c.short_num, c.post_date
            FROM content c
            JOIN books b ON c.book_key = b.book_key
            WHERE c.x_url IS NOT NULL AND c.x_url != ''
        """, conn)

        conn.close()
        x_data    = x_data.fillna("")
        x_content = x_content.fillna("")

    except Exception as e:
        print(f"X analytics query error: {e}")
        return ""

    if x_data.empty and x_content.empty:
        return """
    <h2 style="color:#1DA1F2">X (@wpprotocols) Performance</h2>
    <div class="scoreboard-card" style="border-color:#1DA1F2">
        <p style="color:#AAB4C0;font-size:13px">No X analytics yet.
        Run: python wpp_x_importer.py &lt;csv_file&gt;</p>
    </div>"""

    if x_data.empty:
        untracked = len(x_content)
        return f"""
    <h2 style="color:#1DA1F2">X (@wpprotocols) Performance
        <span style="font-size:12px;color:#AAB4C0;font-weight:normal;margin-left:12px">
        {untracked} X posts live — run: python wpp_x_importer.py &lt;csv_file&gt;</span>
    </h2>
    <div class="scoreboard-card" style="border-color:#1DA1F2">
        <p style="color:#AAB4C0;font-size:13px">
        X posts are live but analytics not yet imported.
        Run: <code>python wpp_x_importer.py account_analytics_content_DATE.csv</code>
        </p>
    </div>"""

    total_views   = int(x_data["x_views"].astype(int).sum())
    total_likes   = int(x_data["x_likes"].astype(int).sum())
    total_reposts = int(x_data["x_reposts"].astype(int).sum())

    rows_html = ""
    for _, r in x_data.iterrows():
        views   = int(r.get("x_views",   0))
        likes   = int(r.get("x_likes",   0))
        reposts = int(r.get("x_reposts", 0))
        eng     = round((likes + reposts) / views * 100, 1) if views > 0 else 0.0
        x_url   = str(r.get("x_url", ""))

        rows_html += f"""
        <tr>
            <td>{r.get("book_or_offer","")}</td>
            <td>{r.get("content_pillar","")}</td>
            <td style="text-align:center">{views:,}</td>
            <td style="text-align:center">{likes}</td>
            <td style="text-align:center">{reposts}</td>
            <td style="text-align:center">{eng}%</td>
            <td style="text-align:center;font-size:11px">{r.get("snapshot_date","")}</td>
            <td><a href="{x_url}" target="_blank" style="color:#1DA1F2">Open ↗</a></td>
        </tr>"""

    return f"""
    <h2 style="color:#1DA1F2">X (@wpprotocols) Performance
        <span style="font-size:12px;color:#AAB4C0;font-weight:normal;margin-left:12px">
        Update: python wpp_x_importer.py &lt;csv&gt; · runs every Monday</span>
    </h2>
    <div class="table-wrap" style="border:1px solid #1DA1F2;border-radius:14px;margin-bottom:30px">
        <table style="min-width:800px">
            <thead>
                <tr>
                    <th>Book</th>
                    <th>Pillar</th>
                    <th style="text-align:center">Impressions</th>
                    <th style="text-align:center">Likes</th>
                    <th style="text-align:center">Reposts</th>
                    <th style="text-align:center">Eng %</th>
                    <th style="text-align:center">Snapshot</th>
                    <th>Link</th>
                </tr>
            </thead>
            <tbody>{rows_html}</tbody>
            <tfoot>
                <tr style="background:#101F36">
                    <td colspan="2"><strong style="color:#1DA1F2">TOTALS</strong></td>
                    <td style="text-align:center"><strong>{total_views:,}</strong></td>
                    <td style="text-align:center"><strong>{total_likes}</strong></td>
                    <td style="text-align:center"><strong>{total_reposts}</strong></td>
                    <td colspan="3"></td>
                </tr>
            </tfoot>
        </table>
    </div>"""


# ─────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────

def _safe_int(value) -> int:
    try:
        if pd.isna(value):
            return 0
        return int(float(value))
    except Exception:
        return 0


# ─────────────────────────────────────────────────────────────────
# CLI entry point
# ─────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="WPP X Analytics Importer — matches X CSV against wpp.db content table."
    )
    parser.add_argument(
        "csv",
        help="Path to X analytics CSV (account_analytics_content_DATE_DATE.csv)",
    )
    parser.add_argument(
        "--year",
        type=int,
        default=date.today().year,
        help=f"Snapshot year (default: {date.today().year})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print SQL without writing to DB",
    )

    args = parser.parse_args()

    result = import_x_analytics(
        csv_path=args.csv,
        snapshot_year=args.year,
        dry_run=args.dry_run,
    )

    print(f"\n{'─'*50}")
    print("Done.")
    print(f"  Matched and written: {result['inserted']}")
    print(f"  Unmatched (not in content table): {result['unmatched']}")
    if result["unmatched"] > 0:
        print("  → Add x_url to those content rows in DB Browser, then re-run.")
    print("\nNow run: python social_dashboard.py")


if __name__ == "__main__":
    main()
