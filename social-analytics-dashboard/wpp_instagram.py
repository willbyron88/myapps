"""
wpp_instagram.py
─────────────────────────────────────────────────────────────────
WPP Instagram Analytics Module — standalone module.

USAGE in social_dashboard.py:
    from wpp_instagram import fetch_instagram_rows

DEPENDENCIES:
    pip install requests pandas python-dotenv
    .env must contain: IG_ACCESS_TOKEN, IG_USER_ID
"""

import os

import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv()

# ─────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────

IG_ACCESS_TOKEN = os.getenv("IG_ACCESS_TOKEN_WPP")
IG_USER_ID      = os.getenv("IG_USER_ID")
IG_BASE_URL     = "https://graph.instagram.com/v21.0"


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


def _truncate_text(text, max_len=145) -> str:
    text = str(text or "").replace("\n", " ").strip()
    return text[:max_len] + "..." if len(text) > max_len else text


def _engagement_rate(views, likes, comments, shares=0, saves=0) -> float:
    views = _safe_int(views)
    if views <= 0:
        return 0.0
    total = _safe_int(likes) + _safe_int(comments) + _safe_int(shares) + _safe_int(saves)
    return round((total / views) * 100, 2)


# ─────────────────────────────────────────────────────────────────
# API helpers
# ─────────────────────────────────────────────────────────────────

def ig_get_json(url: str, params: dict) -> dict:
    response = requests.get(url, params=params, timeout=30)
    if response.status_code != 200:
        print(f"\nInstagram API error — URL: {url} — Status: {response.status_code}")
        print(response.text)
    response.raise_for_status()
    return response.json()


def get_instagram_recent_media(limit: int = 50) -> list[dict]:
    if not IG_ACCESS_TOKEN or not IG_USER_ID:
        print("Skipping Instagram: missing IG_ACCESS_TOKEN or IG_USER_ID in .env.")
        return []

    url = f"{IG_BASE_URL}/{IG_USER_ID}/media"
    params = {
        "fields": (
            "id,caption,media_type,media_product_type,"
            "timestamp,permalink,like_count,comments_count"
        ),
        "limit": limit,
        "access_token": IG_ACCESS_TOKEN,
    }
    data = ig_get_json(url, params)
    return data.get("data", [])


def get_instagram_insights(media_id: str) -> dict:
    url = f"{IG_BASE_URL}/{media_id}/insights"
    params = {
        "metric": "views,reach,likes,comments,shares,saved,total_interactions",
        "access_token": IG_ACCESS_TOKEN,
    }
    data = ig_get_json(url, params)
    results = {}

    for item in data.get("data", []):
        metric_name = item.get("name")
        values = item.get("values", [])
        if not metric_name or not values:
            continue
        raw_value = values[-1].get("value", 0)
        if isinstance(raw_value, dict):
            results[metric_name] = sum(_safe_int(v) for v in raw_value.values())
        else:
            results[metric_name] = _safe_int(raw_value)

    return results


# ─────────────────────────────────────────────────────────────────
# Main fetcher — called by social_dashboard.py
# ─────────────────────────────────────────────────────────────────

def fetch_instagram_rows(limit: int = 50) -> list[dict]:
    """Fetch all Instagram media rows for the analytics dataframe."""
    rows = []
    media_items = get_instagram_recent_media(limit=limit)

    for item in media_items:
        media_id = item.get("id")
        caption  = _truncate_text(item.get("caption", ""))

        try:
            insights = get_instagram_insights(media_id)
        except Exception as e:
            print(f"Could not fetch Instagram insights for media {media_id}: {e}")
            insights = {}

        views    = _safe_int(insights.get("views"))    or _safe_int(insights.get("reach"))
        likes    = _safe_int(insights.get("likes"))    or _safe_int(item.get("like_count"))
        comments = _safe_int(insights.get("comments")) or _safe_int(item.get("comments_count"))
        shares   = _safe_int(insights.get("shares"))
        saves    = _safe_int(insights.get("saved"))

        rows.append({
            "platform":                      "Instagram",
            "published_at":                  item.get("timestamp"),
            "media_type":                    item.get("media_product_type") or item.get("media_type"),
            "title_or_caption":              caption,
            "url":                           item.get("permalink"),
            "views":                         views,
            "likes":                         likes,
            "comments":                      comments,
            "shares":                        shares,
            "saves":                         saves,
            "estimated_minutes_watched":     0,
            "average_view_duration_seconds": 0,
            "subscribers_gained":            0,
            "engagement_rate_percent":       _engagement_rate(views, likes, comments, shares, saves),
        })

    return rows


# ─────────────────────────────────────────────────────────────────
# HTML builder — called by social_dashboard.py
# ─────────────────────────────────────────────────────────────────

_IG_ACCOUNT_CONFIGS = [
    ("Instagram",    "@willpowerprotocols", "#E1306C"),
    ("Instagram-WB", "@will.byron88",       "#C9894C"),
]


