import os
from pathlib import Path
from datetime import date
import json

import pandas as pd
import requests
from dotenv import load_dotenv
load_dotenv()
from wpp_x_importer import build_x_performance_html
from wpp_facebook import fetch_facebook_rows, build_facebook_performance_html
from wpp_instagram import fetch_instagram_rows
from wpp_instagram_wb import fetch_instagram_wb_rows
from wpp_youtube import fetch_youtube_rows
from wpp_kdp import build_kdp_revenue_html, get_kdp_revenue_data
from wpp_fb_image_analytics import fetch_fb_image_rows, build_fb_image_performance_html
# ============================================================
# Configuration
# ============================================================






OUTPUT_DIR = Path("docs")
OUTPUT_DIR.mkdir(exist_ok=True)

START_DATE = "2026-01-01"
MAX_FACEBOOK_POSTS = 50
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

    # Facebook: normalize Reel, video, and post permalink formats
    fb_reel_match = re.search(r'facebook\.com/reel/(\d+)', url, re.IGNORECASE)
    if fb_reel_match:
        return f"https://www.facebook.com/reel/{fb_reel_match.group(1)}"

    fb_video_match = re.search(r'facebook\.com/(?:[^/]+/)?videos?/(\d+)', url, re.IGNORECASE)
    if fb_video_match:
        return f"https://www.facebook.com/reel/{fb_video_match.group(1)}"

    fb_post_match = re.search(r'facebook\.com/(?:permalink\.php\?story_fbid=|permalink/php\?story_fbid=|[^/]+/posts?/)(\d+)', url, re.IGNORECASE)
    if fb_post_match:
        return f"https://www.facebook.com/permalink/{fb_post_match.group(1)}"

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
    print(f"  -> Enrichment URLs: {len(content_map)}")

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
    print(f"  -> Short queue: {len(queue_df)} ({wpp_count} WPP, {wb_count} Will Byron)")
    if len(ep_queue):
        print(f"  -> Episode queue: {len(ep_queue)} unposted video/podcast episodes")

    return content_map, queue_df


