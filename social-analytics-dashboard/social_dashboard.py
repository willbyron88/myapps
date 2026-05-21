import os
from pathlib import Path
from datetime import date

import pandas as pd
import requests
from dotenv import load_dotenv

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build


# ============================================================
# Configuration
# ============================================================

load_dotenv()

# ---------- Instagram ----------
IG_ACCESS_TOKEN = os.getenv("IG_ACCESS_TOKEN")
IG_USER_ID = os.getenv("IG_USER_ID")
IG_BASE_URL = "https://graph.instagram.com/v21.0"

# ---------- YouTube ----------
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
YOUTUBE_CHANNEL_ID = os.getenv("YOUTUBE_CHANNEL_ID")

SCOPES = [
    "https://www.googleapis.com/auth/yt-analytics.readonly",
    "https://www.googleapis.com/auth/youtube.readonly",
]

TOKEN_FILE = "youtube_token.json"
CLIENT_SECRET_FILE = "client_secret.json"

# ---------- Facebook ----------
FB_PAGE_ID = os.getenv("FB_PAGE_ID")
FB_PAGE_ACCESS_TOKEN = os.getenv("FB_PAGE_ACCESS_TOKEN")
FB_BASE_URL = "https://graph.facebook.com/v19.0"
MAX_FACEBOOK_POSTS = 50

OUTPUT_DIR = Path("docs")
OUTPUT_DIR.mkdir(exist_ok=True)

START_DATE = "2026-01-01"
MAX_INSTAGRAM_POSTS = 50
MAX_YOUTUBE_VIDEOS = 50
MIN_ENGAGEMENT_VIEWS = 5

# ---------- SQLite Database ----------
WPP_DB_FILE = "wpp.db"
POST_DAYS = ["Monday", "Tuesday", "Thursday", "Saturday", "Sunday"]


# ============================================================
# Helpers
# ============================================================

def safe_int(value):
    try:
        if pd.isna(value):
            return 0
        return int(float(value))
    except Exception:
        return 0


def safe_float(value):
    try:
        if pd.isna(value):
            return 0.0
        return float(value)
    except Exception:
        return 0.0


def safe_text(value):
    if value is None:
        return ""
    return str(value)


def truncate_text(text, max_len=145):
    text = safe_text(text).replace("\n", " ").strip()
    if len(text) > max_len:
        return text[:max_len] + "..."
    return text


def engagement_rate(views, likes, comments, shares=0, saves=0):
    views = safe_int(views)
    if views <= 0:
        return 0.0
    total_engagement = (
        safe_int(likes)
        + safe_int(comments)
        + safe_int(shares)
        + safe_int(saves)
    )
    return round((total_engagement / views) * 100, 2)


def normalize_url(url):
    """
    Normalize URL for matching — extract canonical ID from any format.
    YouTube IDs are always exactly 11 chars.
    Handles all known URL formats for YouTube, Instagram, X, and Facebook.
    """
    import re
    url = str(url).strip().rstrip("/")

    # YouTube: extract 11-char video ID from any URL format
    yt_patterns = [
        r'youtube\.com/shorts/([a-zA-Z0-9_-]{11})',
        r'youtube\.com/watch\?.*?v=([a-zA-Z0-9_-]{11})',
        r'youtu\.be/([a-zA-Z0-9_-]{11})',
        r'youtube\.com/embed/([a-zA-Z0-9_-]{11})',
        r'youtube\.com/v/([a-zA-Z0-9_-]{11})',
    ]
    for pattern in yt_patterns:
        match = re.search(pattern, url, re.IGNORECASE)
        if match:
            return f"https://www.youtube.com/watch?v={match.group(1)}"

    # Instagram: /reel/, /p/, /tv/ all normalize to /reel/
    ig_match = re.search(r'instagram\.com/(?:reel|p|tv)/([a-zA-Z0-9_-]+)', url, re.IGNORECASE)
    if ig_match:
        return f"https://www.instagram.com/reel/{ig_match.group(1)}"

    # X/Twitter: normalize domain, strip query params
    x_match = re.search(r'(?:twitter|x)\.com/([^/?]+)/status/(\d+)', url, re.IGNORECASE)
    if x_match:
        return f"https://x.com/{x_match.group(1)}/status/{x_match.group(2)}"

    # Facebook: normalize permalink formats
    fb_match = re.search(r'facebook\.com/(?:permalink/php\?story_fbid=|[^/]+/posts?/|[^/]+/videos?/)(\d+)', url, re.IGNORECASE)
    if fb_match:
        return f"https://www.facebook.com/permalink/{fb_match.group(1)}"

    return url.lower()


# ============================================================
# Content Map & Queue
# ============================================================

def load_wpp_content():
    """
    Load content from wpp.db SQLite database.

    Returns (content_map_df, queue_df):
      content_map_df — posted rows with URLs, ready for analytics merging
      queue_df       — unposted Short rows for the weekly schedule

    If wpp.db is missing, returns (empty, empty).
    """
    import sqlite3

    db_path = None
    for candidate in [
        Path(WPP_DB_FILE),
        Path(__file__).parent / WPP_DB_FILE,
        Path.cwd() / WPP_DB_FILE,
    ]:
        if candidate.exists():
            db_path = candidate
            break

    if db_path is None:
        print(f"No {WPP_DB_FILE} found — running without enrichment or action plan.")
        print(f"Searched: {Path.cwd()}")
        return pd.DataFrame(), pd.DataFrame()

    try:
        conn = sqlite3.connect(db_path)

        df = pd.read_sql_query("""
            SELECT
                c.content_id,
                c.book_key,
                b.title          AS book_or_offer,
                b.brand,
                c.content_type,
                CAST(c.short_num   AS TEXT) AS short_num,
                CAST(c.episode_num AS TEXT) AS episode_num,
                c.content_pillar,
                c.campaign,
                c.script_topic,
                c.mp3_ready,
                c.ig_account     AS account,
                c.x_account,
                c.posted,
                COALESCE(c.instagram_url, '') AS instagram_url,
                COALESCE(c.youtube_url,   '') AS youtube_url,
                COALESCE(c.x_url,         '') AS x_url,
                COALESCE(c.facebook_url,  '') AS facebook_url,
                COALESCE(c.post_date,     '') AS post_date,
                COALESCE(c.notes,         '') AS notes
            FROM content c
            JOIN books b ON c.book_key = b.book_key
            ORDER BY c.book_key, c.content_type, c.short_num, c.episode_num
        """, conn)

        conn.close()
        df = df.fillna("")
        print(f"Loaded {WPP_DB_FILE}: {len(df)} content rows.")

    except Exception as e:
        print(f"Database error: {e}")
        return pd.DataFrame(), pd.DataFrame()

    # Build content_map from posted rows with at least one URL
    enrich_rows = []
    for _, row in df.iterrows():
        ig_url = str(row.get("instagram_url", "")).strip()
        yt_url = str(row.get("youtube_url",   "")).strip()
        x_url  = str(row.get("x_url",         "")).strip()
        fb_url = str(row.get("facebook_url",  "")).strip()
        already_crossposted = bool(ig_url and yt_url)

        for url in [ig_url, yt_url, x_url, fb_url]:
            if url:
                enrich_rows.append({
                    "url":               url,
                    "url_normalized":    normalize_url(url),
                    "book_or_offer":     str(row.get("book_or_offer", "")),
                    "content_type":      str(row.get("content_type", "Short")),
                    "content_pillar":    str(row.get("content_pillar", "")),
                    "campaign":          str(row.get("campaign", "")),
                    "short_num":         str(row.get("short_num", "")),
                    "episode_num":       str(row.get("episode_num", "")),
                    "account":           str(row.get("account", "")),
                    "x_account":         str(row.get("x_account", "")),
                    "already_crossposted": already_crossposted,
                    "on_x":              bool(x_url),
                })

    content_map = pd.DataFrame(enrich_rows) if enrich_rows else pd.DataFrame()
    print(f"  → Enrichment URLs: {len(content_map)}")

    # Queue: unposted Shorts only
    all_unposted = df[df["posted"].astype(str).str.upper().eq("N")].copy()
    queue_df = all_unposted[
        all_unposted["content_type"].fillna("Short").eq("Short")
    ].copy()
    ep_queue = all_unposted[
        all_unposted["content_type"].fillna("Short").ne("Short")
    ].copy()

    wpp_count = queue_df[queue_df["account"].ne("@will.byron88")].shape[0]
    wb_count  = queue_df[queue_df["account"].eq("@will.byron88")].shape[0]
    print(f"  → Short queue: {len(queue_df)} ({wpp_count} WPP · {wb_count} Will Byron)")
    if len(ep_queue):
        print(f"  → Episode queue: {len(ep_queue)} unposted video/podcast episodes")

    return content_map, queue_df


