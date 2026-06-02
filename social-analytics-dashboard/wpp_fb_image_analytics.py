"""
wpp_fb_image_analytics.py
─────────────────────────────────────────────────────────────────
Facebook Image Post Analytics — WPP, Prehistoric Memories, Protocol Lab, Will Byron.

Pulls analytics for IMAGE posts (not Reels/videos) from all four Facebook
pages, matches each post's photo URL back to pm_posts / tpl_posts /
gumroad_posts in wpp.db to enrich with pillar and topic, then builds an
HTML section for the social analytics dashboard.

USAGE in social_dashboard.py:
    from wpp_fb_image_analytics import fetch_fb_image_rows, build_fb_image_performance_html

.env keys required:
    FB_PAGE_ID_WPP             Will Power Protocols page ID
    FB_PAGE_ACCESS_TOKEN_WPP   Will Power Protocols page access token
    FB_PAGE_ID_PM              Prehistoric Memories page ID
    FB_PAGE_ACCESS_TOKEN_PM    Prehistoric Memories page access token
    FB_PAGE_ID_TPL             The Protocol Lab page ID
    FB_PAGE_ACCESS_TOKEN_TPL   The Protocol Lab page access token
    FB_PAGE_ID_WB              Will Byron page ID
    FB_PAGE_ACCESS_TOKEN_WB    Will Byron page access token

DB tables read:
    pm_posts  (asset_key, pillar, topic, facebook_url, posted, post_date)
    tpl_posts (asset_key, pillar, topic, facebook_url, posted, post_date)

URL stored in df:
    photo?fbid=ID format when attachment fbid is available — matches
    the same format stored in tpl_posts, pm_posts, and gumroad_posts.

MATCHING LOGIC:
    Facebook stores photo posts with a URL like:
        https://www.facebook.com/photo?fbid=<photo-id>&set=...
    The Graph API returns a post-level permalink_url and/or attachment url
    in the same format. We extract the numeric fbid from BOTH sides and
    match on that — this handles the photo?fbid vs photo/?fbid variation.

IMAGE FILTER:
    We call /{page-id}/posts with attachments{media_type,url}.
    A post is an image post when its first attachment media_type is
    "photo" or "album" (or has no media_type at all on text+photo posts).
    Any post whose media_type contains "video" or whose permalink_url
    contains "/reel/" is excluded.

METRICS (one at a time — fallback pattern from wpp_facebook.py):
    post_impressions_unique  — reach
    post_impressions         — total impressions
    post_reactions_by_type_total — reactions dict → summed
    post_clicks              — link + other clicks

DEPENDENCIES:
    pip install requests pandas python-dotenv
"""

import os
import re
import sqlite3
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv()

# ─────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────

FB_BASE_URL = "https://graph.facebook.com/v25.0"

WPP_DB_FILE   = "wpp.db"
DB_CANDIDATES = [
    Path(WPP_DB_FILE),
    Path(__file__).parent / WPP_DB_FILE,
    Path.home() / "myapps" / "social-analytics-dashboard" / WPP_DB_FILE,
]

# Page definitions — each dict drives both the API calls and DB lookup.
PAGES = [
    {
        "page_id":   os.getenv("FB_PAGE_ID_WPP"),
        "token":     os.getenv("FB_PAGE_ACCESS_TOKEN_WPP"),
        "name":      "WillPowerProtocols",
        "page_url":  "facebook.com/willpowerprotocols",
        "db_table":  None,   # image posts on WPP page tracked via gumroad_posts
        "color":     "#C9A84C",
    },
    {
        "page_id":   os.getenv("FB_PAGE_ID_PM"),
        "token":     os.getenv("FB_PAGE_ACCESS_TOKEN_PM"),
        "name":      "Prehistoric Memories",
        "page_url":  "",
        "db_table":  "pm_posts",
        "color":     "#A0714F",
    },
    {
        "page_id":   os.getenv("FB_PAGE_ID_TPL"),
        "token":     os.getenv("FB_PAGE_ACCESS_TOKEN_TPL"),
        "name":      "The Protocol Lab",
        "page_url":  "facebook.com/theprotocollab",
        "db_table":  "tpl_posts",
        "color":     "#2E86AB",
    },
    {
        "page_id":   os.getenv("FB_PAGE_ID_WB"),
        "token":     os.getenv("FB_PAGE_ACCESS_TOKEN_WB"),
        "name":      "Will Byron",
        "page_url":  "facebook.com/will.byron88",
        "db_table":  None,
        "color":     "#C9894C",
    },
]

