"""
wpp_facebook.py
─────────────────────────────────────────────────────────────────
WPP Facebook Analytics Module — standalone module.

Handles all Facebook Graph API calls, metric collection, and
HTML dashboard section for the WPP social analytics dashboard.

USAGE in social_dashboard.py:
    from wpp_facebook import fetch_facebook_rows, build_facebook_performance_html

WHAT IT CONTAINS:
    Constants  — FACEBOOK_POST_FALLBACK_METRICS, FACEBOOK_REEL_VIDEO_METRICS
    Helpers    — fb_extract_reel_id_from_url, fb_get_json, fb_metric_value
                 fb_fetch_insights, fb_sum_reactions_from_insights
    Fetchers   — fb_collect_video_candidates, fetch_facebook_rows
    HTML       — build_facebook_performance_html

NOTES ON FACEBOOK METRICS:
    Meta's Graph API is inconsistent across object types and API versions.
    Metrics are split into two buckets:
      1) Post-level metrics — proven working on /{page_post_id}/insights
      2) Reel/video metrics — proven working on /{video_or_reel_id}/video_insights
    fb_fetch_insights() tries bulk first, then falls back one metric at a time
    so one unsupported metric can't zero out the entire dashboard.
    post_video_views is the top-line view count for Reels/videos (blue_reels_play_count deprecated 2026-06).
    post_video_view_time and post_video_avg_time_watched are sourced via video_insights edge.

DEPENDENCIES:
    pip install requests pandas python-dotenv
    .env must contain: FB_PAGE_ID, FB_PAGE_ACCESS_TOKEN
"""

import json
import os
import re

import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv()

# ─────────────────────────────────────────────────────────────────
# Config — loaded from .env
# ─────────────────────────────────────────────────────────────────

FB_PAGE_ID           = os.getenv("FB_PAGE_ID_WPP")
FB_PAGE_ACCESS_TOKEN = os.getenv("FB_PAGE_ACCESS_TOKEN_WPP")
WB_PAGE_ID           = os.getenv("FB_PAGE_ID_WB")
WB_PAGE_ACCESS_TOKEN = os.getenv("FB_PAGE_ACCESS_TOKEN_WB")
FB_BASE_URL          = "https://graph.facebook.com/v25.0"
MAX_FACEBOOK_POSTS   = 50


# ─────────────────────────────────────────────────────────────────
# Metric lists
# Proven working by debug_facebook_deep_metrics.py on v25.0.
# Keeping to known-good metrics prevents "(#100) valid insights
# metric" warnings from Meta.
# ─────────────────────────────────────────────────────────────────

FACEBOOK_POST_FALLBACK_METRICS = [
    "post_reactions_by_type_total",
    "post_clicks",
    "post_clicks_by_type",
    "post_video_views",
    "post_video_views_unique",
    "post_video_views_15s",
    "post_video_view_time",
    "post_video_avg_time_watched",
    "post_total_media_view_unique",
    "post_video_complete_views_30s",
    "post_video_complete_views_30s_unique",
    # post_impressions_unique removed — returns (#100) invalid metric as of 2026-06
]

FACEBOOK_REEL_VIDEO_METRICS = [
    "blue_reels_play_count",
    "post_video_view_time",
    "post_video_avg_time_watched",
    # post_impressions_unique removed — returns (#100) invalid metric as of 2026-06
]


# ─────────────────────────────────────────────────────────────────
# Shared helpers (inline copies — keeps module self-contained)
# ─────────────────────────────────────────────────────────────────

def _safe_int(value) -> int:
    try:
        if pd.isna(value):
            return 0
        return int(float(value))
    except Exception:
        return 0


def _safe_float(value) -> float:
    try:
        if pd.isna(value):
            return 0.0
        return float(value)
    except Exception:
        return 0.0


def _safe_text(value) -> str:
    if value is None:
        return ""
    return str(value)


def _truncate_text(text, max_len=145) -> str:
    text = _safe_text(text).replace("\n", " ").strip()
    if len(text) > max_len:
        return text[:max_len] + "..."
    return text


def _engagement_rate(views, likes, comments, shares=0, saves=0) -> float:
    views = _safe_int(views)
    if views <= 0:
        return 0.0
    total = _safe_int(likes) + _safe_int(comments) + _safe_int(shares) + _safe_int(saves)
    return round((total / views) * 100, 2)


