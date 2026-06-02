"""
wpp_gumroad.py
─────────────────────────────────────────────────────────────────
WPP Gumroad Analytics Module — standalone module.

Fetches product sales and revenue from the Gumroad API.
Reads promotion posts from gumroad_posts in wpp.db and cross-
references their URLs against the main analytics dataframe to
pull views and engagement for each promotion post.

USAGE in social_dashboard.py:
    from wpp_gumroad import fetch_gumroad_data, load_gumroad_posts,
                            build_gumroad_revenue_html

DEPENDENCIES:
    pip install requests python-dotenv
    .env must contain: GUMROAD_ACCESS_TOKEN
    wpp.db must contain: gumroad_posts table
"""

import os
import re
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

GUMROAD_TOKEN = os.getenv("GUMROAD_ACCESS_TOKEN")
BASE_URL      = "https://api.gumroad.com/v2"
MAX_SALES_PAGES = 5   # 10 sales/page = up to 50 sales
WPP_DB_FILE   = "wpp.db"

PLATFORM_LABELS = {
    "X":                  "X",
    "Instagram_Static":   "Instagram (static)",
    "Instagram_Carousel": "Instagram (carousel)",
    "Facebook_WPP":       "Facebook WPP",
    "TPL":                "The Protocol Lab",
    "Substack":           "Substack",
}


# ─────────────────────────────────────────────────────────────────
# Gumroad API helpers
# ─────────────────────────────────────────────────────────────────

def _get(endpoint, params=None):
    p = {"access_token": GUMROAD_TOKEN}
    if params:
        p.update(params)
    r = requests.get(f"{BASE_URL}/{endpoint}", params=p, timeout=15)
    r.raise_for_status()
    return r.json()


def fetch_gumroad_data():
    """
    Returns dict:
      products           — list of {name, price_usd, sales_count, revenue_usd, url}
      sales              — list of {date, product_name, amount_usd, country}
      total_revenue      — float (USD, excluding refunds)
      total_sales        — int (excluding refunds)
      top_countries      — list of (country, count) sorted descending
      revenue_by_product — dict {product_name: revenue_usd}
    Returns None if token missing or API error.
    """
    if not GUMROAD_TOKEN:
        print("Gumroad: GUMROAD_ACCESS_TOKEN not set — skipping.")
        return None

    try:
        prod_resp = _get("products")
        products = []
        for p in prod_resp.get("products", []):
            products.append({
                "name":        p.get("name", ""),
                "price_usd":   (p.get("price") or 0) / 100.0,
                "sales_count": p.get("sales_count") or 0,
                "revenue_usd": float(p.get("revenue") or 0),
                "url":         f"https://gumroad.com/l/{p.get('permalink', '')}",
            })
        print(f"Gumroad: {len(products)} products fetched.")

        sales = []
        page_key = None
        for _ in range(MAX_SALES_PAGES):
            params = {}
            if page_key:
                params["page_key"] = page_key
            sale_resp = _get("sales", params)
            batch = sale_resp.get("sales", [])
            for s in batch:
                if s.get("refunded"):
                    continue
                raw_date = s.get("created_at", "")
                try:
                    date_str = datetime.strptime(
                        raw_date[:19], "%Y-%m-%dT%H:%M:%S"
                    ).strftime("%Y-%m-%d")
                except Exception:
                    date_str = raw_date[:10]
                sales.append({
                    "date":         date_str,
                    "product_name": s.get("product_name", ""),
                    "amount_usd":   (s.get("price") or 0) / 100.0,
                    "country":      s.get("ip_country") or "Unknown",
                })
            page_key = sale_resp.get("next_page_key")
            if not page_key or not batch:
                break

        print(f"Gumroad: {len(sales)} sales fetched.")

        total_revenue = sum(s["amount_usd"] for s in sales)
        total_sales   = len(sales)

        country_counts = defaultdict(int)
        for s in sales:
            country_counts[s["country"]] += 1
        top_countries = sorted(country_counts.items(), key=lambda x: x[1], reverse=True)[:10]

        revenue_by_product = defaultdict(float)
        for s in sales:
            revenue_by_product[s["product_name"]] += s["amount_usd"]

        return {
            "products":           products,
            "sales":              sales,
            "total_revenue":      total_revenue,
            "total_sales":        total_sales,
            "top_countries":      top_countries,
            "revenue_by_product": dict(revenue_by_product),
        }

    except Exception as e:
        print(f"Gumroad fetch error: {e}")
        return None


