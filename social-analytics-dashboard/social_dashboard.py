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
from wpp_instagram import fetch_instagram_rows, build_instagram_performance_html
from wpp_instagram_wb import fetch_instagram_wb_rows
from wpp_youtube import fetch_youtube_rows
from wpp_kdp import build_kdp_revenue_html, get_kdp_revenue_data
from wpp_fb_image_analytics import fetch_fb_image_rows, build_fb_image_performance_html
from wpp_ai_briefing import generate_ai_briefing
from wpp_trends import fetch_trends_data, build_trends_html
from wpp_gumroad import fetch_gumroad_data, load_gumroad_posts, build_gumroad_revenue_html
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

# Human-readable display names for every platform key.
# Used in table cells, filter dropdowns, and cards throughout the dashboard.
PLATFORM_LABELS = {
    "Facebook":                     "Facebook Reels (WPP)",
    "Facebook-WB":                  "Facebook Reels (Will Byron)",
    "Instagram":                    "Instagram (@willpowerprotocols)",
    "Instagram-WB":                 "Instagram (@will.byron88)",
    "YouTube":                      "YouTube (Will Power Protocols)",
    "FB-Image-PrehistoricMemories": "Facebook Images (Prehistoric Memories)",
    "FB-Image-TheProtocolLab":      "Facebook Images (The Protocol Lab)",
    "FB-Image-WillByron":           "Facebook Images (Will Byron)",
    "X":                            "X",
}


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

    # Facebook: photo posts — photo?fbid=, photo/?fbid=, photo.php?fbid=
    fb_photo_match = re.search(r'facebook\.com/photo[./]?(?:php)?\??/?fbid=(\d+)', url, re.IGNORECASE)
    if fb_photo_match:
        return f"https://www.facebook.com/photo?fbid={fb_photo_match.group(1)}"

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

    # Also load PM and TPL instagram_urls so cross-posted images get pillar enrichment.
    # PM posts to @will.byron88, TPL posts to @willpowerprotocols — tokens already in .env.
    try:
        import sqlite3 as _sqlite3
        _conn = _sqlite3.connect(db_path)
        _brand_map = [
            ("pm_posts",  "Prehistoric Memories", "@will.byron88"),
            ("tpl_posts", "The Protocol Lab",     "@willpowerprotocols"),
        ]
        for _table, _brand, _ig_acct in _brand_map:
            _cols = [r[1] for r in _conn.execute(f"PRAGMA table_info({_table})").fetchall()]
            if "instagram_url" not in _cols:
                continue
            _ig_rows = _conn.execute(
                f"SELECT instagram_url, pillar FROM {_table} "
                f"WHERE instagram_url IS NOT NULL AND instagram_url != '' AND posted='Y'"
            ).fetchall()
            _added = 0
            for _ig_url, _pillar in _ig_rows:
                _ig_url = str(_ig_url).strip()
                if not _ig_url:
                    continue
                enrich_rows.append({
                    "url":               _ig_url,
                    "url_normalized":    normalize_url(_ig_url),
                    "book_or_offer":     _brand,
                    "content_type":      "Image",
                    "content_pillar":    str(_pillar or ""),
                    "campaign":          "",
                    "short_num":         "",
                    "episode_num":       "",
                    "account":           _ig_acct,
                    "x_account":         "",
                    "already_crossposted": True,
                    "on_x":              False,
                })
                _added += 1
            if _added:
                print(f"  -> {_brand} IG cross-posts: {_added} enrichment URLs added")
        _conn.close()
    except Exception as _e:
        print(f"Could not load PM/TPL instagram_urls: {_e}")

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

    return content_map, queue_df, ep_queue


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

def generate_monday_plan(df, queue_df, ep_queue=None):
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
    pillar_engagement = {}  # pillar -> avg_eng_pct
    if has_content_map:
        known = df[df["content_pillar"].ne("—")]
        if not known.empty:
            pillar_views = known.groupby("content_pillar")["views"].sum()
            if not pillar_views.empty:
                pillar_winner = pillar_views.idxmax()
                pillar_scores = pillar_views.sort_values(ascending=False).to_dict()
            pillar_eng = known.groupby("content_pillar")["engagement_rate_percent"].mean()
            pillar_engagement = {p: round(float(e), 1) for p, e in pillar_eng.items()}

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
            pillar = str(item.get("content_pillar", ""))
            pillar_eng = pillar_engagement.get(pillar)

            schedule.append({
                "day": day,
                "book": chosen_book,
                "short_num": str(item.get("short_num", "?")),
                "topic": str(item.get("script_topic", "")),
                "pillar": pillar,
                "pillar_eng": pillar_eng,
                "account": account,
                "x_account": x_account,
                "note": note,
            })

            last_book = chosen_book
            book_cycle = (
                [b for b in book_cycle if b != chosen_book] + [chosen_book]
            )

    # Books that have matched posts historically but nothing in last 30 days.
    from datetime import date as _date, timedelta
    cutoff_str = (_date.today() - timedelta(days=30)).strftime("%Y-%m-%d")
    books_no_recent = []
    if has_content_map:
        recent = df[df["published_at"].fillna("") >= cutoff_str]
        active_books    = set(recent[recent["book_or_offer"].ne("—")]["book_or_offer"].unique())
        all_known_books = set(df[df["book_or_offer"].ne("—")]["book_or_offer"].unique())
        books_no_recent = sorted(all_known_books - active_books)

    # ── Unposted long-form video episodes ────────────────────────
    episode_items = []
    if ep_queue is not None and not ep_queue.empty:
        for _, row in ep_queue.iterrows():
            episode_items.append({
                "book":       str(row.get("book_or_offer", "Unknown")),
                "ep_num":     str(row.get("episode_num", "?")),
                "topic":      str(row.get("script_topic", "")),
                "pillar":     str(row.get("content_pillar", "")),
            })

    # ── Pillar engagement from posted FB image rows (brand-specific) ─
    def _img_pillar_eng(platform_key):
        """Return {pillar: (avg_eng_pct, avg_reach, post_count)} for a FB image platform."""
        p = df[df["platform"] == platform_key]
        if p.empty or "pillar" not in p.columns:
            return {}
        known = p[p["pillar"].notna() & p["pillar"].ne("") & p["pillar"].ne("—")]
        if known.empty:
            return {}
        result = {}
        for pillar, grp in known.groupby("pillar"):
            result[pillar] = {
                "eng":   round(float(grp["engagement_rate_percent"].mean()), 2),
                "reach": int(grp["reach"].mean()) if "reach" in grp.columns else 0,
                "posts": len(grp),
            }
        return result

    tpl_pillar_eng = _img_pillar_eng("FB-Image-TheProtocolLab")
    pm_pillar_eng  = _img_pillar_eng("FB-Image-PrehistoricMemories")

    # ── Unposted image posts from tpl_posts and pm_posts ─────────
    unposted_images = []
    try:
        import sqlite3 as _sq
        _conn = _sq.connect(WPP_DB_FILE)
        _cur  = _conn.cursor()
        for _table, _brand, _peng in [
            ("tpl_posts", "The Protocol Lab",    tpl_pillar_eng),
            ("pm_posts",  "Prehistoric Memories", pm_pillar_eng),
        ]:
            _cur.execute(
                f"SELECT post_id, pillar, topic, post_type FROM {_table} WHERE posted='N' ORDER BY post_id"
            )
            for pid, pillar, topic, ptype in _cur.fetchall():
                pillar = pillar or "—"
                unposted_images.append({
                    "brand":     _brand,
                    "post_id":   pid,
                    "pillar":    pillar,
                    "topic":     topic or "—",
                    "type":      ptype or "image",
                    "pillar_eng": _peng.get(pillar),
                })
        _conn.close()
    except Exception:
        pass

    return {
        "schedule": schedule,
        "repurpose": repurpose,
        "pillar_winner": pillar_winner,
        "pillar_scores": pillar_scores,
        "pillar_engagement": pillar_engagement,
        "book_test_signals": book_test_signals,
        "unposted_count": unposted_count,
        "has_queue": has_queue,
        "has_content_map": has_content_map,
        "books_no_recent": books_no_recent,
        "episode_items": episode_items,
        "unposted_images": unposted_images,
        "tpl_pillar_eng": tpl_pillar_eng,
        "pm_pillar_eng":  pm_pillar_eng,
    }