def merge_content_map(df, content_map):
    """
    Left-join main analytics df with content_map on normalized URL.

    Important fix:
    Some platform rows, especially Facebook, already arrive with placeholder
    book_or_offer/content_pillar values of "—". Pandas therefore creates
    *_x and *_y columns during merge. We must coalesce the merged *_y values
    back into the display columns, otherwise the dashboard shows blank/— even
    when the content map matched the URL.
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

    def _clean_series(series):
        return (
            series.astype("object")
            .where(series.notna(), None)
            .replace("", None)
            .replace("nan", None)
        )

    for col in enrich_cols:
        merged_col = f"{col}_y"
        original_col = f"{col}_x"

        if merged_col in df.columns:
            merged = _clean_series(df[merged_col])
            if original_col in df.columns:
                original = _clean_series(df[original_col])
                df[col] = merged.where(merged.notna(), original)
            elif col in df.columns:
                original = _clean_series(df[col])
                df[col] = merged.where(merged.notna(), original)
            else:
                df[col] = merged
        elif original_col in df.columns:
            df[col] = _clean_series(df[original_col])
        elif col in df.columns:
            df[col] = _clean_series(df[col])
        else:
            df[col] = None

        df[col] = df[col].fillna("—")

    # Remove merge helper columns so downstream HTML/CSV are cleaner.
    drop_cols = [
        c for c in df.columns
        if any(c == f"{col}_x" or c == f"{col}_y" for col in enrich_cols)
    ]
    if drop_cols:
        df = df.drop(columns=drop_cols)

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
        ~already_done.astype("boolean").fillna(False).astype(bool)
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




# ============================================================
# CEO Tab Builder
# ============================================================

def build_ceo_tab_html(df, plan, kdp_total_revenue, monday_plan_html):
    """CEO overview tab — five-stat hero, platform health, top posts, what's working."""
    total_views = safe_int(df["views"].sum())
    total_posts = len(df)
    avg_eng     = round(df["engagement_rate_percent"].mean(), 2) if total_posts else 0
    queue_count = plan["unposted_count"] if plan else 0
    yt_minutes  = safe_int(
        df[df["platform"] == "YouTube"]["estimated_minutes_watched"].sum()
    )

    # ── Hero stats ────────────────────────────────────────────────
    hero_html = f"""
    <div class="ceo-hero">
        <div class="ceo-stat">
            <div class="ceo-stat-label">KDP Revenue</div>
            <div class="ceo-stat-value">${kdp_total_revenue:.2f}</div>
            <div class="ceo-stat-sub">all time</div>
        </div>
        <div class="ceo-stat">
            <div class="ceo-stat-label">Total Views</div>
            <div class="ceo-stat-value">{total_views:,}</div>
            <div class="ceo-stat-sub">across all platforms</div>
        </div>
        <div class="ceo-stat">
            <div class="ceo-stat-label">YouTube Watch Time</div>
            <div class="ceo-stat-value">{yt_minutes:,}</div>
            <div class="ceo-stat-sub">minutes watched</div>
        </div>
        <div class="ceo-stat">
            <div class="ceo-stat-label">Content Queue</div>
            <div class="ceo-stat-value">{queue_count}</div>
            <div class="ceo-stat-sub">shorts ready to post</div>
        </div>
        <div class="ceo-stat">
            <div class="ceo-stat-label">Avg Engagement</div>
            <div class="ceo-stat-value">{avg_eng}%</div>
            <div class="ceo-stat-sub">across all posts</div>
        </div>
    </div>"""

    # ── What's Working ────────────────────────────────────────────
    bullets = []

    if plan and plan.get("has_content_map"):
        known = df[df["book_or_offer"].ne("—")]
        if not known.empty:
            book_agg = (
                known.groupby("book_or_offer")
                .agg(
                    views=("views", "sum"),
                    eng=("engagement_rate_percent", "mean"),
                    winners=("content_signal",
                             lambda x: x.str.contains("Winner", na=False).sum()),
                )
                .reset_index()
            )
            top_vol = book_agg.sort_values("views", ascending=False).iloc[0]
            top_eng = book_agg.sort_values("eng",   ascending=False).iloc[0]
            bullets.append(
                f'<strong style="color:#C9A84C">{top_vol["book_or_offer"]}</strong>'
                f' leads by volume — {safe_int(top_vol["views"]):,} views,'
                f' {safe_int(top_vol["winners"])} winner posts'
            )
            if top_eng["book_or_offer"] != top_vol["book_or_offer"]:
                bullets.append(
                    f'<strong style="color:#C9A84C">{top_eng["book_or_offer"]}</strong>'
                    f' leads by engagement — {round(top_eng["eng"], 1)}% average'
                )

    if plan and plan.get("pillar_winner") and plan["pillar_winner"] != "—":
        known_p = df[df["content_pillar"].ne("—")]
        if not known_p.empty:
            p_agg = (
                known_p.groupby("content_pillar")
                .agg(
                    posts=("views", "count"),
                    views=("views", "sum"),
                    eng=("engagement_rate_percent", "mean"),
                )
                .reset_index()
            )
            top_vol_p = p_agg.sort_values("views", ascending=False).iloc[0]
            bullets.append(
                f'Top pillar by views: <strong style="color:#C9A84C">'
                f'{top_vol_p["content_pillar"]}</strong>'
                f' ({safe_int(top_vol_p["views"]):,} views)'
            )
            gems = p_agg[
                (p_agg["posts"] <= 5) & (p_agg["eng"] >= 8.0)
            ].sort_values("eng", ascending=False)
            if not gems.empty:
                g = gems.iloc[0]
                bullets.append(
                    f'<strong style="color:#C9A84C">{g["content_pillar"]}</strong>'
                    f' has {round(g["eng"], 1)}% engagement on {safe_int(g["posts"])} posts'
                    f' — high signal, low volume, worth scaling'
                )

    if plan and plan.get("repurpose"):
        rep_links = " &nbsp;&middot;&nbsp; ".join(
            f'<a href="{r["url"]}" target="_blank" style="color:#C9A84C">'
            f'{r["platform"]}: {r["title"][:50]}...</a>'
            for r in plan["repurpose"]
        )
        n = len(plan["repurpose"])
        bullets.append(
            f'{n} repurpose signal{"s" if n > 1 else ""} ready to cross-post: {rep_links}'
        )

    bullet_li = "".join(
        f'<li style="margin-bottom:10px">{b}</li>' for b in bullets
    ) if bullets else (
        '<li>No matched content yet — add URLs to wpp.db to unlock insights.</li>'
    )

    what_working = f"""
    <div class="scoreboard-card" style="margin-bottom:24px">
        <h2 style="margin-top:0;font-size:16px">What's Working</h2>
        <ul style="margin:0;padding-left:20px;line-height:1.6;font-size:13px;color:#D7DEE8">
            {bullet_li}
        </ul>
    </div>"""

    # ── Platform Health ───────────────────────────────────────────
    PLATFORM_DISPLAY = [
        ("Facebook",                     "Facebook Reels (WPP)"),
        ("Instagram",                    "Instagram @willpowerprotocols"),
        ("Instagram-WB",                 "Instagram @will.byron88"),
        ("YouTube",                      "YouTube"),
        ("FB-Image-PrehistoricMemories", "Prehistoric Memories"),
        ("FB-Image-TheProtocolLab",      "The Protocol Lab"),
    ]
    plat_rows = ""
    for plat_key, plat_label in PLATFORM_DISPLAY:
        p = df[df["platform"] == plat_key]
        if p.empty:
            plat_rows += f"""
            <tr>
                <td>{plat_label}</td>
                <td style="text-align:center">—</td>
                <td style="text-align:center">—</td>
                <td style="text-align:center">—</td>
                <td><span style="color:#FF6B6B;font-size:12px">No data</span></td>
            </tr>"""
        else:
            views   = safe_int(p["views"].sum())
            posts   = len(p)
            avg_e   = round(p["engagement_rate_percent"].mean(), 2)
            eng_str = f"{avg_e}%" if avg_e > 0 else "—"
            extra   = ""
            if plat_key == "YouTube":
                mins  = safe_int(p["estimated_minutes_watched"].sum())
                extra = (
                    f'<div style="font-size:11px;color:#AAB4C0;margin-top:2px">'
                    f'{mins:,} watch min</div>'
                )
            plat_rows += f"""
            <tr>
                <td><strong>{plat_label}</strong>{extra}</td>
                <td style="text-align:center">{posts}</td>
                <td style="text-align:center">{views:,}</td>
                <td style="text-align:center">{eng_str}</td>
                <td><span style="color:#5CFF7E;font-size:12px">Active</span></td>
            </tr>"""

    platform_health = f"""
    <div class="scoreboard-card" style="margin-bottom:30px">
        <h2 style="margin-top:0;font-size:16px">Platform Health</h2>
        <table class="scoreboard-table">
            <thead>
                <tr>
                    <th>Platform</th>
                    <th style="text-align:center">Posts</th>
                    <th style="text-align:center">Views / Plays</th>
                    <th style="text-align:center">Avg Eng</th>
                    <th>Status</th>
                </tr>
            </thead>
            <tbody>{plat_rows}</tbody>
        </table>
    </div>"""

    # ── Top 5 Posts ────────────────────────────────────────────────
    top5 = df.sort_values("views", ascending=False).head(5)
    top5_cards = ""
    for _, row in top5.iterrows():
        plat   = safe_text(row.get("platform"))
        title  = truncate_text(safe_text(row.get("title_or_caption")), 90)
        views  = safe_int(row.get("views"))
        eng    = row.get("engagement_rate_percent", 0)
        signal = safe_text(row.get("content_signal"))
        url    = safe_text(row.get("url"))
        pub    = safe_text(row.get("published_at"))
        book   = safe_text(row.get("book_or_offer", "—"))
        sig_badge = (
            f'&nbsp;&middot;&nbsp; <strong style="color:#C9A84C">{signal}</strong>'
            if signal and signal != "Watch" else ""
        )
        book_line = (
            f'<div style="font-size:11px;color:#C9A84C;margin-top:3px">{book}</div>'
            if book and book != "—" else ""
        )
        top5_cards += f"""
        <div class="top5-card">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:7px">
                <span class="platform-badge">{plat}</span>
                <span style="color:#AAB4C0;font-size:11px">{pub}</span>
            </div>
            <div style="font-size:13px;line-height:1.4;margin-bottom:6px">{title}</div>
            {book_line}
            <div style="font-size:12px;color:#AAB4C0;margin-top:6px">
                <strong style="color:#C9A84C">{views:,}</strong> views
                &nbsp;&middot;&nbsp; <strong style="color:#C9A84C">{eng}%</strong> eng
                {sig_badge}
                &nbsp;&middot;&nbsp; <a href="{url}" target="_blank" style="color:#C9A84C">Open</a>
            </div>
        </div>"""

    top5_html = f"""
    <h2 style="color:#C9A84C;margin-top:0">Top 5 Posts</h2>
    <div class="top5-grid" style="margin-bottom:30px">{top5_cards}</div>"""

    return f"""
    {hero_html}
    {monday_plan_html}
    {what_working}
    {platform_health}
    {top5_html}"""


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
               facebook_performance_html="", fb_image_performance_html="",
               kdp_revenue_html="", filtered_repurpose_count=None, plan=None):
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
    facebook_reel_plays = (
        safe_int(facebook_rows.get("facebook_reel_plays", pd.Series([0])).sum())
        if not facebook_rows.empty else 0
    )
    facebook_3s_views = (
        safe_int(facebook_rows.get("facebook_3s_views", pd.Series([0])).sum())
        if not facebook_rows.empty else 0
    )
    facebook_15s_views = (
        safe_int(facebook_rows.get("facebook_15s_views", pd.Series([0])).sum())
        if not facebook_rows.empty else 0
    )
    facebook_reach = (
        safe_int(facebook_rows.get("reach", pd.Series([0])).sum())
        if not facebook_rows.empty else 0
    )

    ceo_tab_html = build_ceo_tab_html(df, plan, kdp_total_revenue, monday_plan_html)

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
        /* Tab switcher */
        .tab-bar {{
            display: flex;
            gap: 0;
            margin-bottom: 28px;
            border-bottom: 2px solid rgba(201,168,76,.25);
        }}
        .tab-btn {{
            background: none;
            border: none;
            color: #AAB4C0;
            font-size: 15px;
            font-weight: bold;
            padding: 12px 32px;
            cursor: pointer;
            border-bottom: 3px solid transparent;
            margin-bottom: -2px;
            font-family: Arial, Helvetica, sans-serif;
            transition: color .15s;
        }}
        .tab-btn:hover {{ color: #D7DEE8; }}
        .tab-btn.active {{
            color: #C9A84C;
            border-bottom-color: #C9A84C;
        }}
        .tab-pane {{ display: none; }}
        .tab-pane.active {{ display: block; }}
        /* CEO view */
        .ceo-hero {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 16px;
            margin-bottom: 28px;
        }}
        .ceo-stat {{
            background: #101F36;
            border-radius: 18px;
            padding: 20px 18px;
            border: 1px solid rgba(255,255,255,.08);
        }}
        .ceo-stat-label {{
            color: #AAB4C0;
            font-size: 12px;
            text-transform: uppercase;
            letter-spacing: .08em;
        }}
        .ceo-stat-value {{
            color: #C9A84C;
            font-size: 36px;
            font-weight: bold;
            margin: 6px 0 2px;
            line-height: 1;
        }}
        .ceo-stat-sub {{
            color: #AAB4C0;
            font-size: 12px;
        }}
        .top5-grid {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 16px;
        }}
        .top5-card {{
            background: #101F36;
            border-radius: 14px;
            padding: 16px;
            border: 1px solid rgba(255,255,255,.08);
        }}
        @media (max-width: 800px) {{
            th, td {{ font-size: 11px; padding: 7px 5px; }}
            .value {{ font-size: 26px; }}
            .scoreboard-grid {{ grid-template-columns: 1fr; }}
            .ceo-stat-value {{ font-size: 28px; }}
            .top5-grid {{ grid-template-columns: 1fr; }}
        }}
    </style>
    <script>
        function showTab(id) {{
            document.querySelectorAll('.tab-pane').forEach(function(p) {{ p.classList.remove('active'); }});
            document.querySelectorAll('.tab-btn').forEach(function(b) {{ b.classList.remove('active'); }});
            document.getElementById(id).classList.add('active');
            document.querySelector('[data-tab="' + id + '"]').classList.add('active');
        }}
    </script>