# ─────────────────────────────────────────────────────────────────
# DB post loader + analytics matcher
# ─────────────────────────────────────────────────────────────────

def load_gumroad_posts(df, normalize_url_fn):
    """
    Read gumroad_posts from wpp.db. Match post_url against analytics
    using two sources:
      - Main df     : Instagram posts (URL normalization)
      - x_analytics : X posts (direct x_url match)

    Facebook photo URLs (photo/?fbid=) cannot be matched because the
    Graph API returns a different post permalink with a different numeric
    ID — the photo fbid and post_id are unrelated numbers.
    Substack is not fetched and will not match.
    """
    db_path = None
    for candidate in [Path(WPP_DB_FILE), Path(__file__).parent / WPP_DB_FILE]:
        if candidate.exists():
            db_path = candidate
            break
    if db_path is None:
        print("Gumroad posts: wpp.db not found.")
        return []

    try:
        conn = sqlite3.connect(db_path)
        rows = conn.execute("""
            SELECT product_key, product_name, platform, post_type,
                   post_date, posted, post_url, gumroad_url, notes
            FROM gumroad_posts
            ORDER BY product_key, platform
        """).fetchall()

        # Build X analytics lookup: normalized x_url -> (impressions, likes, reposts)
        x_lookup = {}
        for x_url, impressions, likes, reposts in conn.execute(
            "SELECT x_url, impressions, likes, reposts FROM x_analytics"
        ).fetchall():
            if x_url:
                x_lookup[normalize_url_fn(str(x_url))] = {
                    "views": impressions or 0,
                    "likes": likes or 0,
                    "reposts": reposts or 0,
                }
        conn.close()
    except Exception as e:
        print(f"Gumroad posts DB error: {e}")
        return []

    # Build normalized URL lookup from main df (Instagram matches here)
    url_index  = {}
    fbid_index = {}   # fbid string -> df row (FB image posts)
    if df is not None and not df.empty and "url" in df.columns:
        for _, r in df.iterrows():
            raw = str(r.get("url", ""))
            if raw:
                url_index[normalize_url_fn(raw)] = r
            fbid = str(r.get("fbid", "")).strip()
            if fbid:
                fbid_index[fbid] = r

    posts = []
    for (product_key, product_name, platform, post_type,
         post_date, posted, post_url, gumroad_url, notes) in rows:

        post_url = post_url or ""
        norm     = normalize_url_fn(post_url) if post_url else ""

        # Try df match first (Instagram), then x_analytics
        # 1) Try URL normalization (Instagram)
        match_row = url_index.get(norm)
        # 2) Try fbid extraction (Facebook photo URLs)
        if match_row is None:
            fbid = re.search(r"fbid=(\d+)", post_url)
            if fbid:
                match_row = fbid_index.get(fbid.group(1))
        has_df_match = match_row is not None
        # 3) Try x_analytics table (X posts)
        x_match   = x_lookup.get(norm) if not has_df_match else None
        matched   = has_df_match or x_match is not None

        if has_df_match:
            views   = int(float(match_row.get("views", 0)))
            eng_pct = float(match_row.get("engagement_rate_percent", 0))
            likes   = int(float(match_row.get("likes", 0)))
            comments = int(float(match_row.get("comments", 0)))
        elif x_match is not None:
            views    = x_match["views"]
            likes    = x_match["likes"]
            comments = x_match["reposts"]   # reposts shown in comments slot
            eng_pct  = round(likes / views * 100, 2) if views > 0 else 0.0
        else:
            views = likes = comments = 0
            eng_pct = 0.0

        posts.append({
            "product_key":         product_key or "",
            "product_name":        product_name or "",
            "platform":            platform or "",
            "platform_label":      PLATFORM_LABELS.get(platform or "", platform or ""),
            "post_type":           post_type or "Static",
            "post_date":           post_date or "",
            "posted":              (posted or "N").upper(),
            "post_url":            post_url,
            "gumroad_url":         gumroad_url or "",
            "notes":               notes or "",
            "views":               views,
            "engagement_rate_pct": eng_pct,
            "likes":               likes,
            "comments":            comments,
            "matched":             matched,
            "source":              "x_analytics" if x_match else ("df" if has_df_match else "none"),
        })

    posted_count   = sum(1 for p in posts if p["posted"] == "Y")
    unposted_count = sum(1 for p in posts if p["posted"] == "N")
    matched_count  = sum(1 for p in posts if p["matched"])
    print(f"Gumroad posts: {len(posts)} total ({posted_count} posted, "
          f"{unposted_count} unposted, {matched_count} analytics matched).")
    return posts