IMAGE_POST_METRICS = [
    "post_impressions_unique",        # reach — confirmed 200 on v25.0 for these pages
    "post_reactions_by_type_total",   # reactions dict — confirmed 200
    "post_clicks",                    # clicks — confirmed 200
    # post_impressions excluded — returns (#100) invalid metric on these page tokens
]


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


def _safe_text(value) -> str:
    return "" if value is None else str(value)


def _truncate_text(text, max_len=120) -> str:
    text = _safe_text(text).replace("\n", " ").strip()
    return text[:max_len] + "..." if len(text) > max_len else text


def _extract_fbid(url: str) -> str:
    """Extract the numeric fbid from a Facebook photo URL.

    Handles both ?fbid= and /?fbid= variants stored in the DB and
    returned by the Graph API permalink_url / attachment url fields.
    """
    match = re.search(r"fbid=(\d+)", _safe_text(url))
    return match.group(1) if match else ""


def _sum_reactions(value) -> int:
    """Sum a reactions dict returned by post_reactions_by_type_total."""
    if isinstance(value, dict):
        return sum(_safe_int(v) for v in value.values())
    return _safe_int(value)


# ─────────────────────────────────────────────────────────────────
# DB helpers — pattern from wpp_kdp.py
# ─────────────────────────────────────────────────────────────────

def _find_db() -> Path | None:
    for candidate in DB_CANDIDATES:
        if candidate.exists():
            return candidate
    return None


def _load_db_lookup(table: str | None) -> dict:
    """Build a fbid -> {pillar, topic, post_date, asset_key} lookup.

    Returns {} immediately when table is None (page has no DB table yet).
    Only includes rows where facebook_url is not NULL/empty.
    """
    if not table:
        return {}
    db_path = _find_db()
    if db_path is None:
        return {}

    try:
        conn = sqlite3.connect(db_path)
        df = pd.read_sql_query(
            f"""
            SELECT asset_key, pillar, topic, facebook_url,
                   COALESCE(post_date, '') AS post_date
            FROM {table}
            WHERE facebook_url IS NOT NULL
              AND facebook_url != ''
            """,
            conn,
        )
        conn.close()
    except Exception as e:
        print(f"DB lookup error ({table}): {e}")
        return {}

    lookup = {}
    for _, row in df.iterrows():
        fbid = _extract_fbid(_safe_text(row["facebook_url"]))
        if fbid:
            lookup[fbid] = {
                "asset_key": _safe_text(row["asset_key"]),
                "pillar":    _safe_text(row["pillar"]),
                "topic":     _safe_text(row["topic"]),
                "post_date": _safe_text(row["post_date"]),
            }

    return lookup


# ─────────────────────────────────────────────────────────────────
# Graph API helpers — pattern from wpp_facebook.py
# Token is passed explicitly so both pages can share the same helpers.
# ─────────────────────────────────────────────────────────────────

def _fb_get_json(path_or_url: str, token: str, params: dict | None = None) -> dict | None:
    """GET helper for Graph API. Returns parsed JSON or None — never raises."""
    params = dict(params or {})
    params["access_token"] = token
    url = (
        path_or_url
        if str(path_or_url).startswith("http")
        else f"{FB_BASE_URL}/{path_or_url.lstrip('/')}"
    )
    try:
        response = requests.get(url, params=params, timeout=30)
    except requests.RequestException as e:
        print(f"FB image request error for {url}: {e}")
        return None

    if response.status_code != 200:
        try:
            err = response.json().get("error", {})
            msg = err.get("message", response.text[:250])
        except Exception:
            msg = response.text[:250]
        print(f"FB image API warning {response.status_code} for {url}: {msg}")
        return None

    return response.json()


def _fb_metric_value(item: dict) -> int:
    """Prefer the lifetime value row; fall back to the last daily row."""
    values = item.get("values", [])
    if not values:
        return 0
    for value_row in values:
        if "end_time" not in value_row:
            return _safe_int(value_row.get("value", 0))
    return _safe_int(values[-1].get("value", 0))