def build_instagram_performance_html(rows: list[dict]) -> str:
    """Build Instagram performance section for both IG accounts.

    Takes a combined list of rows from fetch_instagram_rows and
    fetch_instagram_wb_rows and renders a scoreboard + detail table
    for each account. Returns empty string if no rows.
    """
    if not rows:
        return ""

    df = pd.DataFrame(rows)

    section_html = """
    <h2 style="color:#E1306C">Instagram Performance
        <span style="font-size:12px;color:#AAB4C0;font-weight:normal;margin-left:12px">
        Auto-pulled via Instagram Graph API</span>
    </h2>"""

    for plat_key, handle, color in _IG_ACCOUNT_CONFIGS:
        page_rows = df[df["platform"] == plat_key].copy()

        if page_rows.empty:
            section_html += f"""
    <div class="scoreboard-card" style="border-color:{color};margin-bottom:20px">
        <h2 style="color:{color}">Instagram {handle}</h2>
        <p style="color:#AAB4C0;font-size:13px">No posts found or credentials missing.</p>
    </div>"""
            continue

        page_rows = page_rows.sort_values("views", ascending=False)

        total_views    = _safe_int(page_rows["views"].sum())
        total_likes    = _safe_int(page_rows["likes"].sum())
        total_comments = _safe_int(page_rows["comments"].sum())
        total_shares   = _safe_int(page_rows["shares"].sum())
        total_saves    = _safe_int(page_rows["saves"].sum())
        avg_eng        = round(page_rows["engagement_rate_percent"].mean(), 2)

        summary_html = f"""
    <div class="scoreboard-grid" style="margin-bottom:16px">
        <div class="scoreboard-card" style="border-color:{color}">
            <h2 style="color:{color}">Instagram {handle}</h2>
            <table class="scoreboard-table">
                <tbody>
                    <tr><td>Posts Analyzed</td>
                        <td style="text-align:right"><strong>{len(page_rows)}</strong></td></tr>
                    <tr><td>Total Views</td>
                        <td style="text-align:right"><strong>{total_views:,}</strong></td></tr>
                    <tr><td>Total Likes</td>
                        <td style="text-align:right"><strong>{total_likes:,}</strong></td></tr>
                    <tr><td>Total Comments</td>
                        <td style="text-align:right"><strong>{total_comments:,}</strong></td></tr>
                    <tr><td>Total Shares</td>
                        <td style="text-align:right"><strong>{total_shares:,}</strong></td></tr>
                    <tr><td>Total Saves</td>
                        <td style="text-align:right"><strong>{total_saves:,}</strong></td></tr>
                    <tr><td>Avg Engagement Rate</td>
                        <td style="text-align:right"><strong>{avg_eng}%</strong></td></tr>
                </tbody>
            </table>
        </div>
    </div>"""

        detail_rows = ""
        for _, r in page_rows.iterrows():
            views_v    = _safe_int(r.get("views"))
            likes_v    = _safe_int(r.get("likes"))
            comments_v = _safe_int(r.get("comments"))
            shares_v   = _safe_int(r.get("shares"))
            saves_v    = _safe_int(r.get("saves"))
            eng_v      = r.get("engagement_rate_percent", 0.0)
            mtype_v    = str(r.get("media_type") or "")
            pub_v      = str(r.get("published_at") or "")[:10]
            cap_v      = str(r.get("title_or_caption") or "")[:90]
            url_v      = str(r.get("url") or "")
            book_v     = str(r.get("book_or_offer") or "—")

            detail_rows += f"""
        <tr>
            <td style="font-size:11px;color:#AAB4C0">{mtype_v}</td>
            <td style="font-size:11px;color:#C9A84C">{book_v}</td>
            <td style="font-size:11px">{cap_v}</td>
            <td style="text-align:center"><strong>{views_v:,}</strong></td>
            <td style="text-align:center">{likes_v}</td>
            <td style="text-align:center">{comments_v}</td>
            <td style="text-align:center">{shares_v}</td>
            <td style="text-align:center">{saves_v}</td>
            <td style="text-align:center">{eng_v}%</td>
            <td style="text-align:center;font-size:11px">{pub_v}</td>
            <td><a href="{url_v}" target="_blank" style="color:{color}">Open</a></td>
        </tr>"""

        table_html = f"""
    <div class="table-wrap" style="border:1px solid {color};border-radius:14px;margin-bottom:30px">
        <table style="min-width:1000px">
            <thead>
                <tr>
                    <th>Type</th>
                    <th>Book / Brand</th>
                    <th>Caption</th>
                    <th style="text-align:center">Views</th>
                    <th style="text-align:center">Likes</th>
                    <th style="text-align:center">Comments</th>
                    <th style="text-align:center">Shares</th>
                    <th style="text-align:center">Saves</th>
                    <th style="text-align:center">Eng %</th>
                    <th style="text-align:center">Date</th>
                    <th>Link</th>
                </tr>
            </thead>
            <tbody>{detail_rows}</tbody>
            <tfoot>
                <tr style="background:#101F36">
                    <td colspan="3">
                        <strong style="color:{color}">TOTALS — Instagram {handle}</strong>
                    </td>
                    <td style="text-align:center"><strong>{total_views:,}</strong></td>
                    <td style="text-align:center"><strong>{total_likes:,}</strong></td>
                    <td style="text-align:center"><strong>{total_comments:,}</strong></td>
                    <td style="text-align:center"><strong>{total_shares:,}</strong></td>
                    <td style="text-align:center"><strong>{total_saves:,}</strong></td>
                    <td style="text-align:center"><strong>{avg_eng}%</strong></td>
                    <td colspan="2"></td>
                </tr>
            </tfoot>
        </table>
    </div>"""

        section_html += summary_html + table_html

    return section_html