def merge_content_map(df, content_map):
    """
    Left-join main analytics df with content_map on normalized URL.
    Unmatched rows get '—' for enrichment columns.
    """
    enrich_cols = ["book_or_offer", "content_pillar", "campaign", "cta", "short_num"]

    if content_map.empty:
        for col in enrich_cols:
            df[col] = "—"
        return df

    df["url_normalized"] = df["url"].apply(normalize_url)

    src_cols = ["url_normalized"] + [
        c for c in enrich_cols if c in content_map.columns
    ]
    if "already_crossposted" in content_map.columns:
        src_cols.append("already_crossposted")

    df = df.merge(content_map[src_cols], on="url_normalized", how="left")

    for col in enrich_cols:
        if col in df.columns:
            df[col] = df[col].fillna("—")
        else:
            df[col] = "—"

    matched = df["book_or_offer"].ne("—").sum()
    print(f"Content map: {matched} of {len(df)} posts matched.")
    return df


# ============================================================
# Content Intelligence
# ============================================================

def add_content_intelligence(df):
    df = df.copy()

    if df.empty:
        df["content_signal"] = ""
        df["recommended_action"] = ""
        return df

    views_75 = df["views"].quantile(0.75)
    engagement_75 = df["engagement_rate_percent"].quantile(0.75)

    youtube_df = df[df["platform"] == "YouTube"]
    if not youtube_df.empty:
        avg_duration_75 = youtube_df["average_view_duration_seconds"].quantile(0.75)
        watch_minutes_75 = youtube_df["estimated_minutes_watched"].quantile(0.75)
    else:
        avg_duration_75 = 0
        watch_minutes_75 = 0

    signals = []
    actions = []

    for _, row in df.iterrows():
        row_signals = []
        row_actions = []

        platform = safe_text(row.get("platform"))
        views = safe_int(row.get("views"))
        engagement = safe_float(row.get("engagement_rate_percent"))
        avg_duration = safe_int(row.get("average_view_duration_seconds"))
        watch_minutes = safe_int(row.get("estimated_minutes_watched"))

        if views >= views_75 and views > 0:
            row_signals.append("Winner")
            row_actions.append("Study the hook/title and make a follow-up.")

        if platform == "YouTube" and avg_duration >= avg_duration_75 and avg_duration > 0:
            row_signals.append("Sticky")
            row_actions.append("Audience watched longer than usual; expand this topic.")

        if platform == "YouTube" and watch_minutes >= watch_minutes_75 and watch_minutes > 0:
            row_signals.append("Watch-Time Winner")
            row_actions.append("This generated real watch time; create a deeper version.")

        if views >= MIN_ENGAGEMENT_VIEWS and engagement >= engagement_75 and engagement > 0:
            row_signals.append("Promising")
            row_actions.append("Good engagement; test a stronger hook or repost angle.")

        if platform == "Instagram" and views >= views_75 and views > 0:
            row_signals.append("Repurpose Candidate")
            row_actions.append("Turn this Reel into a YouTube Short.")

        if platform == "YouTube" and (watch_minutes > 0 or engagement > 0):
            row_signals.append("Repurpose Candidate")
            row_actions.append("Turn this Short into an Instagram Reel.")

        row_signals = list(dict.fromkeys(row_signals))
        row_actions = list(dict.fromkeys(row_actions))

        signals.append(", ".join(row_signals) if row_signals else "Watch")
        actions.append(" ".join(row_actions) if row_actions else "Keep collecting data.")

    df["content_signal"] = signals
    df["recommended_action"] = actions
    return df


# ============================================================
# Monday Action Plan Builder
# ============================================================

def generate_monday_plan(df, queue_df):
    has_content_map = (
        "book_or_offer" in df.columns
        and df["book_or_offer"].ne("—").any()
    )
    has_queue = not queue_df.empty

    repurpose = []
    already_done = df.get("already_crossposted", pd.Series(False, index=df.index))
    repurpose_df = df[
        df["content_signal"].str.contains("Repurpose", na=False) &
        ~already_done.infer_objects(copy=False).fillna(False).astype(bool)
    ].head(5)

    for _, row in repurpose_df.iterrows():
        platform = safe_text(row.get("platform"))
        action = (
            "Cross-post to YouTube Shorts"
            if platform == "Instagram"
            else "Cross-post to Instagram Reels"
        )
        repurpose.append({
            "platform": platform,
            "title": truncate_text(safe_text(row.get("title_or_caption")), 70),
            "url": safe_text(row.get("url")),
            "views": safe_int(row.get("views")),
            "signal": safe_text(row.get("content_signal")),
            "action": action,
            "book": safe_text(row.get("book_or_offer", "—")),
        })

    pillar_winner = "—"
    pillar_scores = {}
    if has_content_map:
        known = df[df["content_pillar"].ne("—")]
        if not known.empty:
            pillar_views = known.groupby("content_pillar")["views"].sum()
            if not pillar_views.empty:
                pillar_winner = pillar_views.idxmax()
                pillar_scores = pillar_views.sort_values(ascending=False).to_dict()

    book_test_signals = []
    if has_content_map:
        test_mask = df["book_or_offer"].str.contains(
            "Book Test|TEST", case=False, na=False
        )
        test_df = df[test_mask]
        for book, group in test_df.groupby("book_or_offer"):
            total_views = safe_int(group["views"].sum())
            avg_eng = round(group["engagement_rate_percent"].mean(), 2)
            winners = group["content_signal"].str.contains("Winner", na=False).sum()
            book_test_signals.append({
                "book_test": book,
                "posts": len(group),
                "total_views": total_views,
                "avg_engagement": avg_eng,
                "winners": int(winners),
                "signal": (
                    "⭐ Strong — consider writing this book"
                    if winners > 0
                    else "Collecting data"
                ),
            })

    schedule = []
    unposted_count = 0

    if has_queue:
        if "posted" in queue_df.columns:
            unposted = queue_df[
                queue_df["posted"].astype(str).str.upper().ne("Y")
            ].copy()
        else:
            unposted = queue_df.copy()

        unposted_count = len(unposted)

        winner_books = set()
        if has_content_map:
            winners = df[df["content_signal"].str.contains("Winner", na=False)]
            winner_books = set(winners["book_or_offer"].dropna().unique())

        book_groups = {}
        for _, row in unposted.iterrows():
            book = str(row.get("book_or_offer", "Unknown"))
            if book not in book_groups:
                book_groups[book] = []
            book_groups[book].append(row.to_dict())

        sorted_books = sorted(
            book_groups.keys(),
            key=lambda b: (0 if b in winner_books else 1, b),
        )

        last_book = None
        book_cycle = list(sorted_books)

        for day in POST_DAYS:
            if not any(book_groups.values()):
                break

            chosen_book = None
            for book in book_cycle:
                if book != last_book and book_groups.get(book):
                    chosen_book = book
                    break

            if not chosen_book:
                for book in book_cycle:
                    if book_groups.get(book):
                        chosen_book = book
                        break

            if not chosen_book:
                break

            item = book_groups[chosen_book].pop(0)
            note = "⭐ Winner book" if chosen_book in winner_books else ""
            account = str(item.get("account", "@willpowerprotocols"))
            x_account = str(item.get("x_account", "@wpprotocols"))

            schedule.append({
                "day": day,
                "book": chosen_book,
                "short_num": str(item.get("short_num", "?")),
                "topic": str(item.get("script_topic", "")),
                "pillar": str(item.get("content_pillar", "")),
                "account": account,
                "x_account": x_account,
                "note": note,
            })

            last_book = chosen_book
            book_cycle = (
                [b for b in book_cycle if b != chosen_book] + [chosen_book]
            )

    return {
        "schedule": schedule,
        "repurpose": repurpose,
        "pillar_winner": pillar_winner,
        "pillar_scores": pillar_scores,
        "book_test_signals": book_test_signals,
        "unposted_count": unposted_count,
        "has_queue": has_queue,
        "has_content_map": has_content_map,
    }