# ─────────────────────────────────────────────────────────────────
# URL helpers
# ─────────────────────────────────────────────────────────────────

def fb_extract_reel_id_from_url(url: str) -> str:
    """Extract the numeric Reel/video ID from a Facebook URL.

    Page /posts sometimes returns the Page post ID instead of the
    actual Reel/video ID. The public permalink URL usually has the
    correct ID: https://www.facebook.com/reel/971178662197004/
    """
    text = _safe_text(url)
    patterns = [
        r"facebook\.com/reel/(\d+)",
        r"facebook\.com/.+?/videos/(\d+)",
        r"fb\.watch/([^/?#]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1)
    return ""


# ─────────────────────────────────────────────────────────────────
# Graph API GET helper
# ─────────────────────────────────────────────────────────────────

def fb_get_json(path_or_url: str, params: dict | None = None, token: str | None = None) -> dict | None:
    """GET helper for Graph API paths. Returns JSON or None.
    Never raises — one bad metric should not stop the dashboard.
    Pass token explicitly to use a page-specific access token.
    """
    params = dict(params or {})
    params.setdefault("access_token", token or FB_PAGE_ACCESS_TOKEN)
    url = (
        path_or_url
        if str(path_or_url).startswith("http")
        else f"{FB_BASE_URL}/{path_or_url.lstrip('/')}"
    )
    try:
        response = requests.get(url, params=params, timeout=30)
    except requests.RequestException as e:
        print(f"Facebook request error for {url}: {e}")
        return None

    if response.status_code != 200:
        try:
            err = response.json().get("error", {})
            msg = err.get("message", response.text[:250])
        except Exception:
            msg = response.text[:250]
        print(f"Facebook API warning {response.status_code} for {url}: {msg}")
        return None
    return response.json()


# ─────────────────────────────────────────────────────────────────
# Metric value extraction
# ─────────────────────────────────────────────────────────────────

def fb_metric_value(item: dict) -> int | float:
    """Return the most useful value from a Meta insights metric item.

    Some post-level video metrics return both lifetime and day rows.
    The old approach used values[-1] which often selected the latest
    daily row (frequently 0) instead of the lifetime row — causing
    3-second views and watch time to show as zero incorrectly.
    This version prefers the lifetime row when present.
    """
    values = item.get("values", [])
    if not values:
        return 0

    # Lifetime metrics have one value with no end_time. Prefer that.
    for value_row in values:
        if "end_time" not in value_row:
            return value_row.get("value", 0)

    # Fallback: use latest daily value for purely daily metrics.
    return values[-1].get("value", 0)


# ─────────────────────────────────────────────────────────────────
# Insights fetcher
# ─────────────────────────────────────────────────────────────────

def fb_fetch_insights(
    object_id: str,
    metrics: list[str],
    edge: str = "video_insights",
    token: str | None = None,
) -> dict:
    """Fetch insight metrics from /{id}/video_insights or /{id}/insights.

    Meta can reject a whole comma-separated metric request when one
    metric is unavailable for a given object or API version. This
    tries bulk first for speed, then falls back one metric at a time.
    That prevents one unsupported metric from zeroing out everything.
    """
    if not object_id:
        return {}

    def parse_payload(payload: dict | None) -> dict:
        out = {}
        periods = {}
        if not payload:
            return out
        for item in payload.get("data", []):
            name = item.get("name")
            if not name:
                continue
            period = item.get("period", "")
            value = fb_metric_value(item)
            # Meta can return the same metric twice: lifetime and day.
            # Keep lifetime when present; don't let daily zero overwrite it.
            if name not in out:
                out[name] = value
                periods[name] = period
            elif period == "lifetime" and periods.get(name) != "lifetime":
                out[name] = value
                periods[name] = period
        return out

    # Meta bulk calls now reject comma-separated metrics on these endpoints — always go one at a time.
    out = {}
    for metric in metrics:
        data = fb_get_json(f"{object_id}/{edge}", {"metric": metric}, token=token)
        out.update(parse_payload(data))
    return out


# ─────────────────────────────────────────────────────────────────
# Reaction aggregator
# ─────────────────────────────────────────────────────────────────

def fb_sum_reactions_from_insights(insights: dict) -> int:
    """Sum all reaction types from insights into a single total."""
    reaction_metrics = [
        "post_video_likes_by_reaction_type_like",
        "post_video_likes_by_reaction_type_love",
        "post_video_likes_by_reaction_type_wow",
        "post_video_likes_by_reaction_type_haha",
        "post_video_likes_by_reaction_type_sorry",
        "post_video_likes_by_reaction_type_anger",
    ]
    total = sum(_safe_int(insights.get(m)) for m in reaction_metrics)

    # Regular post insights return a dict for post_reactions_by_type_total.
    by_type = insights.get("post_reactions_by_type_total")
    if isinstance(by_type, dict):
        total += sum(_safe_int(v) for v in by_type.values())
    return total


# ─────────────────────────────────────────────────────────────────
# Video candidate collector
# ─────────────────────────────────────────────────────────────────

def fb_collect_video_candidates(
    limit: int = 50,
    page_id: str | None = None,
    token: str | None = None,
) -> list[dict]:
    """Collect Facebook Reels/videos from a Page.

    Prefers /video_reels and /videos because those IDs work with
    /{video-id}/video_insights. Uses /posts as a fallback so older
    Page posts still appear and can expose attached video target IDs.
    Pass page_id and token to target a page other than FB_PAGE_ID_WPP.
    """
    page_id = page_id or FB_PAGE_ID
    candidates = {}

    def add_candidate(item: dict, source: str, fallback_message: str = ""):
        video_id = _safe_text(item.get("id") or item.get("video_id") or "").strip()
        if not video_id:
            return
        description = (
            item.get("description")
            or item.get("message")
            or item.get("title")
            or fallback_message
            or ""
        )
        permalink = (
            item.get("permalink_url")
            or item.get("url")
            or item.get("permalink")
            or ""
        )
        url_reel_id = fb_extract_reel_id_from_url(permalink)

        # Critical: /{post-id}/insights ≠ /{video-id}/video_insights.
        # If the public permalink contains /reel/<number>, use that
        # as the video/Reel ID and keep the Page post ID for fallback.
        original_id = video_id
        if url_reel_id and url_reel_id != video_id:
            video_id = url_reel_id
            item.setdefault("post_id", original_id)

        current = candidates.get(video_id, {})
        current.update({
            "id":                 video_id,
            "post_id":            item.get("post_id", current.get("post_id", "")),
            "created_time":       item.get("created_time") or current.get("created_time", ""),
            "description":        description or current.get("description", ""),
            "permalink_url":      permalink or current.get("permalink_url", ""),
            "length":             item.get("length", current.get("length", 0)),
            "source_endpoint":    source,
            "post_reaction_count": item.get("post_reaction_count", current.get("post_reaction_count", 0)),
            "post_comment_count": item.get("post_comment_count", current.get("post_comment_count", 0)),
            "post_share_count":   item.get("post_share_count", current.get("post_share_count", 0)),
        })
        candidates[video_id] = current

    # Primary: /video_reels and /videos give IDs that work with video_insights.
    for edge in ["video_reels", "videos"]:
        data = fb_get_json(
            f"{page_id}/{edge}",
            {"fields": "id,description,title,created_time,permalink_url,length", "limit": limit},
            token=token,
        )
        if data:
            for item in data.get("data", []):
                add_candidate(item, edge)

    # Fallback: /posts can expose Reel target IDs via attachments.
    posts_data = fb_get_json(
        f"{page_id}/posts",
        {
            "fields": (
                "id,message,created_time,permalink_url,"
                "reactions.summary(total_count),comments.summary(total_count),shares,"
                "attachments{media_type,target,url,title,description,subattachments}"
            ),
            "limit": limit,
        },
        token=token,
    )
    if posts_data:
        for post in posts_data.get("data", []):
            post_id            = post.get("id", "")
            post_message       = post.get("message", "")
            permalink          = post.get("permalink_url", "")
            post_reaction_count = _safe_int(((post.get("reactions") or {}).get("summary") or {}).get("total_count"))
            post_comment_count  = _safe_int(((post.get("comments") or {}).get("summary") or {}).get("total_count"))
            post_share_count    = _safe_int((post.get("shares") or {}).get("count"))
            attachments        = (post.get("attachments") or {}).get("data", [])
            added_attachment   = False

            for att in attachments:
                media_type = _safe_text(att.get("media_type", "")).lower()
                target_id  = _safe_text((att.get("target") or {}).get("id", "")).strip()
                if target_id and ("video" in media_type or "reel" in permalink.lower()):
                    add_candidate({
                        "id":                  target_id,
                        "post_id":             post_id,
                        "created_time":        post.get("created_time"),
                        "description":         att.get("description") or att.get("title") or post_message,
                        "permalink_url":       att.get("url") or permalink,
                        "post_reaction_count": post_reaction_count,
                        "post_comment_count":  post_comment_count,
                        "post_share_count":    post_share_count,
                    }, "posts_attachment", post_message)
                    added_attachment = True

            if not added_attachment and "facebook.com/reel" in permalink.lower():
                reel_id = fb_extract_reel_id_from_url(permalink) or post_id
                add_candidate({
                    "id":                  reel_id,
                    "post_id":             post_id,
                    "created_time":        post.get("created_time"),
                    "description":         post_message,
                    "permalink_url":       permalink,
                    "post_reaction_count": post_reaction_count,
                    "post_comment_count":  post_comment_count,
                    "post_share_count":    post_share_count,
                }, "posts_reel_permalink", post_message)

    return list(candidates.values())[:limit]


# ─────────────────────────────────────────────────────────────────
# Main fetcher — called by social_dashboard.py
# ─────────────────────────────────────────────────────────────────

def _build_fb_page_rows(
    page_id: str, token: str, platform_label: str, limit: int = 50
) -> list[dict]:
    """Fetch and build row dicts for a single Facebook page.

    Shared by WPP and Will Byron — platform_label differentiates them
    in the combined dataframe ("Facebook" vs "Facebook-WB").
    """
    videos = fb_collect_video_candidates(limit=limit, page_id=page_id, token=token)
    print(f"{platform_label}: {len(videos)} Reels/videos found.")

    rows = []
    for video in videos:
        video_id     = video.get("id")
        post_id      = video.get("post_id") or video_id
        permalink    = _safe_text(video.get("permalink_url", ""))
        description  = video.get("description", "")
        created_time = _safe_text(video.get("created_time", ""))
        length       = _safe_int(video.get("length", 0))

        post_insights  = fb_fetch_insights(post_id,  FACEBOOK_POST_FALLBACK_METRICS, edge="insights",       token=token)
        video_insights = fb_fetch_insights(video_id, FACEBOOK_REEL_VIDEO_METRICS,    edge="video_insights", token=token)

        combined = {**video_insights, **post_insights}

        reel_plays           = _safe_int(combined.get("blue_reels_play_count"))
        three_second_views   = _safe_int(combined.get("post_video_views"))
        three_second_unique  = _safe_int(combined.get("post_video_views_unique"))
        fifteen_second_views = _safe_int(combined.get("post_video_views_15s"))
        ten_second_views     = _safe_int(combined.get("post_video_views_10s"))
        reach                = _safe_int(combined.get("post_impressions_unique"))
        watch_ms             = _safe_int(combined.get("post_video_view_time"))
        avg_watch_ms         = _safe_int(combined.get("post_video_avg_time_watched"))
        reactions            = fb_sum_reactions_from_insights(combined) or _safe_int(video.get("post_reaction_count"))
        comments             = _safe_int(video.get("post_comment_count"))
        shares               = _safe_int(video.get("post_share_count"))
        clicks               = _safe_int(combined.get("post_clicks"))
        reaction_detail      = combined.get("post_reactions_by_type_total")
        click_detail         = combined.get("post_clicks_by_type")
        total_media_view     = _safe_int(combined.get("post_total_media_view"))
        total_media_unique   = _safe_int(combined.get("post_total_media_view_unique"))

        views             = reel_plays or total_media_view or three_second_views
        estimated_minutes = round(watch_ms / 1000 / 60, 2) if watch_ms else 0
        avg_watch_seconds = round(avg_watch_ms / 1000, 2) if avg_watch_ms else 0
        qualified_15s_rate = round((fifteen_second_views / views) * 100, 2) if views else 0.0
        plays_per_reached  = round((views / reach), 2) if reach else 0.0

        if views == 0:
            print(
                f"{platform_label} metric warning: zero views for {permalink or video_id} "
                f"video_id={video_id} post_id={post_id} "
                f"metrics_returned={sorted(combined.keys())}"
            )

        rows.append({
            "platform":                          platform_label,
            "publishedAt":                       created_time,
            "published_at":                      created_time,
            "published_date":                    created_time[:10],
            "mediaType":                         "VIDEO",
            "media_type":                        "VIDEO",
            "post_type":                         "REEL" if "reel" in permalink.lower() else "VIDEO",
            "titleOrCaption":                    _truncate_text(description),
            "title_or_caption":                  _truncate_text(description),
            "url":                               permalink,
            "views":                             views,
            "likes":                             reactions,
            "comments":                          comments,
            "shares":                            shares,
            "saves":                             0,
            "reach":                             reach,
            "clicks":                            clicks,
            "estimatedMinutesWatched":           estimated_minutes,
            "estimated_minutes_watched":         estimated_minutes,
            "averageViewDurationSeconds":        avg_watch_seconds,
            "average_view_duration_seconds":     avg_watch_seconds,
            "subscribersGained":                 0,
            "subscribers_gained":                0,
            "engagementRatePercent":             _engagement_rate(views, reactions, comments, shares, 0),
            "engagement_rate_percent":           _engagement_rate(views, reactions, comments, shares, 0),
            "content_signal":                    "",
            "recommended_action":                "",
            "book_or_offer":                     "—",
            "content_pillar":                    "—",
            "facebook_reel_plays":               reel_plays,
            "facebook_3s_views":                 three_second_views,
            "facebook_3s_unique_views":          three_second_unique,
            "facebook_10s_views":                ten_second_views,
            "facebook_15s_views":                fifteen_second_views,
            "facebook_total_views_any":          views,
            "facebook_total_media_view":         total_media_view,
            "facebook_total_media_view_unique":  total_media_unique,
            "estimated_actual_views":            views,
            "facebook_15s_view_rate_percent":    qualified_15s_rate,
            "facebook_plays_per_reached_person": plays_per_reached,
            "facebook_avg_watch_seconds":        avg_watch_seconds,
            "reactions":                         reactions,
            "length_seconds":                    length,
            "facebook_metric_source":            "post_insights+video_insights",
            "facebook_candidate_source":         video.get("source_endpoint", "unknown"),
            "facebook_video_id_used":            video_id,
            "facebook_post_id_fallback":         post_id,
            "facebook_reactions_by_type":        json.dumps(reaction_detail) if isinstance(reaction_detail, dict) else _safe_text(reaction_detail),
            "facebook_clicks_by_type":           json.dumps(click_detail) if isinstance(click_detail, dict) else _safe_text(click_detail),
            "facebook_insight_metrics_returned": ",".join(sorted(combined.keys())),
            "facebook_raw_metrics_json":         json.dumps(combined, default=str, ensure_ascii=False),
        })

    print(f"{platform_label}: {len(rows)} rows built.")
    return rows


def fetch_facebook_rows(limit: int = 50) -> list[dict]:
    """Fetch Facebook Reel/video rows for WPP and Will Byron pages.

    WPP rows have platform='Facebook', Will Byron rows have platform='Facebook-WB'.
    Skips any page whose credentials are missing from .env.
    """
    rows = []
    for page_id, token, label in [
        (FB_PAGE_ID,  FB_PAGE_ACCESS_TOKEN,  "Facebook"),
        (WB_PAGE_ID,  WB_PAGE_ACCESS_TOKEN,  "Facebook-WB"),
    ]:
        if not page_id or not token:
            print(f"Skipping {label}: missing FB credentials in .env.")
            continue
        rows.extend(_build_fb_page_rows(page_id, token, label, limit=limit))
    return rows


# ─────────────────────────────────────────────────────────────────
# HTML section builder — called by social_dashboard.py
# ─────────────────────────────────────────────────────────────────

# Page configurations: (platform_label, accent_color, display_title)
_FB_PAGE_CONFIGS = [
    ("Facebook",    "#1877F2", "Will Power Protocols Reels", "facebook.com/willpowerprotocols"),
    ("Facebook-WB", "#C9894C", "Will Byron Reels",           "facebook.com/will.byron88"),
]


def _build_fb_page_section_html(fb_rows: pd.DataFrame, color: str) -> str:
    """Build scoreboard + detail table HTML for one Facebook page's rows."""
    total_views     = _safe_int(fb_rows["views"].sum())
    total_15s       = _safe_int(fb_rows.get("facebook_15s_views", pd.Series([0])).sum())
    total_reach     = _safe_int(fb_rows.get("reach", pd.Series([0])).sum())
    total_reactions = _safe_int(fb_rows.get("likes", pd.Series([0])).sum())
    total_comments  = _safe_int(fb_rows.get("comments", pd.Series([0])).sum())
    total_shares    = _safe_int(fb_rows.get("shares", pd.Series([0])).sum())
    total_clicks    = _safe_int(fb_rows.get("clicks", pd.Series([0])).sum())
    total_watch_min = _safe_float(fb_rows.get("estimated_minutes_watched", pd.Series([0])).sum())
    quality_rate    = round((total_15s / total_views) * 100, 2) if total_views else 0.0
    plays_per_reach = round((total_views / total_reach), 2) if total_reach else 0.0

    rows_html = ""
    for _, r in fb_rows.sort_values("views", ascending=False).iterrows():
        book      = _safe_text(r.get("book_or_offer", "—"))
        pillar    = _safe_text(r.get("content_pillar", "—"))
        views     = _safe_int(r.get("views"))
        v15       = _safe_int(r.get("facebook_15s_views"))
        reach     = _safe_int(r.get("reach"))
        qrate     = round((v15 / views) * 100, 1) if views else 0.0
        play_reach = round((views / reach), 2) if reach else 0.0
        avg_sec   = _safe_float(r.get("average_view_duration_seconds"))
        watch_min = _safe_float(r.get("estimated_minutes_watched"))
        reactions = _safe_int(r.get("likes"))
        comments  = _safe_int(r.get("comments"))
        shares    = _safe_int(r.get("shares"))
        clicks    = _safe_int(r.get("clicks"))
        eng       = _safe_float(r.get("engagement_rate_percent"))
        pub       = _safe_text(r.get("published_at", ""))[:10]
        url       = _safe_text(r.get("url", ""))
        caption   = _safe_text(r.get("title_or_caption", ""))[:82]

        rows_html += f"""
        <tr>
            <td>{book}</td>
            <td>{pillar}</td>
            <td style="font-size:11px">{caption}</td>
            <td style="text-align:center"><strong>{views:,}</strong></td>
            <td style="text-align:center">{reach:,}</td>
            <td style="text-align:center">{play_reach:g}</td>
            <td style="text-align:center">{v15:,}</td>
            <td style="text-align:center">{qrate}%</td>
            <td style="text-align:center">{avg_sec:g}</td>
            <td style="text-align:center">{watch_min:g}</td>
            <td style="text-align:center">{reactions}</td>
            <td style="text-align:center">{comments}</td>
            <td style="text-align:center">{shares}</td>
            <td style="text-align:center">{clicks}</td>
            <td style="text-align:center">{eng}%</td>
            <td style="text-align:center;font-size:11px">{pub}</td>
            <td><a href="{url}" target="_blank" style="color:{color}">Open &#x2197;</a></td>
        </tr>"""

    return f"""
    <div class="scoreboard-grid">
        <div class="scoreboard-card" style="border-color:{color}">
            <h2 style="color:{color}">Reach &amp; Viewing</h2>
            <table class="scoreboard-table">
                <tbody>
                    <tr><td>Reel Plays / Main Views</td><td style="text-align:right"><strong>{total_views:,}</strong></td></tr>
                    <tr><td>Reach / Unique Reached</td><td style="text-align:right"><strong>{total_reach:,}</strong></td></tr>
                    <tr><td>Plays per Reached Person</td><td style="text-align:right"><strong>{plays_per_reach:g}</strong></td></tr>
                    <tr><td>15s Quality Views</td><td style="text-align:right"><strong>{total_15s:,}</strong></td></tr>
                    <tr><td>15s Quality Rate</td><td style="text-align:right"><strong>{quality_rate}%</strong></td></tr>
                    <tr><td>Watch Minutes</td><td style="text-align:right"><strong>{total_watch_min:g}</strong></td></tr>
                </tbody>
            </table>
        </div>
        <div class="scoreboard-card" style="border-color:{color}">
            <h2 style="color:{color}">Engagement</h2>
            <table class="scoreboard-table">
                <tbody>
                    <tr><td>Reactions</td><td style="text-align:right"><strong>{total_reactions:,}</strong></td></tr>
                    <tr><td>Comments</td><td style="text-align:right"><strong>{total_comments:,}</strong></td></tr>
                    <tr><td>Shares</td><td style="text-align:right"><strong>{total_shares:,}</strong></td></tr>
                    <tr><td>Clicks</td><td style="text-align:right"><strong>{total_clicks:,}</strong></td></tr>
                </tbody>
            </table>
        </div>
    </div>
    <div class="table-wrap" style="border:1px solid {color};border-radius:14px;margin-bottom:30px">
        <table style="min-width:1400px">
            <thead>
                <tr>
                    <th>Book</th><th>Pillar</th><th>Caption</th>
                    <th style="text-align:center">Reel Plays</th>
                    <th style="text-align:center">Reach</th>
                    <th style="text-align:center">Plays/Reach</th>
                    <th style="text-align:center">15s Views</th>
                    <th style="text-align:center">15s Rate</th>
                    <th style="text-align:center">Avg Sec</th>
                    <th style="text-align:center">Watch Min</th>
                    <th style="text-align:center">React</th>
                    <th style="text-align:center">Comm</th>
                    <th style="text-align:center">Share</th>
                    <th style="text-align:center">Clicks</th>
                    <th style="text-align:center">Eng %</th>
                    <th style="text-align:center">Posted</th>
                    <th>Link</th>
                </tr>
            </thead>
            <tbody>{rows_html}</tbody>
            <tfoot>
                <tr style="background:#101F36">
                    <td colspan="3"><strong style="color:{color}">TOTALS</strong></td>
                    <td style="text-align:center"><strong>{total_views:,}</strong></td>
                    <td style="text-align:center"><strong>{total_reach:,}</strong></td>
                    <td style="text-align:center"><strong>{plays_per_reach:g}</strong></td>
                    <td style="text-align:center"><strong>{total_15s:,}</strong></td>
                    <td style="text-align:center"><strong>{quality_rate}%</strong></td>
                    <td colspan="2"></td>
                    <td style="text-align:center"><strong>{total_reactions:,}</strong></td>
                    <td style="text-align:center"><strong>{total_comments:,}</strong></td>
                    <td style="text-align:center"><strong>{total_shares:,}</strong></td>
                    <td style="text-align:center"><strong>{total_clicks:,}</strong></td>
                    <td colspan="3"></td>
                </tr>
            </tfoot>
        </table>
    </div>"""


def build_facebook_performance_html(df: pd.DataFrame) -> str:
    """Build the Facebook Reels performance section for all pages.

    Renders a sub-section per page (WPP and Will Byron) using the shared
    _build_fb_page_section_html helper. Each sub-section has a scoreboard
    summary and a full detail table.
    """
    all_fb = (
        df[df["platform"].isin(["Facebook", "Facebook-WB"])]
        if not df.empty else pd.DataFrame()
    )

    if all_fb.empty:
        return """
    <h2 style="color:#1877F2">Facebook Reels Performance</h2>
    <div class="scoreboard-card" style="border-color:#1877F2">
        <p style="color:#AAB4C0;font-size:13px">No Facebook Reels/videos found.
        Check FB_PAGE_ID_WPP / FB_PAGE_ID_WB and their tokens in .env.</p>
    </div>"""

    html = f"""
    <h2 style="color:#1877F2">Facebook Reels Performance
        <span style="font-size:12px;color:#AAB4C0;font-weight:normal;margin-left:12px">
        Auto-pulled via Page Post + Video/Reels Insights &middot; {len(all_fb)} Reels/videos across all pages</span>
    </h2>"""

    for platform, color, title, page_url in _FB_PAGE_CONFIGS:
        page_rows = df[df["platform"] == platform] if not df.empty else pd.DataFrame()
        if page_rows.empty:
            continue
        html += f"""
    <h3 style="color:{color};margin-top:24px;margin-bottom:10px">{title}
        <span style="font-size:12px;color:#AAB4C0;font-weight:normal;margin-left:10px">
        {len(page_rows)} videos &middot;
        <a href="https://{page_url}" target="_blank" style="color:#AAB4C0">{page_url}</a>
        </span>
    </h3>"""
        html += _build_fb_page_section_html(page_rows, color)

    return html