def _fb_fetch_image_insights(post_id: str, token: str) -> dict:
    """Fetch image post insights via /{post-id}/insights.

    Tries bulk first; falls back one metric at a time so one
    unsupported metric cannot zero out the rest (same pattern
    as wpp_facebook.py fb_fetch_insights).
    """
    if not post_id:
        return {}

    def _parse(payload: dict | None) -> dict:
        out = {}
        if not payload:
            return out
        for item in payload.get("data", []):
            name = item.get("name")
            if not name:
                continue
            # post_reactions_by_type_total returns a dict value — keep raw.
            raw = item.get("values", [{}])
            if raw:
                val = raw[-1].get("value", 0)
                # Prefer lifetime row (no end_time) if present.
                for v in raw:
                    if "end_time" not in v:
                        val = v.get("value", 0)
                        break
                out[name] = val
        return out

    # Fast path: bulk request — no period param, let the API use its default.
    data = _fb_get_json(
        f"{post_id}/insights",
        token,
        {"metric": ",".join(IMAGE_POST_METRICS)},
    )
    parsed = _parse(data)
    if parsed:
        return parsed

    # Slow path: one metric at a time so one bad metric can't block the rest.
    out = {}
    for metric in IMAGE_POST_METRICS:
        data = _fb_get_json(
            f"{post_id}/insights",
            token,
            {"metric": metric},
        )
        out.update(_parse(data))
    return out


# ─────────────────────────────────────────────────────────────────
# Image post collector — one page at a time
# ─────────────────────────────────────────────────────────────────

def _fetch_image_posts(page_id: str, token: str, limit: int = 50) -> list[dict]:
    """Fetch image posts from a single page via /{page-id}/posts.

    Returns list of dicts with keys:
        post_id, created_time, message, permalink_url, fbid
    Only photo/album posts are returned; video/reel posts are skipped.
    """
    data = _fb_get_json(
        f"{page_id}/posts",
        token,
        {
            "fields": (
                "id,message,created_time,permalink_url,"
                "reactions.summary(total_count),"
                "comments.summary(total_count),"
                "shares,"
                "attachments{media_type,url,description}"
            ),
            "limit": limit,
        },
    )
    if not data:
        return []

    posts = []
    for post in data.get("data", []):
        post_id      = _safe_text(post.get("id", ""))
        permalink    = _safe_text(post.get("permalink_url", ""))
        message      = _safe_text(post.get("message", ""))
        created_time = _safe_text(post.get("created_time", ""))

        # Reaction/comment counts available directly from /posts fields.
        post_reactions = _safe_int(
            ((post.get("reactions") or {}).get("summary") or {}).get("total_count", 0)
        )
        post_comments = _safe_int(
            ((post.get("comments") or {}).get("summary") or {}).get("total_count", 0)
        )
        post_shares = _safe_int((post.get("shares") or {}).get("count", 0))

        # Skip Reels / video posts by permalink pattern.
        if "reel" in permalink.lower():
            continue

        attachments = (post.get("attachments") or {}).get("data", [])
        media_type  = ""
        att_url     = ""

        if attachments:
            first_att  = attachments[0]
            media_type = _safe_text(first_att.get("media_type", "")).lower()
            att_url    = _safe_text(first_att.get("url", ""))

        # Skip video attachments.
        if "video" in media_type:
            continue

        # Derive fbid: try attachment url first, then post permalink_url.
        fbid = _extract_fbid(att_url) or _extract_fbid(permalink)

        posts.append({
            "post_id":       post_id,
            "created_time":  created_time,
            "message":       message,
            "permalink_url": permalink,
            "media_type":    media_type or "photo",
            "fbid":          fbid,
            "post_reactions": post_reactions,
            "post_comments":  post_comments,
            "post_shares":    post_shares,
        })

    return posts


# ─────────────────────────────────────────────────────────────────
# Main fetcher — called by social_dashboard.py
# ─────────────────────────────────────────────────────────────────