def build_monday_plan_html(plan):
    today_str = date.today().strftime("%A, %B %d, %Y")

    if plan["schedule"]:
        sched_rows = ""
        for s in plan["schedule"]:
            note_html = (
                f'<span class="winner-note">{s["note"]}</span>'
                if s["note"]
                else ""
            )
            account = s.get("account", "@willpowerprotocols")
            x_account = s.get("x_account", "@wpprotocols")
            wb = account == "@will.byron88"
            account_html = (
                f'<span style="color:#C9894C;font-size:11px;font-weight:bold">{account}</span>'
                if wb else
                f'<span style="color:#AAB4C0;font-size:11px">{account}</span>'
            )
            x_html = (
                f'<span style="color:#C9894C;font-size:11px">@willbyron</span>'
                if wb else
                f'<span style="color:#1DA1F2;font-size:11px">{x_account}</span>'
            )

            sched_rows += f"""
            <tr>
                <td><strong>{s["day"]}</strong></td>
                <td>{s["book"]}</td>
                <td style="text-align:center">{s["short_num"]}</td>
                <td>{s["topic"]}</td>
                <td>{s["pillar"]}</td>
                <td>{account_html}</td>
                <td>{x_html}</td>
                <td>{note_html}</td>
            </tr>"""

        schedule_html = f"""
        <div class="action-section">
            <h3>This Week — 5 Posts · 4 Platforms</h3>
            <table class="schedule-table">
                <thead>
                    <tr>
                        <th>Day</th>
                        <th>Book / Offer</th>
                        <th style="text-align:center">Short #</th>
                        <th>Topic</th>
                        <th>Pillar</th>
                        <th>IG / YT / FB</th>
                        <th style="color:#1DA1F2">X</th>
                        <th>Note</th>
                    </tr>
                </thead>
                <tbody>{sched_rows}</tbody>
            </table>
            <p class="action-note">{max(plan["unposted_count"] - len(plan["schedule"]), 0)} shorts remaining in queue after this week.</p>
        </div>"""
    elif not plan["has_queue"]:
        schedule_html = """
        <div class="action-section">
            <h3>This Week — 5 Posts</h3>
            <p class="action-note">Add <strong>wpp.db</strong> to unlock the weekly schedule.</p>
        </div>"""
    else:
        schedule_html = """
        <div class="action-section">
            <h3>This Week — 5 Posts</h3>
            <p class="action-note">Queue is empty. Add more unposted rows to wpp.db.</p>
        </div>"""

    if plan["repurpose"]:
        rep_rows = ""
        for r in plan["repurpose"]:
            rep_rows += f"""
            <div class="repurpose-item">
                <span class="platform-badge">{r["platform"]}</span>
                <div>
                    <div style="margin-bottom:2px">{r["title"]}</div>
                    <div style="color:#C9A84C;font-size:11px">→ {r["action"]}
                        &nbsp;·&nbsp; {r["views"]:,} views
                        &nbsp;·&nbsp; <a href="{r["url"]}" target="_blank">Open</a>
                    </div>
                </div>
            </div>"""
        repurpose_html = f"""
        <div class="action-section">
            <h3>Repurpose Queue — Cross-Post These Now</h3>
            {rep_rows}
        </div>"""
    else:
        repurpose_html = ""

    if plan["pillar_winner"] != "—":
        pillar_html = f"""
        <div class="action-section">
            <h3>Pillar Signal</h3>
            <p style="margin:0">Top performing pillar: <strong style="color:#C9A84C">{plan["pillar_winner"]}</strong> —
            prioritize this topic when writing new scripts or testing campaigns.</p>
        </div>"""
    else:
        pillar_html = ""

    if plan["book_test_signals"]:
        test_rows = ""
        for t in plan["book_test_signals"]:
            test_rows += f"""
            <tr>
                <td>{t["book_test"]}</td>
                <td style="text-align:center">{t["posts"]}</td>
                <td style="text-align:center">{t["total_views"]:,}</td>
                <td style="text-align:center">{t["avg_engagement"]}%</td>
                <td style="text-align:center">{t["winners"]}</td>
                <td>{t["signal"]}</td>
            </tr>"""
        test_html = f"""
        <div class="action-section">
            <h3>Book Test Signals — New Book Candidates</h3>
            <table class="schedule-table">
                <thead>
                    <tr>
                        <th>Book Idea</th>
                        <th style="text-align:center">Posts</th>
                        <th style="text-align:center">Views</th>
                        <th style="text-align:center">Eng %</th>
                        <th style="text-align:center">Winners</th>
                        <th>Signal</th>
                    </tr>
                </thead>
                <tbody>{test_rows}</tbody>
            </table>
        </div>"""
    else:
        test_html = ""

    return f"""
    <div class="action-plan">
        <h2>Monday Action Plan — {today_str}</h2>
        {schedule_html}
        {repurpose_html}
        {pillar_html}
        {test_html}
    </div>"""


# ============================================================
# Scoreboard Builders
# ============================================================

