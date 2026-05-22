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

IG_ACCESS_TOKEN = os.getenv("IG_ACCESS_TOKEN")
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