def build_monday_plan_html(plan):
    today_str = date.today().strftime("%A, %B %d, %Y")

    if plan["schedule"]:
        items_html = ""
        for i, s in enumerate(plan["schedule"], 1):
            note_html = (
                f' <span style="color:#C9A84C;font-size:11px;font-weight:bold">{s["note"]}</span>'
                if s["note"] else ""
            )
            account   = s.get("account", "@willpowerprotocols")
            x_account = s.get("x_account", "@wpprotocols")
            wb        = account == "@will.byron88"
            acct_color = "#C9894C" if wb else "#AAB4C0"
            x_color    = "#C9894C" if wb else "#1DA1F2"
            x_handle   = "@willbyron" if wb else x_account
            pillar_eng = s.get("pillar_eng")
            if pillar_eng is not None:
                if pillar_eng >= 20:
                    psig = f'<span style="color:#FF6B6B;font-size:10px;font-weight:bold;margin-left:6px">EXCEPTIONAL {pillar_eng}%</span>'
                elif pillar_eng >= 10:
                    psig = f'<span style="color:#5CFF7E;font-size:10px;font-weight:bold;margin-left:6px">HOT {pillar_eng}%</span>'
                elif pillar_eng >= 5:
                    psig = f'<span style="color:#AAB4C0;font-size:10px;margin-left:6px">{pillar_eng}% eng</span>'
                else:
                    psig = f'<span style="color:#6B7A8D;font-size:10px;margin-left:6px">{pillar_eng}% eng</span>'
            else:
                psig = '<span style="color:#6B7A8D;font-size:10px;margin-left:6px">no data</span>'
            items_html += f"""
            <div style="display:flex;align-items:flex-start;gap:12px;padding:10px 0;border-bottom:1px solid rgba(255,255,255,.06)">
                <div style="min-width:22px;color:#C9A84C;font-weight:bold;font-size:14px;padding-top:1px">{i}.</div>
                <div style="font-size:13px;color:#D7DEE8;line-height:1.5">
                    <strong style="color:#FFFFFF">{s["book"]}</strong>
                    &nbsp;&middot;&nbsp; Short #{s["short_num"]}
                    &nbsp;&middot;&nbsp; {s["topic"]}{note_html}
                    <div style="color:#AAB4C0;font-size:12px;margin-top:2px">
                        {s["pillar"]}{psig} &nbsp;&middot;&nbsp;
                        <span style="color:{acct_color}">{account}</span>
                        &nbsp;&middot;&nbsp;
                        <span style="color:{x_color}">{x_handle}</span>
                    </div>
                </div>
            </div>"""

        remaining = max(plan["unposted_count"] - len(plan["schedule"]), 0)
        schedule_html = f"""
        <div class="action-section">
            <h3>Post These Next, In Order</h3>
            {items_html}
            <p class="action-note">{remaining} more in queue after these.</p>
        </div>"""
    elif not plan["has_queue"]:
        schedule_html = """
        <div class="action-section">
            <h3>Post Queue</h3>
            <p class="action-note">Add <strong>wpp.db</strong> to unlock the post queue.</p>
        </div>"""
    else:
        schedule_html = """
        <div class="action-section">
            <h3>Post Queue</h3>
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

    # ── Unposted long-form video episodes ────────────────────────
    episode_items   = plan.get("episode_items", [])
    unposted_images = plan.get("unposted_images", [])

    if episode_items:
        ep_rows = ""
        for ep in episode_items:
            ep_rows += f"""
            <div style="display:flex;align-items:flex-start;gap:12px;padding:8px 0;border-bottom:1px solid rgba(255,255,255,.06)">
                <div style="min-width:22px;color:#C9A84C;font-weight:bold;font-size:13px;padding-top:1px">&#9654;</div>
                <div style="font-size:13px;color:#D7DEE8;line-height:1.5">
                    <strong style="color:#FFFFFF">{ep["book"]}</strong>
                    &nbsp;&middot;&nbsp; Episode #{ep["ep_num"]}
                    &nbsp;&middot;&nbsp; {ep["topic"]}
                    <div style="color:#AAB4C0;font-size:11px;margin-top:2px">{ep["pillar"]}</div>
                </div>
            </div>"""
        episodes_html = f"""
        <div class="action-section">
            <h3>Unposted Long-Form Videos ({len(episode_items)})</h3>
            {ep_rows}
        </div>"""
    else:
        episodes_html = ""

    if unposted_images:
        img_rows = ""
        for img in unposted_images:
            brand_color = "#E1306C" if "Protocol Lab" in img["brand"] else "#C9A84C"
            pdata = img.get("pillar_eng")
            if pdata is not None:
                eng   = pdata["eng"]
                reach = pdata["reach"]
                posts = pdata["posts"]
                if eng >= 1.5:
                    psig = f'<span style="color:#5CFF7E;font-size:10px;font-weight:bold;margin-left:6px">HOT {eng}% eng &middot; {reach} avg reach</span>'
                elif eng >= 0.5:
                    psig = f'<span style="color:#C9A84C;font-size:10px;margin-left:6px">{eng}% eng &middot; {reach} avg reach</span>'
                elif reach >= 150:
                    psig = f'<span style="color:#C9A84C;font-size:10px;margin-left:6px">{reach} avg reach</span>'
                elif reach >= 50:
                    psig = f'<span style="color:#AAB4C0;font-size:10px;margin-left:6px">{reach} avg reach</span>'
                else:
                    psig = f'<span style="color:#6B7A8D;font-size:10px;margin-left:6px">low reach so far ({posts} posted)</span>'
            else:
                psig = '<span style="color:#6B7A8D;font-size:10px;margin-left:6px">no posts yet</span>'
            img_rows += f"""
            <div style="display:flex;align-items:flex-start;gap:12px;padding:8px 0;border-bottom:1px solid rgba(255,255,255,.06)">
                <div style="min-width:22px;color:{brand_color};font-weight:bold;font-size:11px;padding-top:2px">IMG</div>
                <div style="font-size:13px;color:#D7DEE8;line-height:1.5">
                    <span style="color:{brand_color};font-size:11px">{img["brand"]}</span>
                    &nbsp;&middot;&nbsp; #{img["post_id"]}
                    &nbsp;&middot;&nbsp; {img["topic"]}
                    <div style="color:#AAB4C0;font-size:11px;margin-top:2px">{img["pillar"]}{psig} &nbsp;&middot;&nbsp; {img["type"]}</div>
                </div>
            </div>"""
        images_html = f"""
        <div class="action-section">
            <h3>Unposted Image Posts — TPL &amp; PM ({len(unposted_images)})</h3>
            {img_rows}
        </div>"""
    else:
        images_html = ""

    return f"""
    <div class="action-plan">
        <h2>Monday Action Plan — {today_str}</h2>
        {schedule_html}
        {episodes_html}
        {images_html}
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
# Intelligence Panel Builder
# ============================================================