def build_scoreboard_html(df):
    has_enrichment = (
        "book_or_offer" in df.columns
        and df["book_or_offer"].ne("—").any()
    )

    if not has_enrichment:
        return ""

    known = df[df["book_or_offer"].ne("—")]
    book_agg = (
        known.groupby("book_or_offer")
        .agg(
            posts=("views", "count"),
            total_views=("views", "sum"),
            avg_eng=("engagement_rate_percent", "mean"),
        )
        .reset_index()
    )
    book_agg["avg_eng"] = book_agg["avg_eng"].round(2)
    book_agg["total_views"] = book_agg["total_views"].astype(int)

    winner_counts = (
        df[df["content_signal"].str.contains("Winner", na=False)]
        .groupby("book_or_offer")
        .size()
        .reset_index(name="winners")
    )
    book_agg = book_agg.merge(winner_counts, on="book_or_offer", how="left")
    book_agg["winners"] = book_agg["winners"].fillna(0).astype(int)
    book_agg = book_agg.sort_values("total_views", ascending=False)

    book_rows = ""
    for _, r in book_agg.iterrows():
        star = "⭐ " if r["winners"] > 0 else ""
        book_rows += f"""
        <tr>
            <td>{star}{r["book_or_offer"]}</td>
            <td style="text-align:center">{r["posts"]}</td>
            <td style="text-align:center">{r["total_views"]:,}</td>
            <td style="text-align:center">{r["avg_eng"]}%</td>
            <td style="text-align:center">{r["winners"]}</td>
        </tr>"""

    book_scoreboard = f"""
    <div class="scoreboard-card">
        <h2>Book Scoreboard</h2>
        <table class="scoreboard-table">
            <thead>
                <tr>
                    <th>Book / Offer</th>
                    <th style="text-align:center">Posts</th>
                    <th style="text-align:center">Views</th>
                    <th style="text-align:center">Avg Eng</th>
                    <th style="text-align:center">Winners</th>
                </tr>
            </thead>
            <tbody>{book_rows}</tbody>
        </table>
    </div>"""

    pillar_known = df[df["content_pillar"].ne("—")]
    pillar_agg = (
        pillar_known.groupby("content_pillar")
        .agg(
            posts=("views", "count"),
            total_views=("views", "sum"),
            avg_eng=("engagement_rate_percent", "mean"),
        )
        .reset_index()
    )
    pillar_agg["avg_eng"] = pillar_agg["avg_eng"].round(2)
    pillar_agg["total_views"] = pillar_agg["total_views"].astype(int)
    pillar_agg = pillar_agg.sort_values("total_views", ascending=False)

    pillar_rows = ""
    for _, r in pillar_agg.iterrows():
        pillar_rows += f"""
        <tr>
            <td>{r["content_pillar"]}</td>
            <td style="text-align:center">{r["posts"]}</td>
            <td style="text-align:center">{r["total_views"]:,}</td>
            <td style="text-align:center">{r["avg_eng"]}%</td>
        </tr>"""

    pillar_scoreboard = f"""
    <div class="scoreboard-card">
        <h2>Pillar Scoreboard</h2>
        <table class="scoreboard-table">
            <thead>
                <tr>
                    <th>Pillar</th>
                    <th style="text-align:center">Posts</th>
                    <th style="text-align:center">Views</th>
                    <th style="text-align:center">Avg Eng</th>
                </tr>
            </thead>
            <tbody>{pillar_rows}</tbody>
        </table>
    </div>"""

    return f"""
    <h2>Performance by Book &amp; Pillar</h2>
    <div class="scoreboard-grid">
        {book_scoreboard}
        {pillar_scoreboard}
    </div>"""