</head>
<body>
    <div class="container">
        <h1>Will Power Protocols Social Analytics</h1>
        <div class="subtitle">Instagram · YouTube · Facebook · X · Book &amp; Pillar Tracking · Monday Action Plan</div>

        <div class="tab-bar">
            <button class="tab-btn active" data-tab="tab-ceo" onclick="showTab('tab-ceo')">CEO View</button>
            <button class="tab-btn" data-tab="tab-analyst" onclick="showTab('tab-analyst')">Full Analytics</button>
        </div>

        <div id="tab-ceo" class="tab-pane active">
            {ceo_tab_html}
        </div>

        <div id="tab-analyst" class="tab-pane">
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
                <div class="label">Facebook Reel Plays</div>
                <div class="value">{facebook_views:,}</div>
            </div>
            <div class="card">
                <div class="label">Facebook Reach</div>
                <div class="value">{facebook_reach:,}</div>
            </div>
            <div class="card">
                <div class="label">Facebook 15s Quality Views</div>
                <div class="value">{facebook_15s_views:,}</div>
            </div>
            <div class="card">
                <div class="label">Facebook 3s API Views</div>
                <div class="value">{facebook_3s_views:,}</div>
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

        {fb_image_performance_html}

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
            Facebook metrics: Reel Plays are the main Facebook views. Reach is unique reached. 15s Quality Views are a deeper-watch signal. 3s API Views are kept as diagnostics because Meta may return 0 for Reels even when Reel Plays are available.<br>
            Never commit .env, client_secret.json, or youtube_token.json to GitHub.
        </div>
        </div><!-- end tab-analyst -->
    </div>