# ─────────────────────────────────────────────────────────────────
# HTML builder
# ─────────────────────────────────────────────────────────────────

def build_gumroad_revenue_html(data, posts=None):
    """
    Returns HTML card for CEO tab.
    data  — from fetch_gumroad_data()
    posts — from load_gumroad_posts() — optional, adds promotion tracking
    """
    if not data:
        return ""

    total_rev   = data["total_revenue"]
    total_sales = data["total_sales"]
    products    = data["products"]
    sales       = data["sales"]
    top_ctry    = data["top_countries"]
    rev_by_prod = data["revenue_by_product"]

    # ── Products table ─────────────────────────────────────────
    prod_rows = ""
    for p in sorted(products, key=lambda x: x["revenue_usd"], reverse=True):
        rev = rev_by_prod.get(p["name"], p["revenue_usd"])
        prod_rows += f"""
            <tr>
                <td><a href="{p['url']}" target="_blank" style="color:#D7DEE8">{p['name']}</a></td>
                <td style="text-align:right">${p['price_usd']:.2f}</td>
                <td style="text-align:center">{p['sales_count']}</td>
                <td style="text-align:right"><strong style="color:#C9A84C">${rev:.2f}</strong></td>
            </tr>"""
    if not prod_rows:
        prod_rows = '<tr><td colspan="4" style="color:#AAB4C0;text-align:center">No products found</td></tr>'

    # ── Recent sales ───────────────────────────────────────────
    sale_rows = ""
    for s in sales[:10]:
        sale_rows += f"""
            <tr>
                <td style="font-size:12px;color:#AAB4C0">{s['date']}</td>
                <td style="font-size:12px">{s['product_name']}</td>
                <td style="text-align:right"><strong style="color:#C9A84C">${s['amount_usd']:.2f}</strong></td>
                <td style="font-size:12px;color:#AAB4C0">{s['country']}</td>
            </tr>"""
    if not sale_rows:
        sale_rows = '<tr><td colspan="4" style="color:#AAB4C0;text-align:center">No sales yet</td></tr>'

    # ── Top countries ──────────────────────────────────────────
    ctry_items = "".join(
        f'<span style="color:#D7DEE8;margin-right:14px">'
        f'{country} <strong style="color:#C9A84C">{count}</strong></span>'
        for country, count in top_ctry
    ) or "No data"

    # ── Promotion posts section ────────────────────────────────
    promo_html = ""
    if posts:
        # Group by product
        by_product = defaultdict(list)
        for p in posts:
            by_product[p["product_name"]].append(p)

        promo_blocks = ""
        for product_name, plist in by_product.items():
            prod_rev = rev_by_prod.get(product_name, 0.0)
            posted   = [p for p in plist if p["posted"] == "Y"]
            unposted = [p for p in plist if p["posted"] == "N"]

            # Posted rows with analytics
            post_rows = ""
            for p in posted:
                if p["matched"]:
                    views_label = "impr" if p.get("source") == "x_analytics" else "views"
                    views_cell  = f'<strong style="color:#C9A84C">{p["views"]:,}</strong> <span style="color:#6B7A8D;font-size:10px">{views_label}</span>'
                    eng_cell    = f'{p["engagement_rate_pct"]:.1f}%'
                else:
                    views_cell = '<span style="color:#6B7A8D">—</span>'
                    eng_cell   = '<span style="color:#6B7A8D">—</span>'

                link = (
                    f'<a href="{p["post_url"]}" target="_blank" '
                    f'style="color:#AAB4C0;font-size:11px">Open</a>'
                    if p["post_url"] else "—"
                )
                post_rows += f"""
                    <tr>
                        <td style="font-size:12px;white-space:nowrap">{p['platform_label']}</td>
                        <td style="font-size:11px;color:#AAB4C0">{p['post_date']}</td>
                        <td style="text-align:center">{views_cell}</td>
                        <td style="text-align:center">{eng_cell}</td>
                        <td style="text-align:center">{link}</td>
                    </tr>"""

            # Unposted badges
            unposted_html = ""
            if unposted:
                badges = "".join(
                    f'<span style="background:rgba(255,107,107,0.1);border:1px solid rgba(255,107,107,0.3);'
                    f'color:#FF6B6B;font-size:10px;border-radius:4px;padding:2px 7px;margin:2px 3px 2px 0;'
                    f'display:inline-block">{p["platform_label"]}</span>'
                    for p in unposted
                )
                unposted_html = f"""
                <div style="margin-top:8px;font-size:11px;color:#AAB4C0">
                    Still needed: {badges}
                </div>"""

            rev_badge = (
                f'<span style="color:#C9A84C;font-size:12px;font-weight:bold">'
                f'${prod_rev:.2f} revenue</span>'
                if prod_rev > 0 else
                '<span style="color:#6B7A8D;font-size:12px">No sales yet</span>'
            )

            promo_blocks += f"""
            <div style="margin-bottom:18px;padding-bottom:18px;border-bottom:1px solid rgba(255,255,255,.06)">
                <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:8px">
                    <strong style="font-size:13px;color:#FFFFFF">{product_name}</strong>
                    {rev_badge}
                </div>
                <table class="scoreboard-table" style="width:100%">
                    <thead><tr>
                        <th>Platform</th><th>Date</th>
                        <th style="text-align:center">Views</th>
                        <th style="text-align:center">Eng %</th>
                        <th style="text-align:center">Link</th>
                    </tr></thead>
                    <tbody>{post_rows or '<tr><td colspan="5" style="color:#6B7A8D;font-size:12px">No posts yet</td></tr>'}</tbody>
                </table>
                {unposted_html}
            </div>"""

        promo_html = f"""
        <h3 style="font-size:12px;color:#AAB4C0;text-transform:uppercase;letter-spacing:.06em;margin:20px 0 12px">
            Promotion Posts — Views &amp; Revenue by Product
        </h3>
        {promo_blocks}"""

    return f"""
    <div class="scoreboard-card" style="margin-bottom:20px">
        <h2 style="margin-top:0;font-size:15px">Gumroad
            <span style="font-size:13px;color:#AAB4C0;font-weight:normal;margin-left:10px">
                {total_sales} sales &nbsp;&middot;&nbsp;
                <strong style="color:#C9A84C">${total_rev:.2f}</strong> total revenue
            </span>
        </h2>

        <table class="scoreboard-table" style="width:100%;margin-bottom:20px">
            <thead><tr>
                <th>Product</th>
                <th style="text-align:right">Price</th>
                <th style="text-align:center">Sales</th>
                <th style="text-align:right">Revenue</th>
            </tr></thead>
            <tbody>{prod_rows}</tbody>
        </table>

        {promo_html}

        <h3 style="font-size:12px;color:#AAB4C0;text-transform:uppercase;letter-spacing:.06em;margin:16px 0 8px">
            Recent Sales
        </h3>
        <table class="scoreboard-table" style="width:100%;margin-bottom:16px">
            <thead><tr>
                <th>Date</th><th>Product</th>
                <th style="text-align:right">Amount</th><th>Country</th>
            </tr></thead>
            <tbody>{sale_rows}</tbody>
        </table>

        <div style="font-size:12px;color:#AAB4C0;margin-top:4px">
            <strong style="color:#AAB4C0">Top buyer countries:</strong>&nbsp; {ctry_items}
        </div>
    </div>"""
