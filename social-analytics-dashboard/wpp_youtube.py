"""
wpp_youtube.py
─────────────────────────────────────────────────────────────────
WPP YouTube Analytics Module — standalone module.

USAGE in social_dashboard.py:
    from wpp_youtube import fetch_youtube_rows

DEPENDENCIES:
    pip install google-auth google-auth-oauthlib google-api-python-client pandas python-dotenv
    .env must contain: YOUTUBE_API_KEY, YOUTUBE_CHANNEL_ID
    client_secret.json must exist in the working directory (OAuth desktop client)
    youtube_token.json is auto-created after first OAuth login
"""

import os
from datetime import date
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

load_dotenv()

# ─────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────

YOUTUBE_API_KEY      = os.getenv("YOUTUBE_API_KEY")
YOUTUBE_CHANNEL_ID   = os.getenv("YOUTUBE_CHANNEL_ID")
YOUTUBE_CHANNEL_HANDLE = os.getenv("YOUTUBE_CHANNEL_HANDLE", "Will Power Protocols")
START_DATE         = "2026-01-01"
TOKEN_FILE         = "youtube_token.json"
CLIENT_SECRET_FILE = "client_secret.json"

SCOPES = [
    "https://www.googleapis.com/auth/yt-analytics.readonly",
    "https://www.googleapis.com/auth/youtube.readonly",
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


def _engagement_rate(views, likes, comments, shares=0, saves=0) -> float:
    views = _safe_int(views)
    if views <= 0:
        return 0.0
    total = _safe_int(likes) + _safe_int(comments) + _safe_int(shares) + _safe_int(saves)
    return round((total / views) * 100, 2)


# ─────────────────────────────────────────────────────────────────
# OAuth
# ─────────────────────────────────────────────────────────────────

def get_youtube_credentials() -> Credentials:
    """Load or refresh OAuth credentials. Opens browser on first run."""
    credentials = None

    if os.path.exists(TOKEN_FILE):
        credentials = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not credentials or not credentials.valid:
        if credentials and credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
        else:
            if not os.path.exists(CLIENT_SECRET_FILE):
                raise FileNotFoundError(
                    f"Missing {CLIENT_SECRET_FILE}. "
                    "Download your OAuth desktop client JSON from Google Cloud "
                    "and rename it to client_secret.json."
                )
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_FILE, SCOPES)
            credentials = flow.run_local_server(port=0)

        with open(TOKEN_FILE, "w", encoding="utf-8") as token:
            token.write(credentials.to_json())

    return credentials


# ─────────────────────────────────────────────────────────────────
# YouTube Data API helpers
# ─────────────────────────────────────────────────────────────────

def get_youtube_uploads_playlist_id(youtube_data) -> str:
    response = youtube_data.channels().list(
        part="contentDetails",
        id=YOUTUBE_CHANNEL_ID,
    ).execute()
    items = response.get("items", [])
    if not items:
        raise ValueError("No YouTube channel found. Check YOUTUBE_CHANNEL_ID in .env.")
    return items[0]["contentDetails"]["relatedPlaylists"]["uploads"]


def get_youtube_recent_video_ids(youtube_data, max_results: int = 50) -> list[str]:
    playlist_id     = get_youtube_uploads_playlist_id(youtube_data)
    video_ids       = []
    next_page_token = None

    while len(video_ids) < max_results:
        response = youtube_data.playlistItems().list(
            part="snippet",
            playlistId=playlist_id,
            maxResults=min(50, max_results - len(video_ids)),
            pageToken=next_page_token,
        ).execute()

        for item in response.get("items", []):
            video_ids.append(item["snippet"]["resourceId"]["videoId"])

        next_page_token = response.get("nextPageToken")
        if not next_page_token:
            break

    return video_ids