def build_x_performance_html(content_map):
    import sqlite3

    db_path = None
    for candidate in [
        Path(WPP_DB_FILE),
        Path(__file__).parent / WPP_DB_FILE,
        Path.cwd() / WPP_DB_FILE,
    ]:
        if candidate.exists():
            db_path = candidate
            break

    if db_path is None:
        return ""

    try:
        conn = sqlite3.connect(db_path)
        x_data = pd.read_sql_query("""
            SELECT
                xa.snapshot_date,
                xa.x_url,
                b.title  AS book_or_offer,
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
        x_data = x_data.fillna("")
        x_content = x_content.fillna("")

    except Exception as e:
        print(f"X analytics query error: {e}")
        return ""

    if x_data.empty and x_content.empty:
        return """
    <h2 style="color:#1DA1F2">X (@wpprotocols) Performance</h2>
    <div class="scoreboard-card" style="border-color:#1DA1F2">
        <p style="color:#AAB4C0;font-size:13px">No X analytics yet.
        Use the SQL cheat sheet to INSERT weekly metrics from X Premium export.</p>
    </div>"""

    if x_data.empty:
        untracked = len(x_content)
        return f"""
    <h2 style="color:#1DA1F2">X (@wpprotocols) Performance
        <span style="font-size:12px;color:#AAB4C0;font-weight:normal;margin-left:12px">
        {untracked} X posts live — add analytics via INSERT INTO x_analytics in DB Browser</span>
    </h2>
    <div class="scoreboard-card" style="border-color:#1DA1F2">
        <p style="color:#AAB4C0;font-size:13px">
        X posts are live but analytics not yet entered.
        Use the SQL cheat sheet to INSERT weekly metrics from X Premium export.
        </p>
    </div>"""

    total_views   = int(x_data["x_views"].astype(int).sum())
    total_likes   = int(x_data["x_likes"].astype(int).sum())
    total_reposts = int(x_data["x_reposts"].astype(int).sum())

    rows_html = ""
    for _, r in x_data.iterrows():
        views   = int(r.get("x_views", 0))
        likes   = int(r.get("x_likes", 0))
        reposts = int(r.get("x_reposts", 0))
        eng = round((likes + reposts) / views * 100, 1) if views > 0 else 0.0
        x_url = str(r.get("x_url", ""))

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
        Update via INSERT INTO x_analytics in DB Browser weekly</span>
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


# ============================================================
# Facebook
# ============================================================

def fetch_facebook_rows(limit=50):
    if not FB_PAGE_ID or not FB_PAGE_ACCESS_TOKEN:
        print("Skipping Facebook: missing FB_PAGE_ID or FB_PAGE_ACCESS_TOKEN.")
        return []

    try:
        url = f"{FB_BASE_URL}/{FB_PAGE_ID}/posts"
        params = {
            "fields": "id,message,created_time,permalink_url",
            "limit": limit,
            "access_token": FB_PAGE_ACCESS_TOKEN,
        }
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        posts = response.json().get("data", [])
        print(f"Facebook: {len(posts)} posts found.")
    except Exception as e:
        print(f"Facebook posts fetch error: {e}")
        return []

    rows = []
    for post in posts:
        post_id = post.get("id")
        permalink = post.get("permalink_url", "")
        message = truncate_text(post.get("message", ""), 145)
        created = post.get("created_time", "")

        views = 0
        unique_views = 0
        reach = 0
        reactions = 0
        clicks = 0

        try:
            ins_url = f"{FB_BASE_URL}/{post_id}/insights"
            ins_params = {
                "metric": (
                    "post_video_views,"
                    "post_video_views_unique,"
                    "post_impressions_unique,"
                    "post_reactions_by_type_total,"
                    "post_clicks"
                ),
                "access_token": FB_PAGE_ACCESS_TOKEN,
            }
            ins_response = requests.get(ins_url, params=ins_params, timeout=30)
            if ins_response.status_code == 200:
                for item in ins_response.json().get("data", []):
                    name = item.get("name")
                    values = item.get("values", [{}])
                    val = values[-1].get("value", 0) if values else 0
                    if name == "post_video_views":
                        views = safe_int(val)
                    elif name == "post_video_views_unique":
                        unique_views = safe_int(val)
                    elif name == "post_impressions_unique":
                        reach = safe_int(val)
                    elif name == "post_reactions_by_type_total":
                        reactions = sum(safe_int(v) for v in val.values()) if isinstance(val, dict) else 0
                    elif name == "post_clicks":
                        clicks = safe_int(val)
        except Exception as e:
            print(f"Facebook insights error for {post_id}: {e}")

        rows.append({
            "platform": "Facebook",
            "published_at": created,
            "media_type": "VIDEO",
            "title_or_caption": message,
            "url": permalink,
            "views": views,
            "likes": reactions,
            "comments": 0,
            "shares": 0,
            "saves": 0,
            "estimated_minutes_watched": 0,
            "average_view_duration_seconds": 0,
            "subscribers_gained": 0,
            "engagement_rate_percent": engagement_rate(views, reactions, 0, 0, 0),
        })

    print(f"Facebook: {len(rows)} rows built.")
    return rows


def build_facebook_performance_html(df):
    """Build Facebook performance section from fetched data."""
    fb_rows = df[df["platform"] == "Facebook"] if not df.empty else pd.DataFrame()

    if fb_rows.empty:
        return """
    <h2 style="color:#1877F2">Facebook Performance</h2>
    <div class="scoreboard-card" style="border-color:#1877F2">
        <p style="color:#AAB4C0;font-size:13px">No Facebook posts found.
        Make sure FB_PAGE_ID and FB_PAGE_ACCESS_TOKEN are set in .env,
        and that you have posts on the Will Power Protocols page.</p>
    </div>"""

    total_views = safe_int(fb_rows["views"].sum())
    total_reach = safe_int(fb_rows.get("reach", pd.Series([0])).sum()) if "reach" in fb_rows.columns else 0
    total_reactions = safe_int(fb_rows["likes"].sum())
    total_clicks = safe_int(fb_rows.get("clicks", pd.Series([0])).sum()) if "clicks" in fb_rows.columns else 0

    rows_html = ""
    for _, r in fb_rows.sort_values("views", ascending=False).iterrows():
        book = safe_text(r.get("book_or_offer", "—"))
        pillar = safe_text(r.get("content_pillar", "—"))
        views = safe_int(r.get("views"))
        reactions = safe_int(r.get("likes"))
        eng = safe_float(r.get("engagement_rate_percent"))
        pub = safe_text(r.get("published_at", ""))[:10]
        url = safe_text(r.get("url", ""))
        caption = safe_text(r.get("title_or_caption", ""))[:60]

        rows_html += f"""
        <tr>
            <td>{book}</td>
            <td>{pillar}</td>
            <td style="font-size:11px">{caption}</td>
            <td style="text-align:center">{views:,}</td>
            <td style="text-align:center">{reactions}</td>
            <td style="text-align:center">{eng}%</td>
            <td style="text-align:center;font-size:11px">{pub}</td>
            <td><a href="{url}" target="_blank" style="color:#1877F2">Open ↗</a></td>
        </tr>"""

    return f"""
    <h2 style="color:#1877F2">Facebook Performance
        <span style="font-size:12px;color:#AAB4C0;font-weight:normal;margin-left:12px">
        Auto-pulled via Pages API · {len(fb_rows)} posts</span>
    </h2>
    <div class="table-wrap" style="border:1px solid #1877F2;border-radius:14px;margin-bottom:30px">
        <table style="min-width:800px">
            <thead>
                <tr>
                    <th>Book</th>
                    <th>Pillar</th>
                    <th>Caption</th>
                    <th style="text-align:center">Video Views</th>
                    <th style="text-align:center">Reactions</th>
                    <th style="text-align:center">Eng %</th>
                    <th style="text-align:center">Posted</th>
                    <th>Link</th>
                </tr>
            </thead>
            <tbody>{rows_html}</tbody>
            <tfoot>
                <tr style="background:#101F36">
                    <td colspan="3"><strong style="color:#1877F2">TOTALS</strong></td>
                    <td style="text-align:center"><strong>{total_views:,}</strong></td>
                    <td style="text-align:center"><strong>{total_reactions}</strong></td>
                    <td colspan="3"></td>
                </tr>
            </tfoot>
        </table>
    </div>"""


# ============================================================
# Instagram
# ============================================================

def ig_get_json(url, params):
    response = requests.get(url, params=params, timeout=30)
    if response.status_code != 200:
        print("\nInstagram API error:")
        print("URL:", url)
        print("Status:", response.status_code)
        print(response.text)
    response.raise_for_status()
    return response.json()


def get_instagram_recent_media(limit=50):
    if not IG_ACCESS_TOKEN or not IG_USER_ID:
        print("Skipping Instagram: missing IG_ACCESS_TOKEN or IG_USER_ID.")
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


def get_instagram_insights(media_id):
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
            results[metric_name] = sum(safe_int(v) for v in raw_value.values())
        else:
            results[metric_name] = safe_int(raw_value)

    return results


def fetch_instagram_rows(limit=50):
    rows = []
    media_items = get_instagram_recent_media(limit=limit)

    for item in media_items:
        media_id = item.get("id")
        caption = truncate_text(item.get("caption", ""))

        try:
            insights = get_instagram_insights(media_id)
        except Exception as e:
            print(f"Could not fetch Instagram insights for media {media_id}: {e}")
            insights = {}

        views = safe_int(insights.get("views")) or safe_int(insights.get("reach"))
        likes = safe_int(insights.get("likes")) or safe_int(item.get("like_count"))
        comments = (
            safe_int(insights.get("comments")) or safe_int(item.get("comments_count"))
        )
        shares = safe_int(insights.get("shares"))
        saves = safe_int(insights.get("saved"))

        rows.append({
            "platform": "Instagram",
            "published_at": item.get("timestamp"),
            "media_type": item.get("media_product_type") or item.get("media_type"),
            "title_or_caption": caption,
            "url": item.get("permalink"),
            "views": views,
            "likes": likes,
            "comments": comments,
            "shares": shares,
            "saves": saves,
            "estimated_minutes_watched": 0,
            "average_view_duration_seconds": 0,
            "subscribers_gained": 0,
            "engagement_rate_percent": engagement_rate(
                views, likes, comments, shares, saves
            ),
        })

    return rows


# ============================================================
# YouTube Auth
# ============================================================

def get_youtube_credentials():
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
            flow = InstalledAppFlow.from_client_secrets_file(
                CLIENT_SECRET_FILE, SCOPES
            )
            credentials = flow.run_local_server(port=0)

        with open(TOKEN_FILE, "w", encoding="utf-8") as token:
            token.write(credentials.to_json())

    return credentials


# ============================================================
# YouTube Data API
# ============================================================

def get_youtube_uploads_playlist_id(youtube_data):
    response = youtube_data.channels().list(
        part="contentDetails",
        id=YOUTUBE_CHANNEL_ID,
    ).execute()
    items = response.get("items", [])
    if not items:
        raise ValueError(
            "No YouTube channel found. Check YOUTUBE_CHANNEL_ID in .env."
        )
    return items[0]["contentDetails"]["relatedPlaylists"]["uploads"]


def get_youtube_recent_video_ids(youtube_data, max_results=50):
    playlist_id = get_youtube_uploads_playlist_id(youtube_data)
    video_ids = []
    next_page_token = None

    while len(video_ids) < max_results:
        response = youtube_data.playlistItems().list(
            part="snippet",
            playlistId=playlist_id,
            maxResults=min(50, max_results - len(video_ids)),
            pageToken=next_page_token,
        ).execute()

        for item in response.get("items", []):
            video_id = item["snippet"]["resourceId"]["videoId"]
            video_ids.append(video_id)

        next_page_token = response.get("nextPageToken")
        if not next_page_token:
            break

    return video_ids


def get_youtube_video_metadata(youtube_data, video_ids):
    metadata = {}
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i: i + 50]
        response = youtube_data.videos().list(
            part="snippet,statistics",
            id=",".join(batch),
        ).execute()

        for item in response.get("items", []):
            video_id = item.get("id")
            snippet = item.get("snippet", {})
            stats = item.get("statistics", {})
            metadata[video_id] = {
                "platform": "YouTube",
                "published_at": snippet.get("publishedAt"),
                "media_type": "VIDEO",
                "title_or_caption": snippet.get("title", ""),
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "views": safe_int(stats.get("viewCount")),
                "likes": safe_int(stats.get("likeCount")),
                "comments": safe_int(stats.get("commentCount")),
                "shares": 0,
                "saves": 0,
                "estimated_minutes_watched": 0,
                "average_view_duration_seconds": 0,
                "subscribers_gained": 0,
                "engagement_rate_percent": 0.0,
            }
    return metadata


# ============================================================
# YouTube Analytics API
# ============================================================

def get_youtube_analytics_by_video(
    youtube_analytics,
    start_date=START_DATE,
    end_date=None,
):
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

    headers = [h["name"] for h in response.get("columnHeaders", [])]
    rows = response.get("rows", [])
    analytics = {}

    for row in rows:
        record = dict(zip(headers, row))
        video_id = record.get("video")
        if not video_id:
            continue
        analytics[video_id] = {
            "views": safe_int(record.get("views")),
            "estimated_minutes_watched": safe_int(
                record.get("estimatedMinutesWatched")
            ),
            "average_view_duration_seconds": safe_int(
                record.get("averageViewDuration")
            ),
            "likes": safe_int(record.get("likes")),
            "comments": safe_int(record.get("comments")),
            "shares": safe_int(record.get("shares")),
            "subscribers_gained": safe_int(record.get("subscribersGained")),
        }

    return analytics


def fetch_youtube_rows(max_results=50):
    if not YOUTUBE_API_KEY or not YOUTUBE_CHANNEL_ID:
        print("Skipping YouTube: missing YOUTUBE_API_KEY or YOUTUBE_CHANNEL_ID.")
        return []

    credentials = get_youtube_credentials()
    youtube_data = build("youtube", "v3", developerKey=YOUTUBE_API_KEY)
    youtube_analytics = build("youtubeAnalytics", "v2", credentials=credentials)

    video_ids = get_youtube_recent_video_ids(youtube_data, max_results=max_results)
    metadata = get_youtube_video_metadata(youtube_data, video_ids)
    analytics = get_youtube_analytics_by_video(youtube_analytics, start_date=START_DATE)

    rows = []
    for video_id in video_ids:
        base = metadata.get(video_id, {})
        private_stats = analytics.get(video_id, {})

        views = private_stats.get("views", base.get("views", 0))
        likes = private_stats.get("likes", base.get("likes", 0))
        comments = private_stats.get("comments", base.get("comments", 0))
        shares = private_stats.get("shares", 0)

        row = {
            **base,
            "views": views,
            "likes": likes,
            "comments": comments,
            "shares": shares,
            "saves": 0,
            "estimated_minutes_watched": private_stats.get(
                "estimated_minutes_watched", 0
            ),
            "average_view_duration_seconds": private_stats.get(
                "average_view_duration_seconds", 0
            ),
            "subscribers_gained": private_stats.get("subscribers_gained", 0),
            "engagement_rate_percent": engagement_rate(views, likes, comments, shares, 0),
        }
        rows.append(row)

    return rows


# ============================================================
# HTML Dashboard
# ============================================================

def make_table_rows(df, enriched=False):
    rows = ""
    for _, row in df.iterrows():
        book_td = (
            f'<td class="col-book">{safe_text(row.get("book_or_offer", "—"))}</td>'
            if enriched
            else ""
        )
        pillar_td = (
            f'<td class="col-pillar">{safe_text(row.get("content_pillar", "—"))}</td>'
            if enriched
            else ""
        )

        rows += f"""
        <tr>
            <td>{safe_text(row.get("platform"))}</td>
            {book_td}
            {pillar_td}
            <td>{safe_text(row.get("published_at"))}</td>
            <td>{safe_text(row.get("media_type"))}</td>
            <td>{safe_text(row.get("title_or_caption"))}</td>
            <td>{safe_int(row.get("views")):,}</td>
            <td>{safe_int(row.get("estimated_minutes_watched")):,}</td>
            <td>{safe_int(row.get("average_view_duration_seconds")):,}</td>
            <td>{safe_int(row.get("likes")):,}</td>
            <td>{safe_int(row.get("comments")):,}</td>
            <td>{safe_int(row.get("shares")):,}</td>
            <td>{safe_int(row.get("saves")):,}</td>
            <td>{safe_int(row.get("subscribers_gained")):,}</td>
            <td>{row.get("engagement_rate_percent", 0)}%</td>
            <td>{safe_text(row.get("content_signal"))}</td>
            <td>{safe_text(row.get("recommended_action"))}</td>
            <td><a href="{safe_text(row.get("url"))}" target="_blank">Open</a></td>
        </tr>
        """
    return rows


def make_table_header(enriched=False):
    book_th = '<th>Book</th>' if enriched else ""
    pillar_th = '<th>Pillar</th>' if enriched else ""
    return f"""
    <tr>
        <th>Platform</th>
        {book_th}
        {pillar_th}
        <th>Published</th>
        <th>Type</th>
        <th>Title / Caption</th>
        <th>Views</th>
        <th>Watch Min</th>
        <th>Avg Sec</th>
        <th>Likes</th>
        <th>Comments</th>
        <th>Shares</th>
        <th>Saves</th>
        <th>Subs +</th>
        <th>Eng %</th>
        <th>Signal</th>
        <th>Recommended Action</th>
        <th>Link</th>
    </tr>"""


def build_html(df, monday_plan_html, scoreboard_html, x_performance_html="",
               facebook_performance_html="", kdp_revenue_html="", filtered_repurpose_count=None):
    html_file = OUTPUT_DIR / "index.html"
    enriched = "book_or_offer" in df.columns and df["book_or_offer"].ne("—").any()

    total_posts = len(df)
    total_views = safe_int(df["views"].sum())
    total_watch_minutes = safe_int(df["estimated_minutes_watched"].sum())
    avg_engagement = (
        round(df["engagement_rate_percent"].mean(), 2) if total_posts else 0
    )

    youtube_rows = df[df["platform"] == "YouTube"]
    instagram_rows = df[df["platform"] == "Instagram"]
    facebook_rows = df[df["platform"] == "Facebook"]
    # KDP total for card — fetch separately
    _, _, kdp_total_revenue_raw = get_kdp_revenue_data()
    kdp_total_revenue = kdp_total_revenue_raw or 0.0
    youtube_views = safe_int(youtube_rows["views"].sum()) if not youtube_rows.empty else 0
    instagram_views = safe_int(instagram_rows["views"].sum()) if not instagram_rows.empty else 0
    facebook_views = safe_int(facebook_rows["views"].sum()) if not facebook_rows.empty else 0

    winners_count = df["content_signal"].str.contains("Winner", na=False).sum()
    sticky_count = df["content_signal"].str.contains("Sticky", na=False).sum()
    promising_count = df["content_signal"].str.contains("Promising", na=False).sum()
    repurpose_count = (
        filtered_repurpose_count
        if filtered_repurpose_count is not None
        else df["content_signal"].str.contains("Repurpose Candidate", na=False).sum()
    )

    top_views = df.sort_values("views", ascending=False).head(15)
    top_watch_time = youtube_rows.sort_values(
        "estimated_minutes_watched", ascending=False
    ).head(15)
    top_engagement = (
        df[df["views"] >= MIN_ENGAGEMENT_VIEWS]
        .sort_values("engagement_rate_percent", ascending=False)
        .head(15)
    )
    content_intelligence = (
        df[df["content_signal"].ne("Watch")]
        .sort_values(["views", "engagement_rate_percent"], ascending=False)
        .head(20)
    )

    th = make_table_header(enriched)

    html = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Will Power Protocols Social Analytics</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body {{
            margin: 0;
            font-family: Arial, Helvetica, sans-serif;
            background: #0A1628;
            color: #FFFFFF;
        }}
        .container {{
            max-width: 1400px;
            margin: auto;
            padding: 28px 18px 60px;
        }}
        h1 {{ margin-bottom: 4px; }}
        .subtitle {{
            color: #AAB4C0;
            margin-bottom: 24px;
        }}
        .cards {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
            gap: 16px;
            margin-bottom: 30px;
        }}
        .card {{
            background: #101F36;
            border-radius: 18px;
            padding: 18px;
            box-shadow: 0 10px 28px rgba(0,0,0,.25);
            border: 1px solid rgba(255,255,255,.08);
        }}
        .label {{
            color: #AAB4C0;
            font-size: 13px;
            text-transform: uppercase;
            letter-spacing: .08em;
        }}
        .value {{
            color: #C9A84C;
            font-size: 32px;
            font-weight: bold;
            margin-top: 8px;
        }}
        h2 {{
            color: #C9A84C;
            margin-top: 34px;
        }}
        .table-wrap {{
            overflow-x: auto;
            border-radius: 14px;
            margin-bottom: 34px;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            background: #101F36;
            min-width: 1200px;
        }}
        th, td {{
            padding: 10px 8px;
            border-bottom: 1px solid rgba(255,255,255,.08);
            text-align: left;
            vertical-align: top;
            font-size: 13px;
        }}
        th {{
            color: #C9A84C;
            background: rgba(201,168,76,.08);
            text-transform: uppercase;
            font-size: 11px;
            letter-spacing: .06em;
        }}
        .col-book, .col-pillar {{
            color: #C9A84C;
            font-size: 12px;
            white-space: nowrap;
        }}
        a {{
            color: #FFFFFF;
            text-decoration-color: #C9A84C;
        }}
        .note {{
            color: #AAB4C0;
            font-size: 13px;
            margin-top: 24px;
            line-height: 1.5;
        }}
        .signal-help {{
            background: #101F36;
            border: 1px solid rgba(255,255,255,.08);
            border-radius: 18px;
            padding: 18px;
            margin-bottom: 30px;
            color: #D7DEE8;
            line-height: 1.5;
        }}
        .signal-help strong {{ color: #C9A84C; }}
        .action-plan {{
            background: linear-gradient(135deg, #101F36, #0d1b2e);
            border: 1.5px solid #C9A84C;
            border-radius: 18px;
            padding: 24px 28px;
            margin-bottom: 30px;
        }}
        .action-plan h2 {{ margin-top: 0; color: #C9A84C; }}
        .action-section {{ margin-bottom: 22px; }}
        .action-section h3 {{
            color: #FFFFFF;
            font-size: 13px;
            text-transform: uppercase;
            letter-spacing: .08em;
            margin: 0 0 10px 0;
            border-bottom: 1px solid rgba(201,168,76,.25);
            padding-bottom: 6px;
        }}
        .action-note {{
            color: #AAB4C0;
            font-size: 12px;
            margin: 6px 0 0 0;
        }}
        .schedule-table {{
            width: 100%;
            border-collapse: collapse;
            background: transparent;
            min-width: unset;
        }}
        .schedule-table th {{
            background: transparent;
            color: #C9A84C;
            font-size: 11px;
            text-transform: uppercase;
            padding: 5px 8px;
            letter-spacing: .06em;
        }}
        .schedule-table td {{
            padding: 8px;
            border-bottom: 1px solid rgba(255,255,255,.06);
            font-size: 13px;
            color: #D7DEE8;
        }}
        .schedule-table tr:last-child td {{ border-bottom: none; }}
        .winner-note {{
            color: #C9A84C;
            font-size: 11px;
            font-weight: bold;
        }}
        .repurpose-item {{
            display: flex;
            align-items: flex-start;
            gap: 12px;
            padding: 8px 0;
            border-bottom: 1px solid rgba(255,255,255,.06);
            font-size: 13px;
        }}
        .repurpose-item:last-child {{ border-bottom: none; }}
        .platform-badge {{
            background: rgba(201,168,76,.15);
            color: #C9A84C;
            border-radius: 6px;
            padding: 2px 8px;
            font-size: 11px;
            font-weight: bold;
            white-space: nowrap;
        }}
        .scoreboard-grid {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 20px;
            margin-bottom: 30px;
        }}
        .scoreboard-card {{
            background: #101F36;
            border-radius: 14px;
            padding: 18px;
            border: 1px solid rgba(255,255,255,.08);
            overflow-x: auto;
        }}
        .scoreboard-card h2 {{
            margin-top: 0;
            font-size: 16px;
            color: #C9A84C;
        }}
        .scoreboard-table {{
            width: 100%;
            border-collapse: collapse;
            background: transparent;
            min-width: unset;
        }}
        .scoreboard-table th {{
            background: transparent;
            color: #C9A84C;
            font-size: 11px;
            text-transform: uppercase;
            padding: 5px 8px;
        }}
        .scoreboard-table td {{
            padding: 7px 8px;
            border-bottom: 1px solid rgba(255,255,255,.06);
            font-size: 13px;
            color: #D7DEE8;
        }}
        .scoreboard-table tr:last-child td {{ border-bottom: none; }}
        @media (max-width: 800px) {{
            th, td {{ font-size: 11px; padding: 7px 5px; }}
            .value {{ font-size: 26px; }}
            .scoreboard-grid {{ grid-template-columns: 1fr; }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>Will Power Protocols Social Analytics</h1>
        <div class="subtitle">Instagram · YouTube · Facebook · X · Book &amp; Pillar Tracking · Monday Action Plan</div>

        <div class="cards">
            <div class="card">
                <div class="label">Posts Analyzed</div>
                <div class="value">{total_posts}</div>
            </div>
            <div class="card">
                <div class="label">Total Views</div>
                <div class="value">{total_views:,}</div>
            </div>
            <div class="card">
                <div class="label">Instagram Views</div>
                <div class="value">{instagram_views:,}</div>
            </div>
            <div class="card">
                <div class="label">YouTube Views</div>
                <div class="value">{youtube_views:,}</div>
            </div>
            <div class="card">
                <div class="label">Facebook Views</div>
                <div class="value">{facebook_views:,}</div>
            </div>
            <div class="card">
                <div class="label">YouTube Watch Minutes</div>
                <div class="value">{total_watch_minutes:,}</div>
            </div>
            <div class="card">
                <div class="label">Average Engagement</div>
                <div class="value">{avg_engagement}%</div>
            </div>
            <div class="card">
                <div class="label">Winners</div>
                <div class="value">{winners_count}</div>
            </div>
            <div class="card">
                <div class="label">Promising</div>
                <div class="value">{promising_count}</div>
            </div>
            <div class="card">
                <div class="label">Repurpose Signals</div>
                <div class="value">{repurpose_count}</div>
            </div>
            <div class="card">
                <div class="label">Total KDP Revenue</div>
                <div class="value">${kdp_total_revenue:.2f}</div>
            </div>
        </div>

        <div class="signal-help">
            <strong>Winner</strong> = top-performing content by your own view distribution.
            <strong>Sticky</strong> = YouTube with stronger-than-usual average view duration.
            <strong>Promising</strong> = engagement high relative to your baseline.
            <strong>Repurpose Candidate</strong> = worth turning into a Short, Reel, or follow-up.
        </div>

        {monday_plan_html}

        {scoreboard_html}

        {x_performance_html}

        {facebook_performance_html}

        {kdp_revenue_html}

        <h2>Content Intelligence</h2>
        <div class="subtitle">Posts with a useful signal and suggested next action.</div>
        <div class="table-wrap">
            <table>
                <thead>{th}</thead>
                <tbody>{make_table_rows(content_intelligence, enriched)}</tbody>
            </table>
        </div>

        <h2>Top Content by Views</h2>
        <div class="table-wrap">
            <table>
                <thead>{th}</thead>
                <tbody>{make_table_rows(top_views, enriched)}</tbody>
            </table>
        </div>

        <h2>Top YouTube Content by Watch Time</h2>
        <div class="table-wrap">
            <table>
                <thead>{th}</thead>
                <tbody>{make_table_rows(top_watch_time, enriched)}</tbody>
            </table>
        </div>

        <h2>Top Content by Engagement Rate</h2>
        <div class="subtitle">Filtered to posts with at least {MIN_ENGAGEMENT_VIEWS} views.</div>
        <div class="table-wrap">
            <table>
                <thead>{th}</thead>
                <tbody>{make_table_rows(top_engagement, enriched)}</tbody>
            </table>
        </div>

        <div class="note">
            Watch minutes, average view duration, and subscribers gained are YouTube-only metrics.<br>
            Facebook metrics: video views (3-second), reactions, reach, clicks.<br>
            Never commit .env, client_secret.json, or youtube_token.json to GitHub.
        </div>
    </div>
</body>
</html>"""

    html_file.write_text(html, encoding="utf-8")
    print(f"Saved {html_file}")



# ============================================================
# KDP Revenue
# ============================================================

def get_kdp_revenue_data():
    """Read kdp_snapshots from wpp.db and return summary data."""
    import sqlite3

    db_path = None
    for candidate in [
        Path(WPP_DB_FILE),
        Path(__file__).parent / WPP_DB_FILE,
        Path.cwd() / WPP_DB_FILE,
    ]:
        if candidate.exists():
            db_path = candidate
            break

    if db_path is None:
        return None, None, None

    try:
        conn = sqlite3.connect(db_path)

        # All-time totals by book
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

        # Monthly trend
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

        # Grand total
        total = pd.read_sql_query("""
            SELECT ROUND(SUM(royalties_usd), 2) AS total
            FROM kdp_snapshots
        """, conn)

        conn.close()
        grand_total = float(total.iloc[0]['total']) if not total.empty else 0.0
        return by_book, by_month, grand_total

    except Exception as e:
        print(f"KDP revenue query error: {e}")
        return None, None, None


def build_kdp_revenue_html():
    """Build KDP book sales section for dashboard."""
    by_book, by_month, grand_total = get_kdp_revenue_data()

    if by_book is None or by_book.empty:
        return ""

    # Book revenue rows
    book_rows = ""
    for _, r in by_book.iterrows():
        revenue = float(r.get("total_revenue", 0))
        kindle = int(r.get("kindle_units", 0))
        pb = int(r.get("paperback_units", 0))
        ku = int(r.get("ku_pages", 0))
        book_rows += f"""
        <tr>
            <td>{r["title"]}</td>
            <td style="text-align:center">{kindle}</td>
            <td style="text-align:center">{pb}</td>
            <td style="text-align:center">{ku:,}</td>
            <td style="text-align:center"><strong style="color:#C9A84C">${revenue:.2f}</strong></td>
        </tr>"""

    # Monthly trend rows (last 6 months)
    month_rows = ""
    for _, r in by_month.head(6).iterrows():
        rev = float(r.get("monthly_revenue", 0))
        kindle = int(r.get("kindle_units", 0))
        ku = int(r.get("ku_pages", 0))
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


# ============================================================
# Main
# ============================================================

def main():
    # Load DB content
    content_map, queue_df = load_wpp_content()

    # Fetch all platforms
    rows = []
    print("Fetching Instagram...")
    rows.extend(fetch_instagram_rows(limit=MAX_INSTAGRAM_POSTS))

    print("Fetching YouTube...")
    rows.extend(fetch_youtube_rows(max_results=MAX_YOUTUBE_VIDEOS))

    print("Fetching Facebook...")
    fb_rows = fetch_facebook_rows(limit=MAX_FACEBOOK_POSTS)
    rows.extend(fb_rows)

    if not rows:
        raise RuntimeError("No rows returned from any platform.")

    df = pd.DataFrame(rows)

    df["published_at"] = pd.to_datetime(
        df["published_at"], errors="coerce", utc=True
    ).dt.strftime("%Y-%m-%d")

    df = df.sort_values("views", ascending=False)

    # Enrich with content map
    df = merge_content_map(df, content_map)

    # Add content intelligence signals
    df = add_content_intelligence(df)

    # Save CSV
    csv_file = OUTPUT_DIR / "social_analytics.csv"
    df.to_csv(csv_file, index=False)
    print(f"Saved {csv_file}")

    # Build sections
    plan = generate_monday_plan(df, queue_df)
    monday_plan_html = build_monday_plan_html(plan)
    scoreboard_html = build_scoreboard_html(df)
    x_performance_html = build_x_performance_html(content_map)
    facebook_performance_html = build_facebook_performance_html(df)

    # Build KDP Revenue
    kdp_revenue_html = build_kdp_revenue_html()

    # Build HTML dashboard
    build_html(
        df,
        monday_plan_html,
        scoreboard_html,
        x_performance_html,
        facebook_performance_html,
        kdp_revenue_html,
        filtered_repurpose_count=len(plan["repurpose"]),
    )


if __name__ == "__main__":
    main()