def fetch_fb_image_rows(limit: int = 50) -> list[dict]:
    """Fetch Facebook image post rows for both PM and TPL pages.

    Returns a list of dicts with columns:
        platform, asset_key, page_name, pillar, topic,
        post_date, reach, reactions, clicks, impressions,
        engagement_rate, facebook_url, title_or_caption,
        published_at, url
    """
    all_rows = []

    for page in PAGES:
        page_id   = page["page_id"]
        token     = page["token"]
        page_name = page["name"]
        db_table  = page["db_table"]

        if not page_id or not token:
            print(f"Skipping {page_name}: missing credentials in .env.")
            continue

        print(f"FB Image ({page_name}): fetching posts...")
        posts = _fetch_image_posts(page_id, token, limit=limit)
        print(f"FB Image ({page_name}): {len(posts)} image posts found.")

        db_lookup = _load_db_lookup(db_table)
        print(f"FB Image ({page_name}): {len(db_lookup)} DB entries with facebook_url.")

        matched   = 0
        unmatched = 0

        for post in posts:
            post_id  = post["post_id"]
            fbid     = post["fbid"]
            permalink = post["permalink_url"]

            # Fetch insights for this post.
            insights = _fb_fetch_image_insights(post_id, token)

            reach       = _safe_int(insights.get("post_impressions_unique", 0))
            reactions   = (
                _sum_reactions(insights.get("post_reactions_by_type_total", 0))
                or post["post_reactions"]   # fallback: reactions count from /posts fields
            )
            clicks      = _safe_int(insights.get("post_clicks", 0))
            comments    = post["post_comments"]
            shares      = post["post_shares"]

            # Engagement rate: reactions / reach (image posts have no "views").
            eng_rate = round((reactions / reach) * 100, 2) if reach > 0 else 0.0

            # DB match on fbid.
            db_info  = db_lookup.get(fbid, {})
            if db_info:
                matched += 1
            else:
                unmatched += 1

            all_rows.append({
                "platform":           f"FB-Image-{page_name.replace(' ', '')}",
                "page_name":          page_name,
                "book_or_offer":      page_name,   # prevents "—" in unmatched report
                "asset_key":          db_info.get("asset_key", ""),
                "pillar":             db_info.get("pillar", "—"),
                "topic":              db_info.get("topic", "—"),
                "post_date":          db_info.get("post_date", post["created_time"][:10]),
                "published_at":       post["created_time"],
                "reach":              reach,
                "reactions":          reactions,
                "clicks":             clicks,
                "engagement_rate":    eng_rate,
                "facebook_url":       permalink,
                "fbid":               post.get("fbid", ""),
                "title_or_caption":   _truncate_text(post["message"]),
                # Use photo?fbid= URL so normalize_url can match against
                # tpl_posts, pm_posts, and gumroad_posts which store this format.
                "url": (
                    f"https://www.facebook.com/photo?fbid={fbid}"
                    if fbid else permalink
                ),
                "views":              reach,
                "likes":              reactions,
                "comments":           comments,
                "shares":             shares,
                "saves":              0,
                "media_type":         "IMAGE",
                "estimated_minutes_watched":     0,
                "average_view_duration_seconds": 0,
                "subscribers_gained":            0,
                "engagement_rate_percent":       eng_rate,
            })

        print(
            f"FB Image ({page_name}): {len(posts)} posts — "
            f"{matched} matched DB, {unmatched} unmatched."
        )

    print(f"FB Image: {len(all_rows)} total rows built.")
    return all_rows


# ─────────────────────────────────────────────────────────────────
# HTML builder — called by social_dashboard.py
# ─────────────────────────────────────────────────────────────────