</body>
</html>"""

    html_file.write_text(html, encoding="utf-8")
    print(f"Saved {html_file}")




# ============================================================
# Main
# ============================================================

def main():
    # Load DB content
    content_map, queue_df = load_wpp_content()

    # Fetch all platforms
    rows = []
    print("Fetching Instagram...")
    ig_rows = fetch_instagram_rows(limit=MAX_INSTAGRAM_POSTS)
    print(f"Instagram: {len(ig_rows)} rows built.")
    rows.extend(ig_rows)

    print("Fetching Instagram-WB...")
    ig_wb_rows = fetch_instagram_wb_rows(limit=MAX_INSTAGRAM_POSTS)
    print(f"Instagram-WB: {len(ig_wb_rows)} rows built.")
    rows.extend(ig_wb_rows)

    print("Fetching YouTube...")
    yt_rows = fetch_youtube_rows(max_results=MAX_YOUTUBE_VIDEOS)
    print(f"YouTube: {len(yt_rows)} rows built.")
    rows.extend(yt_rows)

    print("Fetching Facebook...")
    fb_rows = fetch_facebook_rows(limit=MAX_FACEBOOK_POSTS)
    rows.extend(fb_rows)

    print("Fetching FB Image Posts (PM + TPL)...")
    fb_image_rows = fetch_fb_image_rows(limit=MAX_FACEBOOK_POSTS)
    rows.extend(fb_image_rows)




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
    fb_image_performance_html = build_fb_image_performance_html(fb_image_rows)

    # Build KDP Revenue
    kdp_revenue_html = build_kdp_revenue_html()

    # Build HTML dashboard
    build_html(
        df,
        monday_plan_html,
        scoreboard_html,
        x_performance_html,
        facebook_performance_html,
        fb_image_performance_html,
        kdp_revenue_html,
        filtered_repurpose_count=len(plan["repurpose"]),
        plan=plan,
    )


if __name__ == "__main__":
    main()