def build_intelligence_panel_html(df, trends_html="", gumroad_data=None, gumroad_posts=None):
    """Build the 7-section Intelligence Panel appended to the CEO tab."""
    import sqlite3 as _sqlite3
    import json as _json
    from datetime import date as _date

    def _sf(v):
        try: return float(v or 0)
        except: return 0.0

    def _si(v):
        try: return int(float(v or 0))
        except: return 0

    # ── Load DB ───────────────────────────────────────────────────
    try:
        conn = _sqlite3.connect(WPP_DB_FILE)
        cur  = conn.cursor()

        def _cfg(key):
            cur.execute("SELECT value FROM config WHERE key=?", (key,))
            r = cur.fetchone()
            return _json.loads(r[0]) if r else []

        pub_q      = _cfg("publishing_queue")
        rocket_q   = _cfg("rocket_queue")
        pillar_map = _cfg("pillar_book_map")
        triggers   = _cfg("business_triggers")
        gumroad    = _cfg("gumroad_guides")

        cur.execute("""
            SELECT snapshot_year, snapshot_month, SUM(royalties_usd), SUM(ku_pages)
            FROM kdp_snapshots
            GROUP BY snapshot_year, snapshot_month
            ORDER BY snapshot_year DESC, snapshot_month DESC
            LIMIT 6
        """)
        kdp_monthly_rows = cur.fetchall()

        cur.execute("""
            SELECT k.book_key, b.title,
                   SUM(k.royalties_usd), SUM(k.kindle_units), SUM(k.ku_pages)
            FROM kdp_snapshots k JOIN books b ON k.book_key = b.book_key
            GROUP BY k.book_key
            ORDER BY SUM(k.royalties_usd) DESC
        """)
        kdp_by_book = cur.fetchall()

        cur.execute("""
            SELECT book_key, COUNT(*) FROM content
            WHERE posted='Y' AND content_type='Short'
            GROUP BY book_key
        """)
        shorts_posted = dict(cur.fetchall())

        cur.execute("""
            SELECT c.book_key, b.title, COUNT(*), MIN(c.short_num)
            FROM content c JOIN books b ON c.book_key = b.book_key
            WHERE c.posted='N' AND c.content_type='Short'
            GROUP BY c.book_key ORDER BY b.title
        """)
        unposted_rows = cur.fetchall()

        conn.close()
    except Exception as e:
        return f'<p style="color:#FF6B6B">Intelligence Panel load error: {e}</p>'

    today = _date.today()

    # ── Lookup structures ─────────────────────────────────────────
    slot_to_book = {b["slot"]: b for b in pub_q}

    slot_to_pillars = {}
    for entry in pillar_map:
        for slot in entry.get("maps_to_slots", []):
            slot_to_pillars.setdefault(slot, []).append(entry["pillar"])

    # Pillar stats from live df (engagement lives here, not in empty analytics_snapshots)
    pillar_stats = {}
    if "content_pillar" in df.columns and "engagement_rate_percent" in df.columns:
        known_p = df[
            df["content_pillar"].notna()
            & df["content_pillar"].ne("")
            & df["content_pillar"].ne("—")
        ]
        for pillar, grp in known_p.groupby("content_pillar"):
            pillar_stats[pillar] = {
                "count":   len(grp),
                "avg_eng": round(float(grp["engagement_rate_percent"].mean()), 1),
                "views":   _si(grp["views"].sum()),
            }

    high_signal = {p for p, s in pillar_stats.items() if s["avg_eng"] >= 10}
    exceptional = {p for p, s in pillar_stats.items() if s["avg_eng"] >= 20}

    # ── Title → revenue/book_key maps ────────────────────────────
    title_to_rev = {t: _sf(rev) for _, t, rev, _, _ in kdp_by_book}
    title_to_bk  = {t: bk for bk, t, _, _, _ in kdp_by_book}

    # ── SECTION 1 — Monthly KDP Trend ─────────────────────────────
    cur_month_str = today.strftime("%Y-%m")
    days_elapsed  = today.day
    cur_rev = 0.0
    cur_ku  = 0
    prev_months = []
    for yr, mo, rev, ku in kdp_monthly_rows:
        if mo == cur_month_str:
            cur_rev = _sf(rev)
            cur_ku  = _si(ku)
        else:
            prev_months.append((mo, _sf(rev), _si(ku)))

    run_rate = (cur_rev / days_elapsed * 30) if days_elapsed > 0 else 0.0

    months_display = [(cur_month_str, cur_rev, cur_ku)] + prev_months[:2]
    trend_rows = ""
    for i, (mo, rev, ku) in enumerate(months_display):
        if i == 0:
            mom_cell = (
                f'<span style="color:#AAB4C0;font-size:11px">current &nbsp;'
                f'(run rate: <strong style="color:#C9A84C">${run_rate:.2f}/mo</strong>)</span>'
            )
        else:
            prior_rev = months_display[i - 1][1]
            if rev > 0:
                pct     = (prior_rev - rev) / rev * 100
                clr     = "#5CFF7E" if pct >= 0 else "#FF6B6B"
                sign    = "+" if pct >= 0 else ""
                mom_cell = f'<span style="color:{clr}">{sign}{pct:.1f}%</span>'
            else:
                mom_cell = "—"
        trend_rows += f"""
            <tr>
                <td style="font-size:12px">{mo}</td>
                <td style="text-align:right"><strong style="color:#C9A84C">${rev:.2f}</strong></td>
                <td style="text-align:right">{ku:,}</td>
                <td>{mom_cell}</td>
            </tr>"""

    s1_html = f"""
    <div class="scoreboard-card" style="margin-bottom:20px">
        <h2 style="margin-top:0;font-size:15px">Monthly KDP Trend</h2>
        <table class="scoreboard-table" style="width:100%">
            <thead><tr>
                <th>Month</th><th style="text-align:right">Revenue</th>
                <th style="text-align:right">KU Pages</th><th>MoM Change</th>
            </tr></thead>
            <tbody>{trend_rows}</tbody>
        </table>
        <p style="color:#AAB4C0;font-size:11px;margin:8px 0 0">
            Run rate = revenue so far / {days_elapsed} days elapsed x 30
        </p>
    </div>"""

    # ── SECTION 2 — $500/Month Trigger Tracker ────────────────────
    progress_pct = min(run_rate / 500 * 100, 100)
    bar_color    = "#5CFF7E" if run_rate >= 500 else "#C9A84C" if run_rate >= 250 else "#FFB347"
    trigger_rows = ""
    for t in triggers:
        status   = t.get("status", "pending")
        clr      = "#5CFF7E" if status == "complete" else "#C9A84C"
        dot      = "&#9679;" if status == "complete" else "&#9675;"
        trigger_rows += f"""
            <tr>
                <td style="color:{clr};font-size:12px">{dot}&nbsp;{t.get('trigger','')}</td>
                <td style="font-size:12px;color:#AAB4C0">{t.get('unlocks','')}</td>
                <td style="text-align:center"><span style="color:{clr};font-size:11px">{status}</span></td>
            </tr>"""

    s2_html = f"""
    <div class="scoreboard-card" style="margin-bottom:20px">
        <h2 style="margin-top:0;font-size:15px">$500/Month Trigger Tracker</h2>
        <div style="margin-bottom:16px">
            <div style="display:flex;justify-content:space-between;font-size:12px;margin-bottom:5px">
                <span style="color:#AAB4C0">Current monthly run rate</span>
                <strong style="color:{bar_color}">${run_rate:.2f}/mo</strong>
            </div>
            <div style="background:#1A2A3A;border-radius:6px;height:14px;overflow:hidden">
                <div style="width:{progress_pct:.1f}%;height:100%;background:{bar_color};border-radius:6px"></div>
            </div>
            <div style="text-align:right;font-size:11px;color:#AAB4C0;margin-top:3px">
                {progress_pct:.1f}% of $500 goal
            </div>
        </div>
        <table class="scoreboard-table" style="width:100%">
            <thead><tr>
                <th>Trigger</th><th>Unlocks</th><th style="text-align:center">Status</th>
            </tr></thead>
            <tbody>{trigger_rows}</tbody>
        </table>
    </div>"""

    # ── SECTION 3 — Revenue Per Book Ranked ───────────────────────
    book_rev_rows = ""
    rank = 0
    for bk, title, total_rev, kindle_units, ku_pages in kdp_by_book:
        rev = _sf(total_rev)
        ku  = _si(ku_pages)
        if rev == 0 and ku == 0:
            continue
        rank += 1
        sp = shorts_posted.get(bk, 0)
        book_rev_rows += f"""
            <tr>
                <td style="text-align:center;color:#AAB4C0">{rank}</td>
                <td style="font-size:12px">{title}</td>
                <td style="text-align:right"><strong style="color:#C9A84C">${rev:.2f}</strong></td>
                <td style="text-align:right">{ku:,}</td>
                <td style="text-align:center">{sp}</td>
            </tr>"""
    if not book_rev_rows:
        book_rev_rows = '<tr><td colspan="5" style="color:#AAB4C0;text-align:center">No KDP revenue data yet</td></tr>'

    s3_html = f"""
    <div class="scoreboard-card" style="margin-bottom:20px">
        <h2 style="margin-top:0;font-size:15px">Revenue Per Book — Ranked</h2>
        <table class="scoreboard-table" style="width:100%">
            <thead><tr>
                <th style="text-align:center">#</th><th>Title</th>
                <th style="text-align:right">Revenue</th>
                <th style="text-align:right">KU Pages</th>
                <th style="text-align:center">Shorts Posted</th>
            </tr></thead>
            <tbody>{book_rev_rows}</tbody>
        </table>
    </div>"""

    # ── SECTION 4a — Pillar Performance ───────────────────────────
    eligible = sorted(
        [(p, s) for p, s in pillar_stats.items() if s["count"] >= 3],
        key=lambda x: x[1]["avg_eng"], reverse=True
    )
    pillar_rows = ""
    for pillar, s in eligible:
        eng = s["avg_eng"]
        if eng >= 20:
            badge = ' <span style="color:#FF6B6B">EXCEPTIONAL &#128293;</span>'
        elif eng >= 10:
            badge = ' <span style="color:#C9A84C">HIGH SIGNAL &#11088;</span>'
        else:
            badge = ""
        pillar_rows += f"""
            <tr>
                <td style="font-size:12px">{pillar}{badge}</td>
                <td style="text-align:center">{s['count']}</td>
                <td style="text-align:right"><strong style="color:#C9A84C">{eng}%</strong></td>
                <td style="text-align:right">{s['views']:,}</td>
            </tr>"""
    if not pillar_rows:
        pillar_rows = '<tr><td colspan="4" style="color:#AAB4C0;text-align:center;font-size:12px">Not enough data yet — need 3+ posts per pillar.</td></tr>'

    s4a_html = f"""
    <div class="scoreboard-card" style="margin-bottom:20px">
        <h2 style="margin-top:0;font-size:15px">4a &mdash; Pillar Performance</h2>
        <table class="scoreboard-table" style="width:100%">
            <thead><tr>
                <th>Pillar</th><th style="text-align:center">Posts</th>
                <th style="text-align:right">Avg Eng %</th><th style="text-align:right">Total Views</th>
            </tr></thead>
            <tbody>{pillar_rows}</tbody>
        </table>
        <p style="color:#AAB4C0;font-size:11px;margin:8px 0 0">
            Only pillars with 3+ posts shown. HIGH SIGNAL = &gt;10% engagement. EXCEPTIONAL = &gt;20%.
        </p>
    </div>"""

    # ── SECTION 4b — Book Queue with Pillar Intelligence ──────────
    queue_planned = sorted(
        [b for b in pub_q if b.get("status") in ("planned", "idea") and b.get("priority", 99) <= 3],
        key=lambda x: (x.get("priority", 99), x.get("slot", 99))
    )
    queue_rows = ""
    for book in queue_planned:
        slot     = book.get("slot")
        title    = book.get("title", "")
        series   = book.get("series", "")
        price    = book.get("price", "")
        status   = book.get("status", "")
        priority = book.get("priority", "")
        rocket_v = book.get("rocket_validated", False)

        pillars_for_slot = slot_to_pillars.get(slot, [])
        mapped_pillar    = pillars_for_slot[0] if pillars_for_slot else None
        p_data           = pillar_stats.get(mapped_pillar) if mapped_pillar else None

        if p_data:
            p_eng_str = f"{p_data['avg_eng']}% ({p_data['count']} posts)"
        elif mapped_pillar:
            p_eng_str = "No data yet"
        else:
            p_eng_str = "—"

        if mapped_pillar is None:
            rec, rec_color = "No signal yet", "#AAB4C0"
        elif p_data is None or p_data["count"] < 3:
            rec, rec_color = "Insufficient pillar data — use Rocket only", "#AAB4C0"
        elif p_data["avg_eng"] >= 10 and rocket_v:
            rec, rec_color = "GO — write this next &#128640;", "#5CFF7E"
        elif p_data["avg_eng"] >= 10 and not rocket_v:
            rec, rec_color = "Data supports prioritizing — run Rocket &#11088;", "#C9A84C"
        else:
            rec, rec_color = "Insufficient pillar data — use Rocket only", "#AAB4C0"

        s_color   = "#C9A84C" if status == "planned" else "#AAB4C0"
        rocket_td = '<span style="color:#5CFF7E">Yes</span>' if rocket_v else '<span style="color:#AAB4C0">No</span>'

        queue_rows += f"""
            <tr>
                <td style="text-align:center;color:#AAB4C0">{slot}</td>
                <td style="font-size:12px"><strong>{title}</strong>
                    <div style="color:#AAB4C0;font-size:10px">{series}</div></td>
                <td style="text-align:center">{price}</td>
                <td style="text-align:center"><span style="color:{s_color}">{status}</span></td>
                <td style="text-align:center;color:#AAB4C0">{priority}</td>
                <td style="font-size:11px">{mapped_pillar or '—'}</td>
                <td style="font-size:11px;color:#AAB4C0">{p_eng_str}</td>
                <td style="text-align:center">{rocket_td}</td>
                <td style="font-size:11px;color:{rec_color}">{rec}</td>
            </tr>"""

    s4b_html = f"""
    <div class="scoreboard-card" style="margin-bottom:20px">
        <h2 style="margin-top:0;font-size:15px">4b &mdash; Book Queue with Pillar Intelligence</h2>
        <div class="table-wrap">
        <table style="min-width:1100px">
            <thead><tr>
                <th>Slot</th><th>Title</th><th style="text-align:center">Price</th>
                <th style="text-align:center">Status</th><th style="text-align:center">Pri</th>
                <th>Mapped Pillar</th><th>Pillar Eng</th>
                <th style="text-align:center">Rocket</th><th>Recommendation</th>
            </tr></thead>
            <tbody>{queue_rows}</tbody>
        </table>
        </div>
        <p style="color:#AAB4C0;font-size:11px;margin:8px 0 0;font-style:italic">
            Directional only &mdash; 90 days minimum for confident decisions.
            Always validate with Publisher Rocket.
        </p>
    </div>"""

    # ── SECTION 4c — Publisher Rocket Queue ───────────────────────
    rocket_rows = ""
    for rq in rocket_q:
        rank      = rq.get("rank", "")
        keyword   = rq.get("keyword", "")
        priority  = rq.get("priority", "")
        slot      = rq.get("maps_to_slot")
        validated = rq.get("validated", False)
        notes     = rq.get("notes", "")

        book_title = slot_to_book.get(slot, {}).get("title", "—") if slot else "—"

        if priority == "high":
            p_color = "#C9A84C"
        elif priority == "medium":
            p_color = "#D7DEE8"
        else:
            p_color = "#6B7A8D"

        flag = ""
        if priority == "watch" and slot:
            for p in slot_to_pillars.get(slot, []):
                if p in high_signal:
                    flag = f' &nbsp;<span style="color:#C9A84C;font-size:11px">Pillar signal supports researching now.</span>'
                    break

        val_td = '<span style="color:#5CFF7E">Yes</span>' if validated else '<span style="color:#AAB4C0">No</span>'
        rocket_rows += f"""
            <tr style="color:{p_color}">
                <td style="text-align:center">{rank}</td>
                <td style="font-size:12px">{keyword}{flag}</td>
                <td style="text-align:center;font-size:11px">{priority}</td>
                <td style="font-size:11px;color:#AAB4C0">{book_title}</td>
                <td style="text-align:center">{val_td}</td>
                <td style="font-size:11px;color:#6B7A8D">{notes}</td>
            </tr>"""

    s4c_html = f"""
    <div class="scoreboard-card" style="margin-bottom:20px">
        <h2 style="margin-top:0;font-size:15px">4c &mdash; Publisher Rocket Queue</h2>
        <div class="table-wrap">
        <table style="min-width:900px">
            <thead><tr>
                <th style="text-align:center">#</th><th>Keyword</th>
                <th style="text-align:center">Priority</th><th>Maps To Book</th>
                <th style="text-align:center">Validated</th><th>Notes</th>
            </tr></thead>
            <tbody>{rocket_rows}</tbody>
        </table>
        </div>
    </div>"""


    # ── SECTION 6 — Platform ROI ──────────────────────────────────
    roi_rows = ""
    if "book_or_offer" in df.columns:
        known_df = df[df["book_or_offer"].ne("—") & df["book_or_offer"].notna()]
        if not known_df.empty:
            views_by_title = known_df.groupby("book_or_offer")["views"].sum().to_dict()
            all_titles     = set(views_by_title) | set(title_to_rev)
            roi_data = []
            for title in all_titles:
                views = _si(views_by_title.get(title, 0))
                rev   = _sf(title_to_rev.get(title, 0))
                bk    = title_to_bk.get(title, "")
                sp    = shorts_posted.get(bk, 0)
                roi_data.append((title, views, sp, rev))
            roi_data.sort(key=lambda x: x[1], reverse=True)
            for title, views, sp, rev in roi_data:
                if views == 0 and rev == 0:
                    continue
                vpd = f"{views / rev:,.0f}" if rev > 0 else "—"
                roi_rows += f"""
                    <tr>
                        <td style="font-size:12px">{title}</td>
                        <td style="text-align:right"><strong>{views:,}</strong></td>
                        <td style="text-align:center">{sp}</td>
                        <td style="text-align:right"><strong style="color:#C9A84C">${rev:.2f}</strong></td>
                        <td style="text-align:right;color:#AAB4C0">{vpd}</td>
                    </tr>"""
    if not roi_rows:
        roi_rows = '<tr><td colspan="5" style="color:#AAB4C0;text-align:center">No matched data available yet.</td></tr>'

    s6_html = f"""
    <div class="scoreboard-card" style="margin-bottom:20px">
        <h2 style="margin-top:0;font-size:15px">Platform ROI</h2>
        <table class="scoreboard-table" style="width:100%">
            <thead><tr>
                <th>Book</th>
                <th style="text-align:right">Total Views</th>
                <th style="text-align:center">Shorts Posted</th>
                <th style="text-align:right">Revenue Earned</th>
                <th style="text-align:right">Views per $1</th>
            </tr></thead>
            <tbody>{roi_rows}</tbody>
        </table>
    </div>"""

    # ── SECTION 7 — Gumroad Pipeline ──────────────────────────────
    gumroad_rows = ""
    for g in gumroad:
        status  = g.get("status", "")
        s_color = "#5CFF7E" if status == "live" else "#C9A84C" if status == "in-progress" else "#AAB4C0"
        gumroad_rows += f"""
            <tr>
                <td style="font-size:12px">{g.get('title','')}</td>
                <td style="text-align:center">{g.get('price','')}</td>
                <td style="text-align:center;color:#AAB4C0">{g.get('pages','')}</td>
                <td style="text-align:center"><span style="color:{s_color}">{status}</span></td>
                <td style="font-size:11px;color:#AAB4C0">{g.get('notes','')}</td>
            </tr>"""

    s7_html = f"""
    <div class="scoreboard-card" style="margin-bottom:20px">
        <h2 style="margin-top:0;font-size:15px">Gumroad Pipeline</h2>
        <p style="color:#AAB4C0;font-size:12px;margin-top:0">
            Trigger: email list + website live at $500/mo.
        </p>
        <div class="table-wrap">
        <table style="min-width:900px">
            <thead><tr>
                <th>Title</th><th style="text-align:center">Price</th>
                <th style="text-align:center">Pages</th>
                <th style="text-align:center">Status</th><th>Notes</th>
            </tr></thead>
            <tbody>{gumroad_rows}</tbody>
        </table>
        </div>
    </div>"""

    # ── Gold INTELLIGENCE PANEL divider ───────────────────────────
    gold_divider = """
    <div style="font-family:'Bebas Neue','Impact',sans-serif;font-size:26px;
                color:#C9A84C;letter-spacing:4px;margin:36px 0 20px;
                border-bottom:2px solid #C9A84C;padding-bottom:8px">
        INTELLIGENCE PANEL
    </div>"""

    gumroad_section_html = build_gumroad_revenue_html(gumroad_data, posts=gumroad_posts) if gumroad_data else ""

    return f"""
    <div style="margin-top:32px">
        <h2 style="color:#C9A84C;margin-bottom:8px;font-size:16px">KDP Revenue Intelligence</h2>
        {s1_html}
        {s2_html}
        {s3_html}
        {gold_divider}
        {trends_html}
        {s4a_html}
        {s4b_html}
        {s4c_html}
        {s6_html}
        {s7_html}
        {gumroad_section_html}
    </div>"""