def build_fb_image_performance_html(rows: list[dict]) -> str:
    """Build the image-post performance section for PM and TPL.

    Renders a summary scoreboard card + detail table for each page,
    styled consistently with the existing FB Reels section.
    Returns empty string if no rows — dashboard renders without it.
    """
    if not rows:
        return ""

    df = pd.DataFrame(rows)

    section_html = """
    <h2 style="color:#E8A838">Facebook Image Posts — PM, TPL &amp; Will Byron
        <span style="font-size:12px;color:#AAB4C0;font-weight:normal;margin-left:12px">
        Auto-pulled via Page Post Insights</span>
    </h2>"""

    for page in PAGES:
        page_name = page["name"]
        color     = page["color"]
        page_url  = page.get("page_url", "")
        url_link  = (
            f' &middot; <a href="https://{page_url}" target="_blank"'
            f' style="color:#AAB4C0;font-size:12px;font-weight:normal">{page_url}</a>'
            if page_url else ""
        )
        page_rows = df[df["page_name"] == page_name].copy()

        if page_rows.empty:
            section_html += f"""
    <div class="scoreboard-card" style="border-color:{color};margin-bottom:20px">
        <h2 style="color:{color}">{page_name}{url_link}</h2>
        <p style="color:#AAB4C0;font-size:13px">No image posts found or credentials missing.</p>
    </div>"""
            continue

        page_rows = page_rows.sort_values("reach", ascending=False)

        total_reach     = _safe_int(page_rows["reach"].sum())
        total_reactions = _safe_int(page_rows["reactions"].sum())
        total_clicks    = _safe_int(page_rows["clicks"].sum())
        total_comments  = _safe_int(page_rows["comments"].sum())
        total_shares    = _safe_int(page_rows["shares"].sum())
        avg_eng         = round(page_rows["engagement_rate"].mean(), 2)
        matched_count     = (page_rows["pillar"].ne("—")).sum()

        # ── Summary scoreboard ──────────────────────────────────
        summary_html = f"""
    <div class="scoreboard-grid" style="margin-bottom:16px">
        <div class="scoreboard-card" style="border-color:{color}">
            <h2 style="color:{color}">{page_name}{url_link}</h2>
            <table class="scoreboard-table">
                <tbody>
                    <tr><td>Posts Analyzed</td>
                        <td style="text-align:right"><strong>{len(page_rows)}</strong></td></tr>
                    <tr><td>DB Matched</td>
                        <td style="text-align:right"><strong>{matched_count} / {len(page_rows)}</strong></td></tr>
                    <tr><td>Total Reach</td>
                        <td style="text-align:right"><strong>{total_reach:,}</strong></td></tr>
                    <tr><td>Total Reactions</td>
                        <td style="text-align:right"><strong>{total_reactions:,}</strong></td></tr>
                    <tr><td>Total Comments</td>
                        <td style="text-align:right"><strong>{total_comments:,}</strong></td></tr>
                    <tr><td>Total Shares</td>
                        <td style="text-align:right"><strong>{total_shares:,}</strong></td></tr>
                    <tr><td>Total Clicks</td>
                        <td style="text-align:right"><strong>{total_clicks:,}</strong></td></tr>
                    <tr><td>Avg Engagement Rate</td>
                        <td style="text-align:right"><strong>{avg_eng}%</strong></td></tr>
                </tbody>
            </table>
        </div>
    </div>"""

        # ── Detail table ────────────────────────────────────────
        detail_rows = ""
        for _, r in page_rows.iterrows():
            pillar  = _safe_text(r.get("pillar", "—"))
            topic   = _safe_text(r.get("topic", "—"))
            caption = _safe_text(r.get("title_or_caption", ""))[:90]
            reach_v   = _safe_int(r.get("reach"))
            react_v   = _safe_int(r.get("reactions"))
            click_v   = _safe_int(r.get("clicks"))
            comment_v = _safe_int(r.get("comments"))
            share_v   = _safe_int(r.get("shares"))
            eng_v   = r.get("engagement_rate", 0.0)
            date_v  = _safe_text(r.get("post_date", ""))[:10]
            url_v   = _safe_text(r.get("facebook_url", ""))
            asset_v = _safe_text(r.get("asset_key", ""))

            detail_rows += f"""
        <tr>
            <td style="font-size:11px;color:#C9A84C">{asset_v}</td>
            <td>{pillar}</td>
            <td style="font-size:11px">{topic}</td>
            <td style="font-size:11px;color:#AAB4C0">{caption}</td>
            <td style="text-align:center"><strong>{reach_v:,}</strong></td>
            <td style="text-align:center">{react_v}</td>
            <td style="text-align:center">{comment_v}</td>
            <td style="text-align:center">{share_v}</td>
            <td style="text-align:center">{click_v}</td>
            <td style="text-align:center">{eng_v}%</td>
            <td style="text-align:center;font-size:11px">{date_v}</td>
            <td><a href="{url_v}" target="_blank" style="color:{color}">Open</a></td>
        </tr>"""

        table_html = f"""
    <div class="table-wrap" style="border:1px solid {color};border-radius:14px;margin-bottom:30px">
        <table style="min-width:1100px">
            <thead>
                <tr>
                    <th>Asset</th>
                    <th>Pillar</th>
                    <th>Topic</th>
                    <th>Caption</th>
                    <th style="text-align:center">Reach</th>
                    <th style="text-align:center">Reactions</th>
                    <th style="text-align:center">Comments</th>
                    <th style="text-align:center">Shares</th>
                    <th style="text-align:center">Clicks</th>
                    <th style="text-align:center">Eng %</th>
                    <th style="text-align:center">Date</th>
                    <th>Link</th>
                </tr>
            </thead>
            <tbody>{detail_rows}</tbody>
            <tfoot>
                <tr style="background:#101F36">
                    <td colspan="4">
                        <strong style="color:{color}">TOTALS — {page_name}</strong>
                    </td>
                    <td style="text-align:center"><strong>{total_reach:,}</strong></td>
                    <td style="text-align:center"><strong>{total_reactions:,}</strong></td>
                    <td style="text-align:center"><strong>{total_comments:,}</strong></td>
                    <td style="text-align:center"><strong>{total_shares:,}</strong></td>
                    <td style="text-align:center"><strong>{total_clicks:,}</strong></td>
                    <td style="text-align:center"><strong>{avg_eng}%</strong></td>
                    <td colspan="2"></td>

                </tr>
            </tfoot>
        </table>
    </div>"""

        section_html += summary_html + table_html

    return section_html