def get_youtube_video_metadata(youtube_data, video_ids: list[str]) -> dict:
    metadata = {}
    for i in range(0, len(video_ids), 50):
        batch    = video_ids[i: i + 50]
        response = youtube_data.videos().list(
            part="snippet,statistics",
            id=",".join(batch),
        ).execute()

        for item in response.get("items", []):
            video_id = item.get("id")
            snippet  = item.get("snippet", {})
            stats    = item.get("statistics", {})
            metadata[video_id] = {
                "platform":                      "YouTube",
                "channel_label":                 YOUTUBE_CHANNEL_HANDLE,
                "published_at":                  snippet.get("publishedAt"),
                "media_type":                    "VIDEO",
                "title_or_caption":              snippet.get("title", ""),
                "url":                           f"https://www.youtube.com/watch?v={video_id}",
                "views":                         _safe_int(stats.get("viewCount")),
                "likes":                         _safe_int(stats.get("likeCount")),
                "comments":                      _safe_int(stats.get("commentCount")),
                "shares":                        0,
                "saves":                         0,
                "estimated_minutes_watched":     0,
                "average_view_duration_seconds": 0,
                "subscribers_gained":            0,
                "engagement_rate_percent":       0.0,
            }
    return metadata


# ─────────────────────────────────────────────────────────────────
# YouTube Analytics API helper
# ─────────────────────────────────────────────────────────────────

def get_youtube_analytics_by_video(
    youtube_analytics,
    start_date: str = START_DATE,
    end_date: str | None = None,
) -> dict:
    if end_date is None:
        end_date = date.today().isoformat()

    response = youtube_analytics.reports().query(
        ids="channel==MINE",
        startDate=start_date,
        endDate=end_date,
        metrics=(
            "views,"
            "estimatedMinutesWatched,"
            "averageViewDuration,"
            "likes,"
            "comments,"
            "shares,"
            "subscribersGained"
        ),
        dimensions="video",
        sort="-views",
        maxResults=200,
    ).execute()

    headers   = [h["name"] for h in response.get("columnHeaders", [])]
    analytics = {}

    for row in response.get("rows", []):
        record   = dict(zip(headers, row))
        video_id = record.get("video")
        if not video_id:
            continue
        analytics[video_id] = {
            "views":                         _safe_int(record.get("views")),
            "estimated_minutes_watched":     _safe_int(record.get("estimatedMinutesWatched")),
            "average_view_duration_seconds": _safe_int(record.get("averageViewDuration")),
            "likes":                         _safe_int(record.get("likes")),
            "comments":                      _safe_int(record.get("comments")),
            "shares":                        _safe_int(record.get("shares")),
            "subscribers_gained":            _safe_int(record.get("subscribersGained")),
        }

    return analytics


# ─────────────────────────────────────────────────────────────────
# Main fetcher — called by social_dashboard.py
# ─────────────────────────────────────────────────────────────────

def fetch_youtube_rows(max_results: int = 50) -> list[dict]:
    """Fetch all YouTube video rows for the analytics dataframe."""
    if not YOUTUBE_API_KEY or not YOUTUBE_CHANNEL_ID:
        print("Skipping YouTube: missing YOUTUBE_API_KEY or YOUTUBE_CHANNEL_ID in .env.")
        return []

    credentials      = get_youtube_credentials()
    youtube_data     = build("youtube", "v3", developerKey=YOUTUBE_API_KEY)
    youtube_analytics = build("youtubeAnalytics", "v2", credentials=credentials)

    video_ids = get_youtube_recent_video_ids(youtube_data, max_results=max_results)
    metadata  = get_youtube_video_metadata(youtube_data, video_ids)
    analytics = get_youtube_analytics_by_video(youtube_analytics, start_date=START_DATE)

    rows = []
    for video_id in video_ids:
        base         = metadata.get(video_id, {})
        private_stats = analytics.get(video_id, {})

        views    = private_stats.get("views",    base.get("views",    0))
        likes    = private_stats.get("likes",    base.get("likes",    0))
        comments = private_stats.get("comments", base.get("comments", 0))
        shares   = private_stats.get("shares", 0)

        rows.append({
            **base,
            "views":                         views,
            "likes":                         likes,
            "comments":                      comments,
            "shares":                        shares,
            "saves":                         0,
            "estimated_minutes_watched":     private_stats.get("estimated_minutes_watched", 0),
            "average_view_duration_seconds": private_stats.get("average_view_duration_seconds", 0),
            "subscribers_gained":            private_stats.get("subscribers_gained", 0),
            "engagement_rate_percent":       _engagement_rate(views, likes, comments, shares, 0),
        })

    return rows