# ============================================================
# CEO Tab Builder
# ============================================================

def build_ceo_tab_html(df, plan, kdp_total_revenue, monday_plan_html, ai_briefing_html="", trends_html="", gumroad_data=None, gumroad_posts=None):
    """CEO overview tab — five-stat hero, platform health, top posts, what's working."""
    _trends_html = trends_html  # passed through to build_intelligence_panel_html
    total_views = safe_int(df["views"].sum())
    total_posts = len(df)
    avg_eng     = round(df["engagement_rate_percent"].mean(), 2) if total_posts else 0
    queue_count = plan["unposted_count"] if plan else 0
    yt_minutes  = safe_int(
        df[df["platform"] == "YouTube"]["estimated_minutes_watched"].sum()
    )

    # ── Hero stats ────────────────────────────────────────────────
    if gumroad_data:
        _gumroad_stat = f"""
        <div class="ceo-stat">
            <div class="ceo-stat-label">Gumroad Revenue</div>
            <div class="ceo-stat-value">${gumroad_data["total_revenue"]:.2f}</div>
            <div class="ceo-stat-sub">{gumroad_data["total_sales"]} sales</div>
        </div>"""
    else:
        _gumroad_stat = ""

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
        {_gumroad_stat}
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
    from datetime import date as _today_date
    today = _today_date.today()

    PLATFORM_DISPLAY = [
        ("Facebook",                     "Facebook Reels (WPP)"),
        ("Facebook-WB",                  "Facebook Reels (Will Byron)"),
        ("Instagram",                    "Instagram @willpowerprotocols"),
        ("Instagram-WB",                 "Instagram @will.byron88"),
        ("YouTube",                      "YouTube (Will Power Protocols)"),
        ("FB-Image-PrehistoricMemories", "Facebook Images (Prehistoric Memories)"),
        ("FB-Image-TheProtocolLab",      "Facebook Images (The Protocol Lab)"),
        ("FB-Image-WillByron",           "Facebook Images (Will Byron)"),
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
            # Last post date + staleness warning
            last_pub = p["published_at"].dropna().max() or ""
            try:
                days_ago = (today - _today_date.fromisoformat(str(last_pub)[:10])).days
                last_pub_str = str(last_pub)[:10]
                if days_ago > 7:
                    status_html = f'<span style="color:#FFB347;font-size:12px">Silent {days_ago}d</span>'
                else:
                    status_html = f'<span style="color:#5CFF7E;font-size:12px">Active</span>'
            except Exception:
                last_pub_str = "—"
                status_html  = '<span style="color:#5CFF7E;font-size:12px">Active</span>'
            plat_rows += f"""
            <tr>
                <td><strong>{plat_label}</strong>{extra}</td>
                <td style="text-align:center">{posts}</td>
                <td style="text-align:center">{views:,}</td>
                <td style="text-align:center">{eng_str}</td>
                <td style="text-align:center;font-size:12px;color:#AAB4C0">{last_pub_str}</td>
                <td>{status_html}</td>
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
                    <th style="text-align:center">Last Post</th>
                    <th>Status</th>
                </tr>
            </thead>
            <tbody>{plat_rows}</tbody>
        </table>
    </div>"""

    # ── Books with no activity in last 30 days ────────────────────
    if plan and plan.get("books_no_recent"):
        book_items = "".join(
            f'<li style="margin-bottom:5px;color:#D7DEE8">{b}</li>'
            for b in plan["books_no_recent"]
        )
        books_silent_card = f"""
    <div class="scoreboard-card" style="margin-bottom:24px;border-color:#FFB347">
        <h2 style="margin-top:0;font-size:16px;color:#FFB347">No Posts in Last 30 Days</h2>
        <ul style="margin:0;padding-left:20px;font-size:13px;line-height:1.6">
            {book_items}
        </ul>
        <p style="color:#AAB4C0;font-size:12px;margin:8px 0 0">
            These books have matched content in the DB but no new posts in the last 30 days.
        </p>
    </div>"""
    else:
        books_silent_card = ""

    # ── Top 5 Posts ────────────────────────────────────────────────
    top5 = df.sort_values("views", ascending=False).head(5)
    top5_cards = ""
    for _, row in top5.iterrows():
        plat         = safe_text(row.get("platform"))
        plat_display = PLATFORM_LABELS.get(plat, plat)
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
                <span class="platform-badge">{plat_display}</span>
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

    gumroad_card_html = build_gumroad_revenue_html(gumroad_data, posts=gumroad_posts) if gumroad_data else ""
    intelligence_panel_html = build_intelligence_panel_html(df, trends_html=_trends_html, gumroad_data=gumroad_data, gumroad_posts=gumroad_posts)

    return f"""
    {ai_briefing_html}
    {hero_html}
    {gumroad_card_html}
    {monday_plan_html}

    {what_working}
    {platform_health}
    {books_silent_card}
    {top5_html}
    {intelligence_panel_html}"""


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
               instagram_performance_html="",
               kdp_revenue_html="", filtered_repurpose_count=None, plan=None,
               ai_briefing_html="", trends_html="", gumroad_data=None, gumroad_posts=None):
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

    ceo_tab_html = build_ceo_tab_html(df, plan, kdp_total_revenue, monday_plan_html, ai_briefing_html, trends_html, gumroad_data=gumroad_data, gumroad_posts=gumroad_posts)

    winners_count = df["content_signal"].str.contains("Winner", na=False).sum()
    sticky_count = df["content_signal"].str.contains("Sticky", na=False).sum()
    promising_count = df["content_signal"].str.contains("Promising", na=False).sum()
    repurpose_count = (
        filtered_repurpose_count
        if filtered_repurpose_count is not None
        else df["content_signal"].str.contains("Repurpose Candidate", na=False).sum()
    )

    # Per-platform average engagement for benchmark indicators (#8)
    plat_avg_eng = (
        df.groupby("platform")["engagement_rate_percent"].mean().to_dict()
    )

    # Build consolidated table rows — all posts sorted by views (#7)
    book_th   = "<th>Book</th>"   if enriched else ""
    pillar_th = "<th>Pillar</th>" if enriched else ""
    consolidated_rows = ""
    for _, row in df.sort_values("views", ascending=False).iterrows():
        plat    = safe_text(row.get("platform"))
        views   = safe_int(row.get("views"))
        eng     = safe_float(row.get("engagement_rate_percent"))
        watch_m = safe_float(row.get("estimated_minutes_watched"))
        avg_eng = plat_avg_eng.get(plat, 0)
        if avg_eng > 0:
            bench_cls  = "bench-above" if eng >= avg_eng else "bench-below"
            bench_html = (
                f'<span class="{bench_cls}" title="Platform avg: {round(avg_eng,1)}%">'
                f"{eng}%</span>"
            )
        else:
            bench_html = f"{eng}%"
        pub    = safe_text(row.get("published_at"))
        mtype  = safe_text(row.get("media_type"))
        cap    = safe_text(row.get("title_or_caption"))
        url    = safe_text(row.get("url"))
        likes  = safe_int(row.get("likes"))
        cmnts  = safe_int(row.get("comments"))
        shrs   = safe_int(row.get("shares"))
        sig    = safe_text(row.get("content_signal"))
        action = safe_text(row.get("recommended_action"))
        book_td   = (
            f'<td class="col-book">{safe_text(row.get("book_or_offer","—"))}</td>'
            if enriched else ""
        )
        pillar_td = (
            f'<td class="col-pillar">{safe_text(row.get("content_pillar","—"))}</td>'
            if enriched else ""
        )
        consolidated_rows += f"""
        <tr data-platform="{plat}" data-views="{views}" data-eng="{eng}" data-watchmin="{watch_m}">
            <td style="white-space:nowrap">{PLATFORM_LABELS.get(plat, plat)}</td>
            {book_td}
            {pillar_td}
            <td>{pub}</td>
            <td style="font-size:11px">{mtype}</td>
            <td style="font-size:12px">{cap}</td>
            <td style="text-align:center"><strong>{views:,}</strong></td>
            <td style="text-align:center">{watch_m:g}</td>
            <td style="text-align:center">{likes:,}</td>
            <td style="text-align:center">{cmnts:,}</td>
            <td style="text-align:center">{shrs:,}</td>
            <td style="text-align:center">{bench_html}</td>
            <td style="font-size:12px">{sig}</td>
            <td style="font-size:12px">{action}</td>
            <td><a href="{url}" target="_blank">Open</a></td>
        </tr>"""

    # Platform filter options
    distinct_plats = sorted(df["platform"].dropna().unique())
    plat_options   = "".join(
        f'<option value="{p}">{PLATFORM_LABELS.get(p, p)}</option>'
        for p in distinct_plats
    )

    # Unmatched posts report — exclude FB-Image platforms (tracked via pm_posts/tpl_posts, not content table)
    _FB_IMAGE_PLATFORMS = {"FB-Image-PrehistoricMemories", "FB-Image-TheProtocolLab", "FB-Image-WillByron"}
    unmatched_df = (
        df[
            (df["book_or_offer"] == "—") &
            (~df["platform"].isin(_FB_IMAGE_PLATFORMS))
        ]
        if enriched else pd.DataFrame()
    )
    if not unmatched_df.empty:
        unmatched_rows = ""
        for _, r in unmatched_df.sort_values(["platform", "published_at"]).iterrows():
            u_plat = safe_text(r.get("platform"))
            u_pub  = safe_text(r.get("published_at"))
            u_cap  = safe_text(r.get("title_or_caption"))[:100]
            u_url  = safe_text(r.get("url"))
            unmatched_rows += f"""
            <tr>
                <td style="font-size:12px;white-space:nowrap">{PLATFORM_LABELS.get(u_plat, u_plat)}</td>
                <td style="font-size:12px">{u_pub}</td>
                <td style="font-size:12px">{u_cap}</td>
                <td><a href="{u_url}" target="_blank" style="color:#FFB347">Open</a></td>
            </tr>"""
        unmatched_section = f"""
        <div class="unmatched-report">
            <h2>Action Required: {len(unmatched_df)} Posts Not in Database</h2>
            <p style="color:#AAB4C0;font-size:13px;margin-top:0">
            These posts have no matching URL in wpp.db. Add their URLs to the content table
            to unlock book and pillar tracking.
            </p>
            <div class="table-wrap" style="margin-bottom:0">
                <table style="min-width:800px">
                    <thead>
                        <tr>
                            <th>Platform</th>
                            <th>Published</th>
                            <th>Caption</th>
                            <th>Link</th>
                        </tr>
                    </thead>
                    <tbody>{unmatched_rows}</tbody>
                </table>
            </div>
        </div>"""
    else:
        unmatched_section = ""

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
        /* Sort / filter bar */
        .sort-bar {{
            display: flex;
            gap: 10px;
            align-items: center;
            flex-wrap: wrap;
            margin-bottom: 16px;
        }}
        .sort-btn {{
            background: rgba(201,168,76,.12);
            border: 1px solid rgba(201,168,76,.3);
            color: #AAB4C0;
            font-size: 12px;
            padding: 5px 14px;
            border-radius: 20px;
            cursor: pointer;
            font-family: Arial, Helvetica, sans-serif;
            transition: all .15s;
        }}
        .sort-btn:hover {{ color: #D7DEE8; border-color: #C9A84C; }}
        .sort-btn.active {{ background: rgba(201,168,76,.25); color: #C9A84C; border-color: #C9A84C; }}
        .plat-select {{
            background: #101F36;
            border: 1px solid rgba(255,255,255,.2);
            color: #D7DEE8;
            font-size: 12px;
            padding: 5px 10px;
            border-radius: 6px;
            font-family: Arial, Helvetica, sans-serif;
        }}
        .bench-above {{ color: #5CFF7E; font-weight: bold; }}
        .bench-below {{ color: #FF6B6B; }}
        /* Unmatched report */
        .unmatched-report {{
            background: #101F36;
            border: 1px solid #FFB347;
            border-radius: 14px;
            padding: 18px;
            margin-bottom: 30px;
        }}
        .unmatched-report h2 {{ margin-top: 0; color: #FFB347; font-size: 16px; }}
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
        var _allRows = null;
        function _getRows() {{
            if (!_allRows) _allRows = Array.from(document.querySelectorAll('#mainTable tbody tr'));
            return _allRows;
        }}
        function filterTable() {{
            var plat = document.getElementById('platFilter').value;
            _getRows().forEach(function(r) {{
                r.style.display = (!plat || r.getAttribute('data-platform') === plat) ? '' : 'none';
            }});
        }}
        function sortTable(col, btn) {{
            document.querySelectorAll('.sort-btn').forEach(function(b) {{ b.classList.remove('active'); }});
            btn.classList.add('active');
            var visible = _getRows().filter(function(r) {{ return r.style.display !== 'none'; }});
            visible.sort(function(a, b) {{
                return parseFloat(b.getAttribute('data-' + col) || 0) - parseFloat(a.getAttribute('data-' + col) || 0);
            }});
            var tbody = document.querySelector('#mainTable tbody');
            visible.forEach(function(r) {{ tbody.appendChild(r); }});
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

        {instagram_performance_html}

        {x_performance_html}

        {facebook_performance_html}

        {fb_image_performance_html}

        {kdp_revenue_html}

        {unmatched_section}

        <h2>All Posts</h2>
        <div class="sort-bar">
            <label style="color:#AAB4C0;font-size:13px">Platform:
                <select id="platFilter" class="plat-select" onchange="filterTable()">
                    <option value="">All Platforms</option>
                    {plat_options}
                </select>
            </label>
            <span style="color:#AAB4C0;font-size:13px">Sort:</span>
            <button class="sort-btn active" onclick="sortTable('views', this)">Views</button>
            <button class="sort-btn" onclick="sortTable('eng', this)">Engagement</button>
            <button class="sort-btn" onclick="sortTable('watchmin', this)">Watch Time</button>
        </div>
        <div class="table-wrap">
            <table id="mainTable">
                <thead>
                    <tr>
                        <th>Platform</th>
                        {book_th}
                        {pillar_th}
                        <th>Published</th>
                        <th>Type</th>
                        <th>Title / Caption</th>
                        <th style="text-align:center">Views</th>
                        <th style="text-align:center">Watch Min</th>
                        <th style="text-align:center">Likes</th>
                        <th style="text-align:center">Comm</th>
                        <th style="text-align:center">Shares</th>
                        <th style="text-align:center">Eng %</th>
                        <th>Signal</th>
                        <th>Action</th>
                        <th>Link</th>
                    </tr>
                </thead>
                <tbody>{consolidated_rows}</tbody>
            </table>
        </div>

        <div class="note">
            <strong style="color:#5CFF7E">Green</strong> Eng % = above your platform average.
            <strong style="color:#FF6B6B">Red</strong> = below. Hover for the platform average.<br>
            Watch minutes are YouTube-only. Facebook Reel Plays are the main view metric.<br>
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
    content_map, queue_df, ep_queue = load_wpp_content()

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
    plan = generate_monday_plan(df, queue_df, ep_queue)
    monday_plan_html = build_monday_plan_html(plan)

    print("Generating AI briefing...")
    ai_briefing_html = generate_ai_briefing(df, plan)

    print("Fetching trend data...")
    trends_data = fetch_trends_data()
    trends_html = build_trends_html(trends_data, plan.get("pillar_engagement", {}))
    scoreboard_html = build_scoreboard_html(df)
    x_performance_html = build_x_performance_html(content_map)
    facebook_performance_html = build_facebook_performance_html(df)
    fb_image_performance_html = build_fb_image_performance_html(fb_image_rows)
    instagram_performance_html = build_instagram_performance_html(df)

    # Build KDP Revenue
    kdp_revenue_html = build_kdp_revenue_html()

    print("Fetching Gumroad data...")
    gumroad_data  = fetch_gumroad_data()
    gumroad_posts = load_gumroad_posts(df, normalize_url)

    # Build HTML dashboard
    build_html(
        df,
        monday_plan_html,
        scoreboard_html,
        x_performance_html=x_performance_html,
        facebook_performance_html=facebook_performance_html,
        fb_image_performance_html=fb_image_performance_html,
        instagram_performance_html=instagram_performance_html,
        kdp_revenue_html=kdp_revenue_html,
        filtered_repurpose_count=len(plan["repurpose"]),
        plan=plan,
        ai_briefing_html=ai_briefing_html,
        trends_html=trends_html,
        gumroad_data=gumroad_data,
        gumroad_posts=gumroad_posts,
    )


if __name__ == "__main__":
    main()
